"""Провайдер перевода через локальную языковую модель (OpenAI-совместимый API).

Чем он принципиально отличается от облачных
───────────────────────────────────────────
DeepL, Google и Yandex — переводчики: им дают список строк, они возвращают
список той же длины. Языковая модель — не переводчик, а исполнитель
инструкции. Она может ответить прозой, склеить два элемента в один,
переставить их местами, потерять управляющий код или обрезать JSON на
середине. Поэтому здесь:

* каждый элемент уходит со своим идентификатором, контекстом и метаданными
  (см. :mod:`core.llm_contract`);
* ответ обязан соответствовать строгой JSON Schema — без неё в замерах
  корректный JSON приходил в 13 случаях из 15, а точные идентификаторы — в 11;
* каждый перевод проверяется детерминированным валидатором ПОСЛЕ схемы: схема
  задаёт форму ответа, но не равенство токенов входа и выхода (со схемой
  сохранность плейсхолдеров была 14/15, без неё — 10/15);
* непрошедшие элементы уходят на один ремонтный запрос поодиночке и с другой
  формулировкой. Повторять тот же запрос бесполезно: при температуре 0 модель
  трижды из трёх выдала байт-в-байт тот же дефект.

Что провайдер НЕ делает
───────────────────────
Не запускает и не останавливает LM Studio, не загружает и не выгружает
модель. Сервер поднимает пользователь; провайдер только проверяет доступность
и внятно сообщает, если её нет.
"""
from __future__ import annotations

import json
import time
from dataclasses import replace
from typing import Any, Callable

from core.llm_contract import (
    CONTRACT_VERSION,
    ResponseError,
    TranslationItem,
    fingerprint,
    parse_response,
    response_schema,
    verify,
)
from core.translators import Translator, TranslationError

# ── Значения по умолчанию ───────────────────────────────────────────────────
# Взяты из протокола испытаний: профиль 4096 / GPU 0.8 / parallel 1.
DEFAULT_BASE_URL = "http://127.0.0.1:1234/v1"
DEFAULT_MODEL = "qwen3.5-4b-rpg-ru-safe"
DEFAULT_TIMEOUT = 180.0
DEFAULT_BATCH_SIZE = 5        # безопасный размер для 16 ГиБ ОЗУ
BATCH_CHOICES = (1, 5, 10, 20)  # 20 — экспериментальный: терял маркер

# Промпт B из сравнения трёх формулировок: 8/8 по JSON, идентификаторам и
# маркерам. Многословный «RPG-aware» вариант потерял маркер (7/8) — длина
# инструкции не улучшает дисциплину модели.
DEFAULT_SYSTEM_PROMPT = (
    "You are a professional English-to-Russian game localizer. Preserve "
    "meaning, tone, names, output IDs and all <tN/> tokens exactly. Do not "
    "merge, omit, add or reorder items. Use each item's context only for "
    "disambiguation. Reply only with "
    '{"translations":[{"id":...,"translation":...}]} and no Markdown.'
)

# Ремонтная инструкция: другая формулировка, один элемент, без контекста.
REPAIR_PROMPT = (
    "You are a strict repair pass. The previous translation of this single "
    "item was rejected by an automatic validator. Return the Russian "
    "translation again, obeying every rule literally:\n"
    "1. Every <tN/> token from the source must appear in the output, the same "
    "number of times, in the same order, and next to the same word or number "
    "it was attached to in the source.\n"
    "2. Every digit sequence in the source must appear unchanged.\n"
    "3. Output Russian only: no English left over, no explanation, no "
    "Markdown.\n"
    'Reply only with {"translations":[{"id":...,"translation":...}]}.'
)

_LANG_NAMES = {
    "en": "English", "ru": "Russian", "ja": "Japanese", "ko": "Korean",
    "zh": "Chinese", "de": "German", "fr": "French", "es": "Spanish",
}


