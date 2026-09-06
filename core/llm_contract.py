"""Контракт обмена с языковой моделью: элементы, схема ответа, валидаторы.

Зачем отдельный контракт
────────────────────────
Облачные сервисы (DeepL, Yandex, Google) принимают список строк и возвращают
список строк той же длины — сопоставление идёт по позиции. Языковая модель так
работать не может: она свободно переставляет, склеивает и теряет элементы.

Поэтому для неё контракт другой:

* каждый элемент несёт **свой** идентификатор, текст, контекст и метаданные;
* ответ разбирается **по идентификатору**, а не по позиции;
* ответ проверяется построчно, и в кэш попадает только то, что прошло проверку.

Почему проверка обязательна
───────────────────────────
В замерах на Qwen3.5-4B сохранность плейсхолдеров составила 80.7 % без строгой
схемы и 93.3 % с ней. Схема задаёт форму ответа, но не равенство токенов входа
и выхода — значит, инструкции модели в принципе не могут быть механизмом
безопасности. Им может быть только детерминированный валидатор.

Проверки соответствуют задокументированным отказам модели:

===========  ==========================================  ======================
Отказ        Что случилось                               Что ловит
===========  ==========================================  ======================
F-001        ``<t0/>Need something?`` → маркер исчез     ``markers``
F-003        ``700<t0/>`` → ``<t0/>700``                 ``marker_anchor``
F-004        ``4000<t0/>`` → число пропало                ``numbers``
F-009        сломанный или обрезанный JSON                ``parse``/``schema``
F-010        схема прошла, маркер потерян                 ``markers``
===========  ==========================================  ======================
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

# Плейсхолдер защищённого кода: тот же формат, что и в rpgmaker_parser.
_MARKER = re.compile(r'<\s*t\s*(\d+)\s*/?\s*>', re.IGNORECASE)
# Число внутри текста: важно и как содержимое («получено 4000»), и как якорь.
_NUMBER = re.compile(r'\d+')
# Управляющий код RPG Maker в сыром виде. В переводе его быть не должно:
# все коды до отправки заменены плейсхолдерами.
_RAW_CODE = re.compile(r'\\[A-Za-z][A-Za-z0-9]*\s*[\[<]|\\[A-Za-z](?![A-Za-z])')
# Признаки того, что модель ответила не переводом, а разговором о переводе.
_FENCE = re.compile(r'```|~~~')
_COMMENTARY = re.compile(
    r'^\s*(?:перевод|translation|here(?:\'s| is)|note|примечание)\s*[:\-—]',
    re.IGNORECASE,
)

CONTRACT_VERSION = "1"


# ────────────────────────────────────────────────────────────────────────────
# Элемент запроса
# ────────────────────────────────────────────────────────────────────────────

@dataclass
class TranslationItem:
    """Одна единица перевода со всем, что нужно модели для решения.

    Контекст лежит ВНУТРИ элемента, а не в общей строке на весь пакет. Это
    принципиально: пакет может содержать реплики из разных сцен, и подсказка от
    соседнего элемента увела бы перевод в сторону.
    """

    id: str
    text: str = ""
    context_before: list[str] = field(default_factory=list)
    context_after: list[str] = field(default_factory=list)
    text_type: str = "other"
    speaker: str = ""
    location: dict[str, Any] = field(default_factory=dict)
    glossary: dict[str, str] = field(default_factory=dict)

    @property
    def protected_tokens(self) -> list[str]:
        """Плейсхолдеры в порядке появления — модель обязана сохранить их все."""
        return [m.group(0) for m in _MARKER.finditer(self.text)]

    def payload(self, text: str | None = None, *, minimal: bool = False) -> dict:
        """Тело элемента для отправки.

        ``minimal=True`` используется в ремонтном запросе: контекст убирается,
        остаётся голая задача. Повторять тот же запрос бессмысленно — при
        температуре 0 модель трижды из трёх выдала один и тот же дефект.
        """
        body: dict[str, Any] = {
            "id": self.id,
            "text": self.text if text is None else text,
            "text_type": self.text_type,
        }
        tokens = [m.group(0) for m in _MARKER.finditer(body["text"])]
        if tokens:
            body["protected_tokens"] = tokens
        if self.speaker:
            body["speaker"] = self.speaker
        if self.glossary:
            body["glossary"] = dict(self.glossary)
        if minimal:
            return body
        if self.context_before:
            body["context_before"] = list(self.context_before)
        if self.context_after:
            body["context_after"] = list(self.context_after)
        if self.location:
            body["location"] = dict(self.location)
        return body


def make_item_id(file: str, path: Iterable[Any], block_start: int | None = None) -> str:
    """Стабильный идентификатор элемента.

    Складывается из настоящего положения в исходнике, а не из номера в массиве:
    позиция в пакете меняется от запуска к запуску, а путь в JSON — нет.
    """
    tail = "/".join(str(p) for p in path)
    ident = f"{file}:{tail}"
    return ident if block_start is None else f"{ident}#{block_start}"


# ────────────────────────────────────────────────────────────────────────────
# Схема ответа
# ────────────────────────────────────────────────────────────────────────────

def response_schema(ids: list[str]) -> dict:
    """Строгая JSON Schema: ровно по объекту на каждый отправленный элемент.

    Перечисление идентификаторов и точная длина массива не дают модели
    придумать запись, потерять её или ответить одним объектом с повторяющимися
    ключами ``id`` — всё это наблюдалось без схемы.
    """
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "rpgmaker_translations",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "translations": {
                        "type": "array",
                        "minItems": len(ids),
                        "maxItems": len(ids),
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string", "enum": list(ids)},
                                "translation": {"type": "string"},
                            },
                            "required": ["id", "translation"],
                            "additionalProperties": False,
                        },
                    }
                },
                "required": ["translations"],
                "additionalProperties": False,
            },
        },
    }


# ────────────────────────────────────────────────────────────────────────────
# Разбор ответа
# ────────────────────────────────────────────────────────────────────────────

class ResponseError(Exception):
    """Ответ невозможно разобрать как контракт целиком."""


def parse_response(content: str, expected_ids: list[str]) -> dict[str, str]:
    """Достаёт из ответа отображение id → перевод.

    Ошибки уровня всего ответа (не JSON, нет массива, лишние или потерянные
    идентификаторы, дубликаты) поднимаются исключением: чинить нечего, пакет
    надо переспрашивать целиком.
    """
    text = (content or "").strip()
    if not text:
        raise ResponseError("пустой ответ модели")
    if _FENCE.search(text):
        # Модель обернула JSON в ```json … ``` — вытаскиваем содержимое.
        stripped = re.sub(r'^\s*```[a-zA-Z]*\s*|\s*```\s*$', '', text).strip()
        text = stripped or text
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ResponseError(f"ответ не разбирается как JSON: {exc}") from exc

    rows = data.get("translations") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise ResponseError("в ответе нет массива translations")

    result: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ResponseError("элемент translations не является объектом")
        ident = row.get("id")
        value = row.get("translation")
        if not isinstance(ident, str) or not isinstance(value, str):
            raise ResponseError("у элемента нет строковых id/translation")
        if ident in result:
            raise ResponseError(f"идентификатор повторяется: {ident}")
        result[ident] = value

    expected = set(expected_ids)
    missing = expected - set(result)
    unexpected = set(result) - expected
    if missing or unexpected:
        raise ResponseError(
            f"состав ответа не совпал: нет {sorted(missing)[:3]}, "
            f"лишние {sorted(unexpected)[:3]}"
        )
    return result


# ────────────────────────────────────────────────────────────────────────────
# Проверка одного перевода
# ────────────────────────────────────────────────────────────────────────────

@dataclass
class ItemVerdict:
    """Машиночитаемая квитанция по одному элементу."""

    id: str
    ok: bool
    problems: list[str] = field(default_factory=list)
    translation: str = ""

    def __bool__(self) -> bool:
        return self.ok


def verify(item_id: str, source: str, translation: str, *,
           allow_unchanged: bool = False) -> ItemVerdict:
    """Проверяет перевод одного элемента. Любое нарушение — жёсткий отказ.

    Порядок проверок — от самых частых отказов к редким, чтобы в квитанции
    первым стояло главное.
    """
    problems: list[str] = []
    text = translation or ""

    if not text.strip():
        problems.append("пустой перевод")
        return ItemVerdict(item_id, False, problems, text)

    if _FENCE.search(text):
        problems.append("в ответе разметка Markdown")
    if _COMMENTARY.match(text):
        problems.append("ответ начинается с пояснения, а не с перевода")

    # ── Плейсхолдеры: состав, кратность и порядок ───────────────────────────
    src_markers = [m.group(0) for m in _MARKER.finditer(source)]
    out_markers = [m.group(0) for m in _MARKER.finditer(text)]
    if _canonical(src_markers) != _canonical(out_markers):
        problems.append(
            f"плейсхолдеры не совпадают: было {_canonical(src_markers)}, "
            f"стало {_canonical(out_markers)}"
        )
    else:
        # ── Позиция маркера относительно числа ──────────────────────────────
        # «700<t0/>» не должно превращаться в «<t0/>700»: код валюты обязан
        # остаться после суммы, иначе в игре получится «¥700» вместо «700¥».
        for anchor in _marker_anchors(source):
            if anchor not in _marker_anchors(text):
                problems.append(
                    f"плейсхолдер {anchor[0]} сместился относительно числа"
                )
                break

    # ── Числа: должны дожить до перевода ────────────────────────────────────
    src_numbers = sorted(_NUMBER.findall(_strip_markers(source)))
    out_numbers = sorted(_NUMBER.findall(_strip_markers(text)))
    if src_numbers and src_numbers != out_numbers:
        lost = sorted(set(src_numbers) - set(out_numbers))
        if lost:
            problems.append(f"пропали числа: {lost}")

    # ── Сырые коды RPG Maker, которых не было в источнике ───────────────────
    if _RAW_CODE.search(_strip_markers(text)) and not _RAW_CODE.search(_strip_markers(source)):
        problems.append("в переводе появился управляющий код, которого не было")

    # ── Перевод не сделан ───────────────────────────────────────────────────
    if not allow_unchanged and _comparable(text) == _comparable(source):
        problems.append("текст не переведён")

    return ItemVerdict(item_id, not problems, problems, text)


def _canonical(markers: list[str]) -> list[str]:
    """Нормализует запись плейсхолдеров: <T0 /> и <t0/> — один и тот же токен."""
    out = []
    for marker in markers:
        m = _MARKER.match(marker)
        out.append(f"<t{int(m.group(1))}/>" if m else marker)
    return out


def _strip_markers(text: str) -> str:
    return _MARKER.sub("", text)


def _comparable(text: str) -> str:
    return re.sub(r'\s+', ' ', text or '').strip().lower()


def _marker_anchors(text: str) -> list[tuple[str, str]]:
    """Для каждого плейсхолдера — есть ли вплотную число слева или справа.

    Возвращает пары (канонический токен, признак). Признак сравнивается между
    источником и переводом: так ловится перестановка кода относительно суммы,
    при которой количество маркеров и чисел не меняется.
    """
    anchors: list[tuple[str, str]] = []
    for m in _MARKER.finditer(text):
        token = f"<t{int(m.group(1))}/>"
        left = text[:m.start()].rstrip()
        right = text[m.end():].lstrip()
        side = ""
        if left and left[-1].isdigit():
            side += "L"
        if right and right[0].isdigit():
            side += "R"
        if side:
            anchors.append((token, side))
    return anchors


# ────────────────────────────────────────────────────────────────────────────
# Отпечаток настроек для кэша
# ────────────────────────────────────────────────────────────────────────────

def fingerprint(**parts: Any) -> str:
    """Короткий отпечаток семантических входов перевода.

    В ключ кэша обязаны входить модель, промпт, параметры генерации, режим
    контекста и версия глоссария: при их смене прежний перевод больше не
    является ответом на тот же вопрос и переиспользовать его нельзя.
    """
    payload = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
