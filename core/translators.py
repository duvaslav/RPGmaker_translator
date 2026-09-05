"""
Провайдеры перевода: DeepL, Google (deep-translator), Yandex.
Поддержка цепочки: JP → EN → RU и других маршрутов.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from core.cache import TranslationCache


def _keep_entities(text: str) -> str:
    """Ответ провайдера отдаётся дальше КАК ЕСТЬ, без расэкранирования.

    Раньше html-сущности расэкранировались дважды: здесь и в restore_codes.
    Из-за этого строка, где в игре реально лежит «&amp;», превращалась в «&».
    Теперь единственная точка расэкранирования — restore_codes, и она
    вызывается ровно один раз за жизнь строки.
    """
    return text or ""


@dataclass
class TranslationError(Exception):
    message: str
    provider: str = ""
    recoverable: bool = True
    too_large: bool = False  # True = запрос превысил лимит размера, нужно разбить батч
    retry_after: float = 0.0  # сколько ждать по требованию сервера (429)

    def __str__(self) -> str:
        return f"[{self.provider}] {self.message}"


def _interruptible_sleep(seconds: float,
                         should_stop: Callable[[], bool] | None = None) -> None:
    """Пауза, которую можно прервать кнопкой «Остановить»."""
    deadline = time.monotonic() + max(0.0, seconds)
    while time.monotonic() < deadline:
        if should_stop and should_stop():
            return
        time.sleep(min(0.25, deadline - time.monotonic()))


def _retry_after(response) -> float:
    """Читает заголовок Retry-After, если сервер его прислал."""
    try:
        value = response.headers.get("Retry-After", "")
    except Exception:
        return 0.0
    try:
        return min(120.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _placeholders_intact(source: str, translated: str) -> bool:
    """Все ли плейсхолдеры <tN/> дожили до перевода в единственном экземпляре."""
    from core.rpgmaker_parser import count_placeholders, validate_placeholders
    expected = count_placeholders(source)
    if not expected:
        # Кодов не было — но чужой плейсхолдер мог переехать из соседней строки.
        return not count_placeholders(translated)
    ok, _ = validate_placeholders(translated, max(expected) + 1)
    return ok


class Translator(ABC):
    """Абстрактный переводчик."""

    name: str = "base"

    @abstractmethod
    def translate_batch(
        self,
        texts: list[str],
        src: str,
        dst: str,
        context: str | None = None,
    ) -> list[str]:
        """Переводит список строк. Возвращает список той же длины."""
        ...

    def supports(self, src: str, dst: str) -> bool:
        return True


# ────────────────────────────────────────────────────────────────────────────
# DeepL
# ────────────────────────────────────────────────────────────────────────────

class DeepLTranslator(Translator):
    name = "DeepL"
    # DeepL коды языков
    LANG_MAP = {
        "ja": "JA", "en": "EN", "ru": "RU",
        "ko": "KO", "zh": "ZH",
        "de": "DE", "fr": "FR", "es": "ES",
    }

    def __init__(self, api_key: str, free_tier: bool | None = None):
        if not api_key:
            raise TranslationError("API-ключ DeepL не задан", self.name, False)
        self.api_key = api_key.strip()
        # Авто-определение endpoint по суффиксу ключа
        # Free ключи заканчиваются на ":fx", Pro — нет.
        # Если free_tier явно задан, используем его, иначе определяем по ключу.
        if free_tier is None:
            self.is_free = self.api_key.endswith(":fx")
        else:
            self.is_free = bool(free_tier)
        self.endpoint = (
            "https://api-free.deepl.com/v2/translate" if self.is_free
            else "https://api.deepl.com/v2/translate"
        )

    def translate_batch(
        self,
        texts: list[str],
        src: str,
        dst: str,
        context: str | None = None,
    ) -> list[str]:
        import requests
        if not texts:
            return []
        src_code = self.LANG_MAP.get(src.lower())
        dst_code = self.LANG_MAP.get(dst.lower())
        if not dst_code:
            raise TranslationError(f"DeepL не поддерживает язык {dst}", self.name, False)

        # Современный способ аутентификации DeepL — заголовок DeepL-Auth-Key.
        # Отправка ключа в теле как auth_key — устаревший формат, для новых ключей
        # часто возвращает 403 "неверный ключ".
        headers = {
            "Authorization": f"DeepL-Auth-Key {self.api_key}",
            "User-Agent": "rpgmaker-translator/1.0",
        }

        data = [("target_lang", dst_code)]
        if src_code:
            data.append(("source_lang", src_code))
        data.append(("preserve_formatting", "1"))
        if context:
            # DeepL использует context как подсказку для неоднозначных коротких
            # фраз, но не возвращает его в переводе. Это лучше, чем склеивать
            # соседние реплики специальным символом и потом пытаться разрезать
            # ответ переводчика обратно.
            data.append(("context", context[:5000]))
        # tag_handling=html заставляет DeepL сохранять наши плейсхолдеры <t0/>
        # нетронутыми. Используем именно html (не xml), потому что html-парсинг
        # нестрогий и прощает мелкие неидеальности тегов. Это ключевая защита
        # от порчи управляющих кодов при переводе.
        data.append(("tag_handling", "html"))
        for t in texts:
            data.append(("text", t))

        try:
            r = requests.post(self.endpoint, data=data, headers=headers, timeout=60)
        except requests.RequestException as e:
            raise TranslationError(f"Сетевая ошибка: {e}", self.name, True)

        # Полезный сценарий: пользователь поставил «Free» галку, но ключ Pro
        # (или наоборот). DeepL вернёт 403 с подсказкой "Wrong endpoint".
        if r.status_code == 403:
            body = r.text or ""
            if "Wrong endpoint" in body or "wrong endpoint" in body.lower():
                # Переключаемся на противоположный endpoint и пробуем один раз
                alt = ("https://api.deepl.com/v2/translate"
                       if "api-free" in self.endpoint
                       else "https://api-free.deepl.com/v2/translate")
                try:
                    r = requests.post(alt, data=data, headers=headers, timeout=60)
                    self.endpoint = alt  # запомним
                    self.is_free = "api-free" in alt
                except requests.RequestException as e:
                    raise TranslationError(f"Сетевая ошибка: {e}", self.name, True)
            else:
                # Самая частая реальная причина 403 — Pro/Free путаница или
                # ключ от обычного DeepL Pro (подписка), а не от DeepL API.
                hint = (
                    "Авторизация DeepL не прошла (403). Проверь:\n"
                    "• Ключ — именно от DeepL API (не от обычной подписки DeepL Pro)\n"
                    "• Free-ключи оканчиваются на «:fx», Pro — нет\n"
                    "• Скопирован полностью, без пробелов\n"
                    f"Ответ сервера: {body[:200]}"
                )
                raise TranslationError(hint, self.name, False)

        if r.status_code == 456:
            raise TranslationError(
                "Превышен месячный лимит символов DeepL (HTTP 456). "
                "Free даёт 500 000 символов в месяц.",
                self.name, False,
            )
        if r.status_code == 429:
            raise TranslationError("Rate limit DeepL — подожди немного", self.name, True,
                                   retry_after=_retry_after(r))
        if r.status_code != 200:
            raise TranslationError(
                f"DeepL ответил {r.status_code}: {r.text[:200]}", self.name, True
            )

        payload = r.json()
        translations = payload.get("translations", [])
        if len(translations) != len(texts):
            raise TranslationError(
                f"DeepL вернул {len(translations)} переводов вместо {len(texts)}",
                self.name, True,
            )
        # html-режим экранирует спецсимволы — декодируем обратно
        return [_keep_entities(t.get("text", "")) for t in translations]


# ────────────────────────────────────────────────────────────────────────────
# Google (через deep-translator, без ключа)
# ────────────────────────────────────────────────────────────────────────────

class GoogleTranslator_(Translator):
    name = "Google"

    def __init__(self, api_key: str = ""):
        # ключ не нужен, но параметр оставлен для единообразия
        import importlib.util
        if importlib.util.find_spec("deep_translator") is None:
            raise TranslationError(
                "Установи пакет: pip install deep-translator",
                self.name, False,
            )

    def translate_batch(
        self,
        texts: list[str],
        src: str,
        dst: str,
        context: str | None = None,
    ) -> list[str]:
        from deep_translator import GoogleTranslator
        if not texts:
            return []
        # deep-translator принимает 'auto', 'ja', 'en', 'ru'
        gt = GoogleTranslator(source=src, target=dst)
        results: list[str] = []
        # Google неофициально лимитит длинные батчи. Делаем по одному, но быстро.
        # translate_batch есть в deep-translator, но он внутри тоже шлёт по одному.
        try:
            results = gt.translate_batch(texts)
        except Exception as e:
            raise TranslationError(f"Ошибка Google: {e}", self.name, True)
        # На некоторых строках может вернуться None
        results = [r if isinstance(r, str) else "" for r in results]
        if len(results) != len(texts):
            raise TranslationError(
                f"Google вернул {len(results)} переводов вместо {len(texts)}",
                self.name, True,
            )
        return results


# ────────────────────────────────────────────────────────────────────────────
# Yandex
# ────────────────────────────────────────────────────────────────────────────

class YandexTranslator(Translator):
    name = "Yandex"
    ENDPOINT = "https://translate.api.cloud.yandex.net/translate/v2/translate"
    DETECT_ENDPOINT = "https://translate.api.cloud.yandex.net/translate/v2/detect"

    def __init__(self, api_key: str, folder_id: str = ""):
        if not api_key:
            raise TranslationError("API-ключ Yandex не задан", self.name, False)
        # Принимаем три формата ввода ключа:
        #   1) "<api_key>" + folder_id отдельным параметром
        #   2) "<api_key>|<folder_id>" (legacy-формат для совместимости)
        #   3) пустой folder_id — но это даст ошибку при первом запросе
        if "|" in api_key and not folder_id:
            parts = api_key.split("|", 1)
            self.api_key = parts[0].strip()
            self.folder_id = parts[1].strip()
        else:
            self.api_key = api_key.strip()
            self.folder_id = folder_id.strip()

        # folder_id обязателен для API-ключа сервисного аккаунта (это самый
        # распространённый способ авторизации). Без него Yandex вернёт 400.
        if not self.folder_id:
            raise TranslationError(
                "Не указан folder_id для Yandex. "
                "Получить: console.cloud.yandex.ru → твой каталог → ID в правом верхнем углу.",
                self.name, False,
            )

    def _headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Api-Key {self.api_key}",
        }

    def _handle_status(self, r) -> None:
        """Общая обработка кодов ответа."""
        if r.status_code == 401:
            raise TranslationError(
                "Неверный ключ Yandex (HTTP 401). "
                "Проверь, что ключ скопирован полностью.",
                self.name, False,
            )
        if r.status_code == 403:
            raise TranslationError(
                "Нет доступа (HTTP 403). Проверь:\n"
                "• folder_id корректный\n"
                "• Ключу присвоена роль ai.translate.user в этом каталоге\n"
                "• Биллинг подключён",
                self.name, False,
            )
        if r.status_code == 400:
            body = (r.text or "").lower()
            # Yandex возвращает 400 если тело запроса/текст слишком большие.
            # Лимит тела POST — 30 KB, плюс есть лимит на суммарную длину текстов.
            # Признаки: "too long", "too large", "limit", "exceeded", "maximum".
            if any(kw in body for kw in (
                "too long", "too large", "limit", "exceed", "maximum",
                "request entity", "size", "длин", "превы", "слишком",
            )):
                raise TranslationError(
                    f"Запрос Yandex слишком большой: {r.text[:200]}",
                    self.name, recoverable=True, too_large=True,
                )
            raise TranslationError(
                f"Yandex отверг запрос (HTTP 400): {r.text[:300]}",
                self.name, False,
            )
        if r.status_code == 413:
            # Payload Too Large — однозначный признак переполнения
            raise TranslationError(
                "Запрос Yandex слишком большой (HTTP 413)",
                self.name, recoverable=True, too_large=True,
            )
        if r.status_code == 429:
            raise TranslationError("Rate limit Yandex", self.name, True,
                                   retry_after=_retry_after(r))
        if r.status_code != 200:
            raise TranslationError(
                f"Yandex ответил {r.status_code}: {r.text[:200]}",
                self.name, True,
            )

    # Лимиты Yandex: тело POST до 30 KB; на практике безопасный порог суммы
    # символов текстов ниже. Если суммарный размер батча превышает порог —
    # делим его, не дожидаясь ошибки от сервера.
    SAFE_CHARS_PER_REQUEST = 9000   # порог превентивной разбивки
    MIN_SPLIT_CHARS = 5000          # до какого размера дробим при ошибке

    def translate_batch(
        self,
        texts: list[str],
        src: str,
        dst: str,
        context: str | None = None,
    ) -> list[str]:
        if not texts:
            return []
        # Превентивная разбивка: если суммарная длина великовата, сразу делим
        total_chars = sum(len(t) for t in texts)
        if total_chars > self.SAFE_CHARS_PER_REQUEST and len(texts) > 1:
            return self._translate_split(texts, src, dst)

        try:
            return self._translate_raw(texts, src, dst)
        except TranslationError as e:
            if e.too_large:
                # Сервер сказал «слишком большой» — дробим
                if len(texts) > 1:
                    return self._translate_split(texts, src, dst)
                else:
                    # Один текст и он сам по себе огромный — режем по предложениям
                    return [self._translate_huge_single(texts[0], src, dst)]
            raise

    def _translate_raw(self, texts: list[str], src: str, dst: str) -> list[str]:
        """Один реальный запрос к API без разбивки."""
        import requests
        body = {
            "folderId": self.folder_id,
            "targetLanguageCode": dst,
            "texts": texts,
            # format=HTML заставляет Yandex сохранять наши плейсхолдеры <t0/>.
            # Это защита управляющих кодов от искажения при переводе.
            "format": "HTML",
        }
        if src and src != "auto":
            body["sourceLanguageCode"] = src

        try:
            r = requests.post(self.ENDPOINT, json=body, headers=self._headers(), timeout=60)
        except requests.RequestException as e:
            raise TranslationError(f"Сетевая ошибка: {e}", self.name, True)

        self._handle_status(r)

        translations = r.json().get("translations", [])
        if len(translations) != len(texts):
            raise TranslationError(
                f"Yandex вернул {len(translations)} переводов вместо {len(texts)}",
                self.name, True,
            )
        # format=HTML экранирует спецсимволы — декодируем обратно
        return [_keep_entities(t.get("text", "")) for t in translations]

    def _translate_split(self, texts: list[str], src: str, dst: str) -> list[str]:
        """Рекурсивно делит батч пополам, пока куски не станут достаточно мелкими.
        Сохраняет порядок результатов."""
        # Если кусок уже из одного текста — дальше делить по списку нельзя
        if len(texts) == 1:
            try:
                return self._translate_raw(texts, src, dst)
            except TranslationError as e:
                if e.too_large:
                    return [self._translate_huge_single(texts[0], src, dst)]
                raise

        # Делим пополам
        mid = len(texts) // 2
        left = texts[:mid]
        right = texts[mid:]

        # Каждую половину: если она всё ещё крупная — рекурсивно, иначе напрямую
        def process(chunk: list[str]) -> list[str]:
            chunk_chars = sum(len(t) for t in chunk)
            if chunk_chars > self.MIN_SPLIT_CHARS and len(chunk) > 1:
                return self._translate_split(chunk, src, dst)
            try:
                return self._translate_raw(chunk, src, dst)
            except TranslationError as e:
                if e.too_large and len(chunk) > 1:
                    return self._translate_split(chunk, src, dst)
                if e.too_large:
                    return [self._translate_huge_single(chunk[0], src, dst)]
                raise

        return process(left) + process(right)

    def _translate_huge_single(self, text: str, src: str, dst: str) -> str:
        """Переводит одну строку, которая сама по себе превышает лимит.
        Режем по предложениям/переносам, переводим частями, склеиваем.
        Это крайний случай — обычно строки RPG Maker короткие."""

        # Точки разреза: переносы строк, потом японские/латинские концы предложений
        # Стараемся не резать внутри плейсхолдера <tN/>.
        chunk_limit = self.MIN_SPLIT_CHARS
        # Сначала пробуем резать по переносам строк (в RPG Maker встречаются \n)
        parts = text.split("\n")
        # Собираем куски, не превышающие лимит
        chunks: list[str] = []
        current = ""
        for part in parts:
            candidate = (current + "\n" + part) if current else part
            if len(candidate) > chunk_limit and current:
                chunks.append(current)
                current = part
            else:
                current = candidate
        if current:
            chunks.append(current)

        # Если какой-то кусок всё ещё огромный (одна строка без переносов) —
        # режем грубо по символам на границах пробелов
        final_chunks: list[str] = []
        for ch in chunks:
            if len(ch) <= chunk_limit:
                final_chunks.append(ch)
                continue
            # Грубая нарезка по словам
            words = ch.split(" ")
            buf = ""
            for w in words:
                cand = (buf + " " + w) if buf else w
                if len(cand) > chunk_limit and buf:
                    final_chunks.append(buf)
                    buf = w
                else:
                    buf = cand
            if buf:
                final_chunks.append(buf)

        # Переводим каждый кусок отдельным запросом и склеиваем через \n
        translated_parts = []
        for ch in final_chunks:
            res = self._translate_raw([ch], src, dst)
            translated_parts.append(res[0] if res else "")
        return "\n".join(translated_parts)

    def detect(self, text: str) -> str | None:
        """Опциональный метод: определить язык строки через Yandex API.
        Сейчас не используется (есть локальный детектор), но оставлено на будущее."""
        import requests
        body = {"folderId": self.folder_id, "text": text}
        try:
            r = requests.post(self.DETECT_ENDPOINT, json=body,
                              headers=self._headers(), timeout=30)
        except requests.RequestException:
            return None
        if r.status_code != 200:
            return None
        return r.json().get("languageCode")


# ────────────────────────────────────────────────────────────────────────────
# Фабрика
# ────────────────────────────────────────────────────────────────────────────

PROVIDERS: dict[str, type[Translator]] = {
    "DeepL": DeepLTranslator,
    "Google": GoogleTranslator_,
    "Yandex": YandexTranslator,
}


def make_translator(provider: str, api_key: str, **kwargs) -> Translator:
    cls = PROVIDERS.get(provider)
    if not cls:
        raise TranslationError(f"Неизвестный провайдер: {provider}")
    return cls(api_key=api_key, **kwargs)


# ────────────────────────────────────────────────────────────────────────────
# Цепочка перевода (pivot)
# ────────────────────────────────────────────────────────────────────────────

@dataclass
class TranslationRoute:
    """Маршрут перевода: одна или две стадии."""
    src: str
    pivot: str | None   # None = прямой
    dst: str

    @property
    def is_pivot(self) -> bool:
        return self.pivot is not None and self.pivot not in (self.src, self.dst)

    def stages(self) -> list[tuple[str, str]]:
        if self.is_pivot:
            return [(self.src, self.pivot), (self.pivot, self.dst)]
        return [(self.src, self.dst)]


@dataclass
class ChainConfig:
    """Конфигурация цепочки: для каждой стадии — свой провайдер.

    stage_providers — список кортежей:
        (provider_name, api_key)  — простой формат
        (provider_name, api_key, {extra kwargs}) — с доп. параметрами (например, folder_id для Yandex)
    """
    route: TranslationRoute
    stage_providers: list[tuple]

    def validate(self) -> None:
        if len(self.stage_providers) != len(self.route.stages()):
            raise ValueError(
                f"Нужно {len(self.route.stages())} провайдеров, "
                f"передано {len(self.stage_providers)}"
            )

    def get_stage(self, idx: int) -> tuple[str, str, dict]:
        """Возвращает (provider_name, api_key, extra_kwargs) для стадии."""
        item = self.stage_providers[idx]
        if len(item) == 2:
            return item[0], item[1], {}
        return item[0], item[1], item[2] if isinstance(item[2], dict) else {}


def translate_with_chain(
    texts: list[str],
    cfg: ChainConfig,
    batch_size: int = 40,
    contexts: list[str] | None = None,
    progress_cb: Callable[[int, int, str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    wait_if_paused: Callable[[], None] | None = None,
    cache: "TranslationCache | None" = None,
    save_cache_every: int = 1,
    stats: dict | None = None,
) -> list[str]:
    """
    Прогоняет тексты через все стадии цепочки.
    progress_cb(done, total, stage_label)
    should_stop() — вернёт True если нужно немедленно прервать
    wait_if_paused() — блокирующий вызов, который вернётся когда пауза снята
    cache — постоянный кэш переводов; если задан, кэш проверяется ДО отправки
            батча в API и обновляется ПОСЛЕ каждого батча с атомарной записью.

    При прерывании (stop или исключение):
    - Уже переведённые строки сохранены в кэше на диске
    - Возвращается частичный список: переведённые там где успели, исходные там где нет
    - Повторный запуск подхватит кэш и продолжит с того же места.
    """
    cfg.validate()
    current = list(texts)
    current_contexts = list(contexts) if contexts is not None else [""] * len(current)
    stages = cfg.route.stages()
    rejected = 0
    stage_count = len(stages)

    for stage_idx, (src, dst) in enumerate(stages):
        provider_name, api_key, extra_kwargs = cfg.get_stage(stage_idx)
        translator = make_translator(provider_name, api_key, **extra_kwargs)
        # Кэш привязывается к провайдеру ИМЕННО ЭТОЙ стадии: в цепочке
        # JP→EN Google, EN→RU DeepL это два независимых набора переводов.
        stage_cache = cache.for_provider(provider_name) if cache is not None else None
        stage_label = f"{src.upper()}→{dst.upper()} ({provider_name})"
        total = len(current)
        done = 0
        translated: list[str] = []
        translated_contexts: list[str] = []
        batches_since_save = 0

        i = 0
        while i < len(current):
            # Сначала ждём, если стоит пауза
            if wait_if_paused:
                wait_if_paused()
            if should_stop and should_stop():
                # Сохраняем кэш и дополняем оставшиеся исходными
                if stage_cache is not None:
                    stage_cache.save()
                translated.extend(current[i:])
                translated_contexts.extend(current_contexts[i:])
                current = translated
                current_contexts = translated_contexts
                break

            batch = current[i:i + batch_size]
            batch_contexts = current_contexts[i:i + batch_size]

            # Проверяем кэш: какие из batch уже переведены ранее
            to_translate_idx: list[int] = []   # индексы в batch, требующие API
            cached_results: dict[int, str] = {}
            for k, t in enumerate(batch):
                if not t.strip():
                    cached_results[k] = ""
                    continue
                if stage_cache is not None:
                    cached = stage_cache.get(src, dst, t)
                    if cached is not None:
                        cached_results[k] = cached
                        continue
                to_translate_idx.append(k)

            # Шлём в API только те, что не в кэше
            if to_translate_idx:
                api_texts = [batch[k] for k in to_translate_idx]
                api_context = _merge_contexts(
                    [batch_contexts[k] for k in to_translate_idx]
                )
                attempts = 0
                while True:
                    try:
                        api_results = translator.translate_batch(
                            api_texts, src, dst, context=api_context
                        )
                        break
                    except TranslationError as e:
                        attempts += 1
                        if not e.recoverable or attempts >= 3:
                            # Перед выходом сохраним то, что успели в кэш
                            if stage_cache is not None:
                                stage_cache.save()
                            raise
                        # Пауза между попытками не должна делать приложение
                        # глухим: спим короткими шагами и проверяем «Стоп».
                        delay = e.retry_after or float(2 ** attempts)
                        _interruptible_sleep(delay, should_stop)
                        if should_stop and should_stop():
                            if stage_cache is not None:
                                stage_cache.save()
                            raise

                # Проверяем целостность плейсхолдеров ДО кэширования.
                # Битый ответ, попавший в кэш, оставался там навсегда: при
                # следующем запуске строка бралась из кэша, снова не проходила
                # валидацию и уже никогда не переводилась заново.
                good_pairs: list[tuple[str, str]] = []
                for kk, source_text, val in zip(to_translate_idx, api_texts, api_results):
                    if _placeholders_intact(source_text, val):
                        cached_results[kk] = val
                        good_pairs.append((source_text, val))
                    else:
                        # Пустой результат = «не переведено»: строка останется
                        # исходной, а следующий запуск попробует её снова.
                        cached_results[kk] = ""
                        rejected += 1
                if stage_cache is not None and good_pairs:
                    stage_cache.set_many(src, dst, good_pairs)

            # Собираем итоговый батч в правильном порядке
            full = [cached_results.get(k, "") for k in range(len(batch))]
            translated.extend(full)
            translated_contexts.extend(batch_contexts)

            done += len(batch)
            if progress_cb:
                # Прогресс сквозной по всей цепочке: раньше при маршруте
                # JP→EN→RU полоса доходила до 100 % и обнулялась.
                progress_cb(stage_idx * total + done, total * stage_count, stage_label)

            # Периодическое сохранение кэша
            batches_since_save += 1
            if stage_cache is not None and batches_since_save >= save_cache_every:
                stage_cache.save()
                batches_since_save = 0

            i += batch_size
        else:
            # Цикл while закончился штатно (без break) — финальное сохранение
            if stage_cache is not None:
                stage_cache.save()

        # Если break сработал (остановка) — выходим из всех стадий
        if should_stop and should_stop():
            break

        current = translated
        current_contexts = translated_contexts

    if rejected and stats is not None:
        stats["rejected_placeholders"] = rejected
    return current


def _merge_contexts(contexts: list[str], limit: int = 5000) -> str:
    """Собирает контекст для одного API-батча.

    DeepL принимает один context на весь запрос, а не отдельный для каждого
    text. Батчи идут в порядке файлов, поэтому соседние units обычно относятся
    к одной сцене; объединённый контекст даёт переводчику имена и соседние
    реплики без необходимости склеивать переводимые строки разделителями.
    """
    if not contexts:
        return ""
    parts: list[str] = []
    seen: set[str] = set()
    total = 0
    for ctx in contexts:
        ctx = (ctx or "").strip()
        if not ctx or ctx in seen:
            continue
        seen.add(ctx)
        if total + len(ctx) + 2 > limit:
            break
        parts.append(ctx)
        total += len(ctx) + 2
    return "\n\n".join(parts)