def _is_loopback(base_url: str) -> bool:
    """Слушает ли адрес только эту машину.

    Локальная модель не должна ходить наружу: и потому что там её нет, и
    потому что в запросах едет текст игры.
    """
    from urllib.parse import urlparse
    host = (urlparse(base_url).hostname or "").lower()
    return host in ("127.0.0.1", "localhost", "::1", "0:0:0:0:0:0:0:1")


class LocalLLMTranslator(Translator):
    """Перевод через локальный OpenAI-совместимый сервер (LM Studio и др.)."""

    name = "Local LLM"
    # Флаг для translate_with_chain: провайдер умеет принимать элементы
    # целиком, а не только плоские строки с общим контекстом на пакет.
    wants_items = True

    def __init__(
        self,
        api_key: str = "",
        *,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
        system_prompt: str = "",
        temperature: float = 0.0,
        top_p: float = 0.8,
        top_k: int = 20,
        reasoning_effort: str = "none",
        max_tokens: int = 0,
        timeout: float = DEFAULT_TIMEOUT,
        use_json_schema: bool = True,
        repair_retries: int = 1,
        context_mode: str = "event_page_1_1",
        glossary_version: str = "",
        allow_remote: bool = False,
        session: Any = None,
    ):
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.model = (model or DEFAULT_MODEL).strip()
        self.api_key = (api_key or "").strip()
        self.system_prompt = (system_prompt or "").strip() or DEFAULT_SYSTEM_PROMPT
        self.temperature = float(temperature)
        self.top_p = float(top_p)
        self.top_k = int(top_k)
        self.reasoning_effort = reasoning_effort
        self.max_tokens = int(max_tokens or 0)
        self.timeout = float(timeout)
        self.use_json_schema = bool(use_json_schema)
        self.repair_retries = max(0, int(repair_retries))
        self.context_mode = context_mode
        self.glossary_version = glossary_version
        self._session = session

        if not self.model:
            raise TranslationError("Не указан идентификатор модели", self.name, False)
        if not _is_loopback(self.base_url) and not allow_remote:
            raise TranslationError(
                f"Адрес {self.base_url} не является локальным. Локальная модель "
                "должна слушать 127.0.0.1: в запросах едет текст игры, и "
                "выставлять порт в сеть нельзя. Разрешите нелокальный адрес "
                "явно, если это осознанное решение.",
                self.name, False,
            )

        # Счётчики за сеанс — их показывает отчёт после перевода.
        self.stats: dict[str, Any] = {
            "requests": 0, "items": 0, "accepted": 0,
            "repaired": 0, "failed": 0, "seconds": 0.0,
        }
        # Причины отказов: id → список проблем. Нужны для списка «на разбор».
        self.failures: list[tuple[str, str, list[str]]] = []

    # ── Отпечаток для кэша ──────────────────────────────────────────────────

    @property
    def cache_namespace(self) -> str:
        """Пространство имён кэша с отпечатком всех смысловых входов.

        Модель, промпт, параметры генерации, режим контекста и версия
        глоссария меняют результат. Если они изменились, прежний перевод — это
        ответ на другой вопрос, и переиспользовать его нельзя. Облачные ключи
        при этом не затрагиваются: у них своё пространство имён.
        """
        fp = fingerprint(
            contract=CONTRACT_VERSION,
            model=self.model,
            prompt=self.system_prompt,
            temperature=self.temperature,
            top_p=self.top_p,
            top_k=self.top_k,
            reasoning=self.reasoning_effort,
            schema=self.use_json_schema,
            context_mode=self.context_mode,
            glossary=self.glossary_version,
        )
        return f"local:{fp}"

    def supports(self, src: str, dst: str) -> bool:
        return True

    # ── Транспорт ───────────────────────────────────────────────────────────

    def _requests(self):
        import requests
        return requests

    def _post(self, path: str, payload: dict, timeout: float | None = None) -> dict:
        requests = self._requests()
        url = f"{self.base_url}{path}"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            http = self._session or requests
            r = http.post(url, json=payload, headers=headers,
                          timeout=timeout or self.timeout)
        except requests.exceptions.ConnectionError as e:
            raise TranslationError(
                f"Сервер модели не отвечает по адресу {self.base_url}. "
                "Запустите LM Studio и загрузите модель.",
                self.name, False,
            ) from e
        except requests.exceptions.Timeout as e:
            raise TranslationError(
                f"Модель не ответила за {timeout or self.timeout:.0f} с. "
                "Уменьшите размер пакета или увеличьте таймаут.",
                self.name, True,
            ) from e
        except requests.exceptions.RequestException as e:
            raise TranslationError(f"Ошибка запроса к модели: {e}", self.name, True) from e

        if r.status_code == 404:
            raise TranslationError(
                f"Сервер есть, но модель «{self.model}» не найдена. "
                "Проверьте идентификатор в /v1/models и загрузите модель.",
                self.name, False,
            )
        if r.status_code >= 500:
            raise TranslationError(
                f"Сервер модели вернул {r.status_code}. Обычно это выгруженная "
                "модель или нехватка памяти.", self.name, True,
            )
        if r.status_code != 200:
            raise TranslationError(
                f"HTTP {r.status_code}: {r.text[:200]}", self.name, False,
            )
        try:
            return r.json()
        except ValueError as e:
            raise TranslationError(
                "Ответ сервера не является JSON — вероятно, по этому адресу "
                "не OpenAI-совместимый API.", self.name, False,
            ) from e

    def _chat(self, system: str, user_payload: dict, ids: list[str],
              timeout: float | None = None) -> str:
        """Один запрос к модели. Возвращает содержимое ответа как строку."""
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user",
                 "content": json.dumps(user_payload, ensure_ascii=False)},
            ],
            "stream": False,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "top_k": self.top_k,
        }
        if self.reasoning_effort:
            # Рассуждения выключены намеренно: они не улучшают дисциплину
            # формата, но съедают бюджет вывода и приводят к finish_reason=length.
            body["reasoning_effort"] = self.reasoning_effort
        if self.max_tokens > 0:
            body["max_tokens"] = self.max_tokens
        if self.use_json_schema and ids:
            body["response_format"] = response_schema(ids)

        started = time.monotonic()
        data = self._post("/chat/completions", body, timeout=timeout)
        self.stats["requests"] += 1
        self.stats["seconds"] += time.monotonic() - started

        choices = data.get("choices") or []
        if not choices:
            raise TranslationError("Модель вернула пустой список choices",
                                   self.name, True)
        choice = choices[0] or {}
        finish = choice.get("finish_reason")
        message = choice.get("message") or {}
        content = message.get("content")
        if finish == "length":
            # Обрезанный ответ — это гарантированно битый JSON. Виноват не
            # текст, а слишком большой пакет: чинится уменьшением batch_size.
            raise TranslationError(
                "Ответ модели обрезан по лимиту токенов (finish_reason=length). "
                "Уменьшите размер пакета.", self.name, True,
            )
        if not isinstance(content, str):
            raise TranslationError("В ответе модели нет текстового content",
                                   self.name, True)
        return content

    # ── Основной путь: перевод элементов ────────────────────────────────────

    def translate_items(
        self,
        items: list[TranslationItem],
        src: str,
        dst: str,
        should_stop: Callable[[], bool] | None = None,
    ) -> list[str]:
        """Переводит элементы. Возвращает список той же длины.

        Пустая строка на позиции означает «перевод не принят»: исходник
        останется как есть, а следующий запуск попробует снова. Возвращать
        вместо этого исходный текст нельзя — он попал бы в кэш как готовый
        перевод и закрепил бы отказ навсегда.
        """
        if not items:
            return []
        self.stats["items"] += len(items)

        ids = [it.id for it in items]
        user_payload = {
            "task": f"Translate each item from {_LANG_NAMES.get(src, src)} "
                    f"to {_LANG_NAMES.get(dst, dst)}.",
            "items": [it.payload() for it in items],
        }
        content = self._chat(self.system_prompt, user_payload, ids)

        batch_problem = ""
        try:
            mapping = parse_response(content, ids)
        except ResponseError as e:
            # Ошибка уровня всего пакета: сопоставить нечего. Пакет
            # переспрашивается поэлементно — там, где ремонт разрешён.
            if self.repair_retries <= 0:
                raise TranslationError(
                    f"Ответ модели не соответствует контракту: {e}",
                    self.name, True,
                )
            # Причину надо запомнить: иначе в квитанции у всех элементов
            # пакета оказалось бы «элемента нет в ответе», и по отчёту было бы
            # не понять, что на самом деле сломался ответ целиком.
            batch_problem = f"ответ пакета отклонён ({e})"
            mapping = {}

        out: list[str] = []
        for item in items:
            if should_stop and should_stop():
                out.extend([""] * (len(items) - len(out)))
                break
            candidate = mapping.get(item.id, "")
            verdict = verify(item.id, item.text, candidate) if candidate else None
            if verdict is not None and verdict.ok:
                self.stats["accepted"] += 1
                out.append(candidate)
                continue

            if verdict is not None:
                problems = list(verdict.problems)
            else:
                problems = [batch_problem or "элемента нет в ответе"]
            repaired = self._repair(item, src, dst)
            if repaired:
                self.stats["accepted"] += 1
                self.stats["repaired"] += 1
                out.append(repaired)
            else:
                self.stats["failed"] += 1
                self.failures.append((item.id, item.text, problems))
                out.append("")
        return out

    def _repair(self, item: TranslationItem, src: str, dst: str) -> str:
        """Один ремонтный запрос по одному элементу. Пусто = не починили."""
        if self.repair_retries <= 0:
            return ""
        payload = {
            "task": f"Repair the {_LANG_NAMES.get(dst, dst)} translation of this "
                    f"single {_LANG_NAMES.get(src, src)} item.",
            "items": [item.payload(minimal=True)],
        }
        for _ in range(self.repair_retries):
            try:
                content = self._chat(REPAIR_PROMPT, payload, [item.id])
                mapping = parse_response(content, [item.id])
            except (TranslationError, ResponseError):
                # Ремонт — необязательная попытка. Её падение не должно
                # ронять весь пакет: остальные элементы уже переведены.
                return ""
            candidate = mapping.get(item.id, "")
            if candidate and verify(item.id, item.text, candidate).ok:
                return candidate
        return ""

    # ── Совместимость с плоским интерфейсом ─────────────────────────────────

    def translate_batch(
        self,
        texts: list[str],
        src: str,
        dst: str,
        context: str | None = None,
    ) -> list[str]:
        """Плоский путь: строки без метаданных.

        Нужен, чтобы провайдер работал везде, где вызывают старый интерфейс
        (предпросмотр, ручные проверки). Полноценный контекст здесь недоступен,
        поэтому основной путь — translate_items.
        """
        if not texts:
            return []
        before = [context.strip()] if (context or "").strip() else []
        items = [
            TranslationItem(id=f"item-{i}", text=t, context_before=before)
            for i, t in enumerate(texts)
        ]
        return self.translate_items(items, src, dst)

    # ── Проверка соединения ─────────────────────────────────────────────────

    def probe(self, timeout: float = 20.0) -> dict:
        """Диагностика для кнопки «Проверить соединение».

        Отвечает на три вопроса по отдельности, потому что лечатся они
        по-разному: сервер поднят? нужная модель загружена? модель отвечает
        в требуемом формате?
        """
        report: dict[str, Any] = {
            "base_url": self.base_url, "model": self.model,
            "loopback": _is_loopback(self.base_url),
            "reachable": False, "model_found": False,
            "responds": False, "models": [], "ok": False,
            "message": "", "seconds": 0.0,
        }
        requests = self._requests()
        started = time.monotonic()

        # 1. Сервер и список моделей.
        try:
            headers = {}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            http = self._session or requests
            r = http.get(f"{self.base_url}/models", headers=headers, timeout=timeout)
        except Exception as e:
            report["message"] = (
                f"Сервер не отвечает по адресу {self.base_url}: "
                f"{type(e).__name__}. Запустите LM Studio."
            )
            report["seconds"] = time.monotonic() - started
            return report

        if r.status_code != 200:
            report["message"] = f"Сервер ответил HTTP {r.status_code} на /models."
            report["seconds"] = time.monotonic() - started
            return report
        report["reachable"] = True

        try:
            listing = r.json().get("data") or []
            report["models"] = [m.get("id", "") for m in listing if isinstance(m, dict)]
        except ValueError:
            report["message"] = "Ответ /models не разбирается как JSON."
            report["seconds"] = time.monotonic() - started
            return report

        # Сверка ровно по идентификатору: похожее имя другой сборки даст
        # другой перевод и другой кэш.
        if self.model not in report["models"]:
            report["message"] = (
                f"Сервер работает, но модели «{self.model}» среди загруженных "
                f"нет. Доступны: {', '.join(report['models']) or '—'}."
            )
            report["seconds"] = time.monotonic() - started
            return report
        report["model_found"] = True

        # 2. Один безобидный элемент: проверяем формат ответа, ничего не меняя.
        probe_item = TranslationItem(id="probe-1", text="Good morning!",
                                     text_type="dialogue")
        try:
            content = self._chat(
                self.system_prompt,
                {"task": "Translate each item from English to Russian.",
                 "items": [probe_item.payload()]},
                ["probe-1"], timeout=timeout,
            )
            mapping = parse_response(content, ["probe-1"])
        except (TranslationError, ResponseError) as e:
            report["message"] = f"Модель загружена, но ответ не по контракту: {e}"
            report["seconds"] = time.monotonic() - started
            return report

        report["responds"] = True
        report["sample"] = mapping.get("probe-1", "")
        report["ok"] = True
        report["seconds"] = time.monotonic() - started
        report["message"] = (
            f"Готово. Модель «{self.model}» отвечает по контракту "
            f"({report['seconds']:.1f} с). Пример: {report['sample']!r}"
        )
        if not report["loopback"]:
            report["message"] += "  ВНИМАНИЕ: адрес не локальный."
        return report


def items_for_stage(items: list[TranslationItem], texts: list[str]) -> list[TranslationItem]:
    """Пересобирает элементы под текущую стадию цепочки.

    На второй стадии (например EN→RU после JP→EN) переводить надо уже
    результат первой, а не исходник. Метаданные при этом сохраняются.
    """
    return [replace(item, text=text) for item, text in zip(items, texts)]


def unit_items(units, glossary=None) -> list[TranslationItem]:
    """Строит элементы контракта из единиц перевода.

    Глоссарий передаётся подсказкой ТОЛЬКО для терминов, которые реально видны
    в тексте элемента. Термины, закрытые плейсхолдерами при защите кодов, до
    модели не доходят вовсе — подсказывать их незачем, а лишний словарь в
    запросе съедает контекст.
    """
    terms = dict(getattr(glossary, "terms", {}) or {}) if glossary else {}
    items: list[TranslationItem] = []
    for u in units:
        subset = {src: dst for src, dst in terms.items() if src and src in u.combined_text}
        items.append(TranslationItem(
            id=u.item_id or f"unit-{len(items)}",
            text=u.combined_text,
            context_before=list(u.context_before),
            context_after=list(u.context_after),
            text_type=u.text_type,
            speaker=u.speaker,
            location=dict(u.location),
            glossary=subset,
        ))
    return items
