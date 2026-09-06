"""
Парсер и сборщик текста для RPG Maker MV/MZ.

Поддерживает:
- Карты (Map###.json) — команды событий с кодами 401, 102, 405, 101, 324, 320
- CommonEvents.json, Troops.json — события с теми же кодами
- Actors.json, Items.json, Weapons.json, Armors.json, Skills.json,
  Enemies.json, States.json, Classes.json, MapInfos.json
- System.json — термины интерфейса

Защита управляющих кодов RPG Maker через плейсхолдеры.
"""
from __future__ import annotations

import html as _html
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from collections import Counter
from typing import Any

from core.text_fit import fit_message, flatten_for_translation


# Коды команд событий, содержащих текст
EVENT_TEXT_CODES = {
    101: "message_header",  # Show Text — header (имя говорящего в params[4] у MZ)
    401: "text_line",       # Show Text — строка сообщения
    102: "choices",         # Show Choices — массив вариантов
    402: "choice_when",     # When [choice] — параметр 1 это текст
    405: "scroll_text",     # Scrolling Text — строка
    320: "change_name",     # Change Name — новое имя актёра
    324: "name_input",      # Name Input Processing
    355: "script",          # Script — не трогаем
}

SCRIPT_LIKE_EVENT_CODES = {
    108, 408,  # Comment / comment continuation
    355, 655,  # Script / script continuation
    356,       # MV plugin command
    357,       # MZ plugin command
}

# Поля по типам файлов БД
DB_FIELDS = {
    "Actors":   ["name", "nickname", "profile"],
    "Classes":  ["name"],
    "Skills":   ["name", "description", "message1", "message2"],
    "Items":    ["name", "description"],
    "Weapons":  ["name", "description"],
    "Armors":   ["name", "description"],
    "Enemies":  ["name"],
    "States":   ["name", "message1", "message2", "message3", "message4"],
    "MapInfos": ["name"],
    "Troops":   ["name"],
}

# System.json — особые поля
SYSTEM_STRING_FIELDS = [
    "gameTitle", "currencyUnit",
]
SYSTEM_LIST_FIELDS = [
    "armorTypes", "elements", "equipTypes", "skillTypes",
    "weaponTypes",
]
SYSTEM_TERMS_LISTS = ["basic", "commands", "params"]
SYSTEM_TERMS_MESSAGES = True  # обработаем все строки в terms.messages


# ────────────────────────────────────────────────────────────────────────────
# Защита управляющих кодов
# ────────────────────────────────────────────────────────────────────────────

# Стандартные коды RPG Maker: \C[1], \N[2], \V[5], \P[1] — буква и [...]
# Плагин-теги: многобуквенные коды типа \FFF[eriya9], \AA[FFF], \F[eriyad1]
# Локализационные ссылки: \I18N[16054], \LANG[42] — буква + буквы И ЦИФРЫ + [...]
# Вариант с угловыми скобками: \N<имя> (используется в плагинах для имени говорящего)
# HTML-форматирование: <br>, <br/>, <br />, <color=...>...</color> — теги движка
# Одиночные: \\, \., \|, \!, \>, \<, \^, \$, \G, \{, \}
CONTROL_CODE_PATTERN = re.compile(
    r'('
    # Backslash-коды с буквами+цифрами после первой буквы:
    # \C[1], \FFF[eriya9], \I18N[16054], \X42N[abc]
    r'\\[A-Za-z][A-Za-z0-9]*\[[^\]]*\]'
    # Backslash-коды с угловыми скобками: \N<エリヤ>
    r'|\\[A-Za-z][A-Za-z0-9]*<[^>]*>'
    # HTML-теги форматирования: <br>, <br/>, <br />, <BR>, и т.п.
    r'|<\s*[Bb][Rr]\s*/?\s*>'
    # HTML парные цветовые теги <color=...> и </color>
    r'|<\s*/?\s*[Cc][Oo][Ll][Oo][Rr][^>]*>'
    # Плагин-теги в текстовых полях: <WordWrap>, <name:foo>, </tag>.
    # Не ловим выражения вида "A < B > C", где после < стоит пробел.
    r'|<[^ \t\r\n<>][^>\r\n]{0,120}>'
    # Плейсхолдеры формата RPG Maker/System: %1, %2...
    r'|%\d+'
    # Экранированный слэш \\
    r'|\\\\'
    # Короткие буквенные коды плагинов/патчей. В частности, эта игра использует
    # голый \c как сброс цвета; без защиты переводчик превращал его в кириллический \с.
    # Порядок важен: варианты с [...] и <...> выше должны совпасть первыми.
    r'|\\[cCwW]'
    # Одиночные backslash-символы: \., \!, \|, \<, \>, \^, \$, \G, \{, \}, \n
    r'|\\[.!|<>^$G{}nrt]'
    r')'
)


TECHNICAL_TEXT_PATTERNS = [
    # JS/RPG Maker script fragments and property chains.
    re.compile(r'(?:^|[^\w])(?:\$game|\$data|Scene_|Window_|DataManager|BattleManager|AudioManager)\w*'),
    re.compile(r'[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)+\s*(?:\(|=|$)'),
    re.compile(r'(?:=>|&&|\|\||===|!==|==|!=|<=|>=|;\s*$|^\s*(?:if|else|for|while|switch)\b)'),
    # File/resource/plugin identifiers.
    re.compile(r'^[A-Za-z0-9_$.-]+(?:[/\\:][A-Za-z0-9_$ .-]+)+$'),
    re.compile(r'^[A-Za-z_$][A-Za-z0-9_$]*(?:_[A-Za-z0-9_$]+)+$'),
]

SHORT_UI_TOKENS = {
    "hp", "mp", "tp", "atk", "def", "mat", "mdf", "agi", "luk",
    "exp", "lv", "id", "ok", "on", "off", "bgm", "bgs", "me", "se",
}

# ── Формат плейсхолдеров ────────────────────────────────────────────────────
# ВАЖНО: используем HTML-теги <t0/>, <t1/> вместо экзотических unicode-символов.
# Почему: машинные переводчики (DeepL, Google, Yandex) ИСКАЖАЮТ необычные
# unicode-символы — превращают ⟦ в ⟧, в кавычки, латинскую P в кириллическую Р.
# А HTML-теги они обязаны сохранять нетронутыми, если включён режим html/HTML.
# Это решает проблему порчи кодов на корню.
#
# Формат <t0/> (самозакрывающийся тег):
#   - ASCII-only, переводчик не «переведёт» и не исказит
#   - короткий — экономит символы (важно для лимитов)
#   - номер внутри — для восстановления в правильном порядке
#   - DeepL html-режим, Yandex format=HTML, Google — все его сохраняют
PLACEHOLDER_PREFIX = "t"   # <t0/>, <t1/>, ...


def make_placeholder(idx: int) -> str:
    """Создаёт плейсхолдер для кода с данным индексом."""
    return f"<{PLACEHOLDER_PREFIX}{idx}/>"


# Паттерн для поиска плейсхолдеров при восстановлении.
# Терпим к искажениям, которые всё же может внести переводчик:
#   <t0/>  — идеал
#   <t0 /> — пробел перед слешем
#   <t0>   — потерян слеш (html-режим может «нормализовать»)
#   < t0/> — пробел после <
#   <T0/>  — смена регистра
# Эта терпимость — вторая линия обороны на случай, если переводчик всё же
# что-то поменяет в теге, несмотря на html-режим.
_PLACEHOLDER_RESTORE = re.compile(
    r'<\s*' + PLACEHOLDER_PREFIX + r'\s*(\d+)\s*/?\s*>',
    re.IGNORECASE,
)


def protect_codes(text: str, glossary=None) -> tuple[str, list[str]]:
    """Заменяет управляющие коды на HTML-теги-плейсхолдеры.

    Возвращает (текст_с_плейсхолдерами, список_кодов).

    Если передан `glossary`, его термины (имена персонажей и т.п.) тоже
    заменяются плейсхолдерами и в переводчик не попадают. В список codes
    кладётся уже целевая форма термина, поэтому restore_codes вернёт в текст
    «Айри», а не «Airy». Без этого DeepL/Yandex переводят имена как обычные
    слова — в реальном прогоне «Airy» стал «Воздушным».

    Важно для html-режима переводчиков: «голые» спецсимволы < > & в тексте
    (например «цена < 100», «A & B») экранируются в &lt; &gt; &amp;, чтобы
    переводчик не спутал их с тегами и не сломал строгий XML/HTML-парсинг.
    Сами коды уходят в список codes и не отправляются переводчику, поэтому
    их экранировать не нужно. После перевода _unescape_html в translators.py
    вернёт спецсимволы в исходный вид.

    Порядок операций:
      1. Разбиваем текст на сегменты: коды и обычный текст между ними
      2. Коды → плейсхолдеры <tN/> (уже валидные теги)
      3. Обычный текст → экранируем < > &
    """
    codes: list[str] = []
    result_parts: list[str] = []
    last_end = 0

    for m in CONTROL_CODE_PATTERN.finditer(text):
        # Текст до кода: в нём ещё могут быть термины глоссария
        plain_segment = text[last_end:m.start()]
        _emit_plain(plain_segment, glossary, codes, result_parts)
        # Сам код → плейсхолдер
        idx = len(codes)
        codes.append(m.group(0))
        result_parts.append(make_placeholder(idx))
        last_end = m.end()

    # Хвост после последнего кода
    _emit_plain(text[last_end:], glossary, codes, result_parts)

    return "".join(result_parts), codes


def _emit_plain(segment: str, glossary, codes: list[str],
                out: list[str]) -> None:
    """Экранирует обычный текст, попутно пряча термины глоссария."""
    if not segment:
        return
    pattern = glossary.pattern() if glossary else None
    if pattern is None:
        out.append(_escape_for_html(segment))
        return
    pos = 0
    for m in pattern.finditer(segment):
        out.append(_escape_for_html(segment[pos:m.start()]))
        idx = len(codes)
        codes.append(glossary.replacement(m.group(0)))
        out.append(make_placeholder(idx))
        pos = m.end()
    out.append(_escape_for_html(segment[pos:]))


def _escape_for_html(text: str) -> str:
    """Экранирует < > & в обычном тексте для html-режима переводчика.
    Кавычки НЕ трогаем — переводчики их обрабатывают нормально, а лишнее
    экранирование засоряет текст."""
    if not text:
        return text
    return (text.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;"))


def _unescape_html_entities(text: str) -> str:
    """Расэкранирует html-сущности РОВНО ОДИН РАЗ за весь конвейер.

    Раньше расэкранирование шло дважды — в translators._unescape_html и здесь.
    Из-за этого строка, где в игре реально лежит «&amp;», превращалась в «&»:
    «Bread &amp; Butter» → «Bread & Butter». Теперь единственная точка — эта,
    и она вызывается один раз на строку, уже после подстановки sentinel-ов,
    поэтому сами коды не страдают.

    html.unescape (а не три replace) нужен потому, что провайдеры в html-режиме
    добавляют и свои сущности: &quot;, &#39;, &nbsp;.
    """
    if not text or "&" not in text:
        return text
    return _html.unescape(text)


def restore_codes(text: str, codes: list[str]) -> str:
    """Возвращает плейсхолдеры обратно в коды и расэкранирует html-сущности.

    Терпим к искажениям тегов переводчиком (регистр, пробелы, потерянный слеш).
    Если плейсхолдер потерян совсем — код просто не вернётся в эту позицию
    (см. validate_placeholders для детекции таких случаев).

    Расэкранирование (&lt; → <, &gt; → >, &amp; → &) нужно потому что в html-режиме
    спецсимволы текста были экранированы перед отправкой; теперь возвращаем их
    в исходный вид. Делается ПОСЛЕ восстановления кодов, чтобы коды (которые
    могут содержать < >, как \\N<Van>) не пострадали — они подставляются из
    списка codes уже в финальном виде.
    """
    if not codes:
        return _unescape_html_entities(text)

    used: set[int] = set()

    def repl(m: re.Match) -> str:
        idx = int(m.group(1))
        if 0 <= idx < len(codes):
            if idx in used:
                # Translators sometimes duplicate placeholder tags. Repeating a
                # control code can show garbage or change text state in-game, so
                # keep the first occurrence and drop later copies.
                return ""
            used.add(idx)
            # Маркер: подставляем плейсхолдер кода, расэкранируем потом только текст.
            # Но коды подставляем как есть — поэтому экранируем порядок: сначала
            # расэкранируем текст вокруг, затем коды уже вставлены. Проще:
            # вставляем уникальный sentinel, потом unescape, потом замена sentinel.
            return f"\x00CODE{idx}\x00"
        return m.group(0)

    # 1. Заменяем плейсхолдеры на sentinel'ы (защищаем коды от unescape)
    with_sentinels = _PLACEHOLDER_RESTORE.sub(repl, text)
    # 2. Расэкранируем текст (sentinel'ы не содержат html-сущностей)
    unescaped = _unescape_html_entities(with_sentinels)
    # 3. Заменяем sentinel'ы на реальные коды
    for idx, code in enumerate(codes):
        unescaped = unescaped.replace(f"\x00CODE{idx}\x00", code)
    return unescaped


def strip_placeholders(text: str) -> str:
    """Удаляет все плейсхолдеры из текста (для детекции языка, проверки «пустоты»)."""
    return _PLACEHOLDER_RESTORE.sub('', text)


def clean_for_detection(text: str) -> str:
    """Готовит текст для детекции языка: убирает плейсхолдеры <tN/> И
    расэкранирует html-эскейпы (&lt; &gt; &amp;).

    Нужно потому что protected-текст в e.text содержит html-эскейпы для
    html-режима переводчика, а в них есть латинские буквы (lt, gt, amp),
    которые исказили бы определение языка."""
    no_ph = _PLACEHOLDER_RESTORE.sub('', text)
    return _unescape_html_entities(no_ph)


def count_placeholders(text: str) -> set[int]:
    """Возвращает множество индексов плейсхолдеров, найденных в тексте."""
    return {int(m.group(1)) for m in _PLACEHOLDER_RESTORE.finditer(text)}


def validate_placeholders(translated: str, expected_count: int) -> tuple[bool, set[int]]:
    """Проверяет, все ли плейсхолдеры на месте после перевода.

    Возвращает (всё_ок, множество_потерянных_индексов).
    expected_count — сколько кодов было до перевода (индексы 0..expected_count-1).
    """
    found_list = [int(m.group(1)) for m in _PLACEHOLDER_RESTORE.finditer(translated)]
    found = set(found_list)
    expected = set(range(expected_count))
    missing = expected - found
    counts = Counter(found_list)
    duplicated = any(counts[idx] != 1 for idx in expected if idx in counts)
    unexpected = any(idx not in expected for idx in found)
    return (len(missing) == 0 and not duplicated and not unexpected), missing


_JS_STRING = re.compile(
    r"'((?:[^'\\\n\r]|\\.){0,200})'"
    r'|"((?:[^"\\\n\r]|\\.){0,200})"'
    r'|`((?:[^`\\\n\r]|\\.){0,200})`'
)


def _string_literals(text: str) -> set[str]:
    """Содержимое строковых литералов JS/скриптов события.

    Только литералы: имя из базы данных ломает игру ровно тогда, когда его
    сравнивают со строкой в коде. Упоминание того же слова в комментарии или
    в описании плагина ничего не ломает и блокировать перевод не должно.
    """
    found: set[str] = set()
    if not text or ("'" not in text and '"' not in text and "`" not in text):
        return found
    for m in _JS_STRING.finditer(text):
        value = m.group(1) or m.group(2) or m.group(3) or ""
        value = value.strip()
        if value:
            found.add(value)
    return found


def is_probably_technical_text(raw_text: str) -> bool:
    """Heuristic guard for strings that look like script/resource identifiers.

    The parser still extracts such strings so previews/statistics can see them,
    but they are marked as not translatable. This protects plugin commands,
    switch/variable names, enum ids and script fragments that may live in
    database names or event text fields.
    """
    if not isinstance(raw_text, str):
        return True

    stripped = raw_text.strip()
    if not stripped:
        return True

    without_codes = CONTROL_CODE_PATTERN.sub('', stripped).strip()
    if not without_codes:
        return True

    token = without_codes.strip()
    lower = token.lower()
    if lower in SHORT_UI_TOKENS:
        return True

    # Single-token latin ids are commonly used by plugins and scripts. Keep
    # natural names with spaces translatable, e.g. "Iron Sword".
    if re.fullmatch(r'[A-Za-z_$][A-Za-z0-9_$-]{0,31}', token):
        if (
            '_' in token
            or '$' in token
            or any(ch.isdigit() for ch in token)
            or token.isupper()
            or token[:1].islower()
        ):
            return True

    for pattern in TECHNICAL_TEXT_PATTERNS:
        if pattern.search(token):
            return True

    # Перечисление идентификаторов через запятую: плагины хранят в поле «имя»
    # списки вида «TaikiSperm/11Zakozu,TaikiSperm/11ZakozuRanshi». Каждая часть
    # по отдельности распознаётся как путь к ресурсу, а строка целиком — нет,
    # потому что запятая в шаблон пути не входит. Такое значение уходило
    # переводчику и после перевода ломало показ картинок.
    if _is_identifier_list(token):
        return True

    return False


_LIST_SEPARATOR = re.compile(r'\s*[;,]\s*')


def _is_identifier_list(token: str) -> bool:
    """True, если строка — перечисление технических идентификаторов."""
    if not _LIST_SEPARATOR.search(token):
        return False
    parts = [p for p in _LIST_SEPARATOR.split(token) if p.strip()]
    if len(parts) < 2:
        return False
    # Рекурсии нет: части уже без разделителя, поэтому проверяем их напрямую
    # теми же правилами, что и одиночное значение.
    for part in parts:
        part = part.strip()
        if not part:
            return False
        if part.lower() in SHORT_UI_TOKENS:
            continue
        if any(pattern.search(part) for pattern in TECHNICAL_TEXT_PATTERNS):
            continue
        if re.fullmatch(r'[A-Za-z_$][A-Za-z0-9_$-]{0,31}', part) and (
            '_' in part or '$' in part or any(ch.isdigit() for ch in part)
            or part.isupper() or part[:1].islower()
        ):
            continue
        return False
    return True


# ────────────────────────────────────────────────────────────────────────────
# Структуры
# ────────────────────────────────────────────────────────────────────────────

@dataclass
class MessageBlock:
    """Одно окно сообщения: команда-заголовок 101 и её строки 401.

    Хранится диапазон индексов в списке команд события, чтобы после перевода
    можно было ПЕРЕСОБРАТЬ блок: заново перенести текст по ширине окна и, если
    строк стало больше четырёх, разложить их на несколько окон.
    """
    header_index: int | None        # индекс команды 101, если она есть
    first: int                      # индекс первой команды 401
    last: int                       # индекс последней команды 401 (включительно)
    indent: int = 0
    has_face: bool = False          # у окна есть портрет → текста влезает меньше


@dataclass
class TextEntry:
    """Одна единица перевода с обратной ссылкой на местоположение в JSON."""
    text: str                       # исходный текст (с защищёнными кодами)
    codes: list[str]                # сохранённые управляющие коды
    file: str                       # имя файла, например 'Map001.json'
    path: tuple                     # путь до значения в структуре (для записи)
    group_id: str = ""              # ID группы (для склейки многострочных диалогов)
    is_dialogue: bool = False       # это часть диалога (для контекстной склейки)
    needs_translation: bool = True  # False = техническая строка, пропускаем переводчик
    block: MessageBlock | None = None   # для сообщений: путь ведёт к списку команд
    raw: str = ""                   # исходный текст до защиты кодов
    scope: str = ""                 # сцена: файл + Event/Page/список команд
    text_type: str = "other"        # dialogue | choice | scroll | name | database | system
    speaker: str = ""               # имя говорящего, если движок его дал явно

    @property
    def is_message(self) -> bool:
        """True — запись описывает целое окно сообщения, а не одно значение."""
        return self.block is not None


# Явный тег имени говорящего: \N<Эрия>. Именно тег, а не \N[7] — последнее
# подставляет имя героя по номеру и говорящего не обозначает.
_SPEAKER_TAG = re.compile(r'\\N<([^>]{1,40})>')


def _speaker_from_tag(raw_text: str) -> str:
    """Имя говорящего, если оно записано явным тегом.

    Имя НЕ угадывается по файлу портрета: один и тот же портрет используют
    разные персонажи, а ошибочный говорящий хуже отсутствующего — он уводит
    род и обращение в переводе.
    """
    m = _SPEAKER_TAG.search(raw_text or "")
    return m.group(1).strip() if m else ""


def scope_of(file: str, path: tuple) -> str:
    """Область, дальше которой контекст не должен выходить.

    Соседство в списке entries — это порядок обхода JSON, а не порядок сцен.
    Реплики соседних событий на одной карте лежат подряд, хотя происходят в
    разных местах и, возможно, никогда не встречаются в одной игровой сессии.
    На реальной Map041 фраза Event 4/Page 1 «Let's get along!» получала в
    качестве «следующего текста» реплики Event 5 и Event 6 — подсказку из
    чужой сцены.

    Граница берётся из настоящего пути в JSON:

    * список команд события — ``events/4/pages/1/list``, ``12/list``,
      ``3/pages/0/list``: всё, что до слова ``list`` включительно;
    * запись базы данных — её индекс в массиве;
    * остальное (System.json, displayName карты) — файл целиком.
    """
    tail: tuple = ()
    for i in range(len(path) - 1, -1, -1):
        if path[i] == "list":
            tail = path[:i + 1]
            break
    else:
        if path and isinstance(path[0], int):
            tail = path[:1]
    return file + "|" + "/".join(str(x) for x in tail)


# ────────────────────────────────────────────────────────────────────────────
# Извлечение
# ────────────────────────────────────────────────────────────────────────────

class RPGMakerProject:
    """Работа с проектом RPG Maker MV/MZ.

    Если файлы зашифрованы (CryptoJS AES), передай экземпляр GameCrypto через
    параметр `crypto`. Чтение/запись JSON станут прозрачно расшифровывать
    и зашифровывать содержимое.

    Параметр `i18n_field` — какое поле читать/писать в файле I18NTexts.json
    (если он есть). Это имя поля в записи плагина локализации:
    'en_US', 'ja_JP', 'zh_CN', 'zh_TW', 'ko_KR' и т.п.
    Сопоставляется с языковым кодом исходника маршрута.
    Если None — I18NTexts.json не обрабатывается.
    """

    # Сопоставление кодов языков ISO 639-1 с полями плагина локализации.
    # Используется чтобы автоматически выбрать правильное поле в I18NTexts.json
    # по исходному языку маршрута.
    I18N_FIELD_BY_LANG = {
        "ja": "ja_JP",
        "en": "en_US",
        "zh": "zh_CN",     # упрощённый китайский по умолчанию
        "ko": "ko_KR",
        "ru": "ru_RU",
        "es": "es_ES",
        "fr": "fr_FR",
        "de": "de_DE",
        "pt": "pt_BR",
        "it": "it_IT",
    }

    def __init__(self, data_dir: Path, crypto=None, i18n_field: str | None = None,
                 glossary=None, layout=None, fit_messages: bool = True,
                 translate_map_tree: bool = False):
        self.data_dir = Path(data_dir)
        if not self.data_dir.exists():
            raise FileNotFoundError(f"Папка не найдена: {data_dir}")
        self.entries: list[TextEntry] = []
        self._files_cache: dict[str, Any] = {}
        self.crypto = crypto  # GameCrypto | None
        self.i18n_field = i18n_field   # имя поля в I18NTexts.json, или None
        self.glossary = glossary       # core.glossary.Glossary | None
        # Переводить ли имена карт из MapInfos.json (метки дерева в редакторе).
        self.translate_map_tree = translate_map_tree
        self._script_reference_tokens: set[str] | None = None

        # Вёрстка сообщений: расклейка перед переводом и перенос после него.
        self.fit_messages = fit_messages
        self.layout = layout
        self.measurer = None
        if fit_messages:
            from core.text_fit import MessageLayout, TextMeasurer, EscapeResolver
            self.layout = layout or MessageLayout.detect(self.data_dir)
            self.measurer = TextMeasurer(self.layout, EscapeResolver(self.data_dir))

    # ── Низкоуровневое чтение/запись ────────────────────────────────────────

    def _read(self, name: str) -> Any:
        if name in self._files_cache:
            return self._files_cache[name]
        path = self.data_dir / name
        if not path.exists():
            return None
        if self.crypto is not None:
            # Зашифрованный файл — читаем как Base64-строку, расшифровываем
            with open(path, "r", encoding="utf-8") as f:
                ciphertext = f.read()
            plain = self.crypto.decrypt(ciphertext)
            data = json.loads(plain)
        else:
            with open(path, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
        self._files_cache[name] = data
        return data

    def _write(self, name: str, data: Any) -> None:
        """Атомарная запись: сначала во временный файл, потом os.replace.

        Обрыв процесса на середине обычного write оставлял бы игроку битый
        MapXXX.json, который движок не откроет вообще. Кэш и установщик
        плагина давно пишутся так же — теперь и данные игры.
        """
        path = self.data_dir / name
        # Сериализуем в JSON-строку. ensure_ascii=False сохранит юникод.
        json_text = json.dumps(data, ensure_ascii=False)
        if self.crypto is not None:
            # Зашифровываем обратно — игра должна суметь прочитать
            json_text = self.crypto.encrypt(json_text)

        fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp",
                                        dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
                f.write(json_text)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_name, path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    # ── Извлечение текста ──────────────────────────────────────────────────

    def extract_all(self) -> list[TextEntry]:
        """Собирает все переводимые строки из проекта."""
        self.entries = []

        # Карты
        for map_file in sorted(self.data_dir.glob("Map[0-9]*.json")):
            self._extract_map_events(map_file.name)

        # CommonEvents
        if (self.data_dir / "CommonEvents.json").exists():
            self._extract_common_events()

        # Troops (текст в эвентах битв)
        if (self.data_dir / "Troops.json").exists():
            self._extract_troops()

        # Базы данных
        for db_name, fields in DB_FIELDS.items():
            if db_name in ("Troops",):  # уже обработан
                continue
            file = f"{db_name}.json"
            if not (self.data_dir / file).exists():
                continue
            self._extract_db(file, fields)

        # System.json
        if (self.data_dir / "System.json").exists():
            self._extract_system()

        # I18NTexts.json — плагин локализации с массивом записей-словарей.
        # Сам файл не стандартный для RPG Maker, но встречается в играх
        # с плагином мультиязычности. Если задано i18n_field, читаем оттуда.
        if self.i18n_field and (self.data_dir / "I18NTexts.json").exists():
            self._extract_i18n_texts()

        return self.entries

    def _add(self, raw_text: str, file: str, path: tuple,
             group_id: str = "", is_dialogue: bool = False,
             force_technical: bool = False,
             block: "MessageBlock | None" = None,
             text_type: str = "other", speaker: str = "") -> None:
        if not isinstance(raw_text, str) or not raw_text.strip():
            return
        if block is not None and self.measurer is not None:
            # Расклеиваем авторские переносы: переводчику уходит связный текст,
            # а не обрывки строк, подогнанные под ширину окна.
            raw_text = flatten_for_translation(
                raw_text, self.measurer, has_face=block.has_face,
            )
        protected, codes = protect_codes(raw_text, self.glossary)

        # Технические строки: после удаления кодов и терминов глоссария не
        # осталось ничего, кроме пробелов, знаков препинания и цифр. Переводить
        # такое нельзя и незачем: это либо «header»-команда плагина вроде
        # \FFF[eriya9]\AA[FFF]\N<エリヤ>, либо имя говорящего, целиком закрытое
        # глоссарием — иначе провайдер получил бы запрос из одного «<t0/>».
        #
        # Считаем по ЗАЩИЩЁННОМУ тексту без плейсхолдеров: только так учитываются
        # и управляющие коды, и глоссарий. clean_for_detection заодно снимает
        # html-эскейпы, в которых есть латинские lt/gt/amp — они дали бы ложное
        # срабатывание «тут есть текст» на строках вида «\C[1] < >».
        cleaned_for_check = clean_for_detection(protected)
        has_letters = bool(re.search(
            r'[A-Za-zА-Яа-яЁё'
            r'\u3040-\u309F'   # хирагана
            r'\u30A0-\u30FF'   # катакана
            r'\u4E00-\u9FFF'   # кандзи/китайские
            r'\uAC00-\uD7AF'   # хангыль
            r']',
            cleaned_for_check,
        ))
        needs_translation = (
            has_letters
            and not force_technical
            and not is_probably_technical_text(raw_text)
        )

        self.entries.append(TextEntry(
            text=protected, codes=codes, file=file, path=path,
            group_id=group_id, is_dialogue=is_dialogue,
            needs_translation=needs_translation, block=block, raw=raw_text,
            scope=scope_of(file, path), text_type=text_type,
            speaker=speaker or _speaker_from_tag(raw_text),
        ))

    def _extract_event_list(self, event_list: list, file: str, base_path: tuple) -> None:
        """Обрабатывает массив команд события.

        Сообщения (101 + идущие за ней 401) извлекаются ЦЕЛЫМ БЛОКОМ, а не
        построчно. Это принципиально: в JSON строки уже разбиты под ширину
        окна, часто посреди предложения. Отправляя их по одной, мы заставляли
        переводчик работать с обрывками — «Trigger» отдельно от «Condition:»
        превращался в «спусковой крючок». Собранный блок уходит в переводчик
        связным текстом, а обратно раскладывается в text_fit.fit_message.
        """
        i = 0
        n = len(event_list)
        while i < n:
            cmd = event_list[i]
            if not isinstance(cmd, dict):
                i += 1
                continue
            code = cmd.get("code")
            params = cmd.get("parameters", [])

            if code == 101:
                block = self._collect_message_block(event_list, i, header=True)
                if block is not None:
                    self._add_message_block(event_list, file, base_path, block)
                    i = block.last + 1
                    continue
                # 101 без единой строки 401 — переводим только имя говорящего
                self._add_speaker_name(cmd, file, base_path, i)
                i += 1
                continue

            if code == 401:
                block = self._collect_message_block(event_list, i, header=False)
                if block is not None:
                    self._add_message_block(event_list, file, base_path, block)
                    i = block.last + 1
                    continue
                i += 1
                continue

            if code == 102 and isinstance(params, list) and params:
                # Варианты выбора — params[0] список строк
                choices = params[0]
                if isinstance(choices, list):
                    for j, ch in enumerate(choices):
                        if isinstance(ch, str):
                            self._add(ch, file, base_path + (i, "parameters", 0, j),
                                      text_type="choice")

            elif code == 402 and isinstance(params, list) and len(params) >= 2:
                if isinstance(params[1], str):
                    self._add(params[1], file, base_path + (i, "parameters", 1),
                              text_type="choice")

            elif code == 405 and isinstance(params, list) and params:
                if isinstance(params[0], str):
                    self._add(params[0], file, base_path + (i, "parameters", 0),
                              text_type="scroll")

            elif code == 320 and isinstance(params, list) and len(params) >= 2:
                if isinstance(params[1], str):
                    self._add(params[1], file, base_path + (i, "parameters", 1),
                              text_type="name")

            i += 1

    @staticmethod
    def _collect_message_block(event_list: list, start: int,
                               header: bool) -> MessageBlock | None:
        """Собирает окно сообщения, начиная с команды по индексу start.

        Блок — это одна команда 101 и все идущие подряд за ней 401. Любая
        другая команда закрывает блок: так устроен сам движок, который склеивает
        именно эти 401 в одно окно.
        """
        header_index = start if header else None
        first = start + 1 if header else start
        j = first
        while j < len(event_list):
            cmd = event_list[j]
            if not isinstance(cmd, dict) or cmd.get("code") != 401:
                break
            j += 1
        if j == first:
            return None

        indent = 0
        has_face = False
        anchor = event_list[header_index] if header_index is not None else event_list[first]
        if isinstance(anchor, dict):
            indent = anchor.get("indent") or 0
        if header_index is not None:
            params = event_list[header_index].get("parameters") or []
            # params[0] — имя файла портрета; непустое значение сдвигает текст
            # вправо на 168 px, и в строку влезает заметно меньше.
            has_face = bool(params and isinstance(params[0], str) and params[0].strip())

        return MessageBlock(header_index=header_index, first=first, last=j - 1,
                            indent=indent, has_face=has_face)

    def _add_message_block(self, event_list: list, file: str, base_path: tuple,
                           block: MessageBlock) -> None:
        """Добавляет окно сообщения как одну единицу перевода."""
        lines: list[str] = []
        for idx in range(block.first, block.last + 1):
            params = event_list[idx].get("parameters") or []
            lines.append(params[0] if params and isinstance(params[0], str) else "")
        raw = "\n".join(lines)

        # Заголовок 101 у MZ несёт имя говорящего в params[4] — его переводим
        # отдельной записью, окна оно не касается. Оно же — единственный
        # достоверный источник говорящего для контекста: портрет им не является.
        speaker = ""
        if block.header_index is not None:
            speaker = self._add_speaker_name(event_list[block.header_index], file,
                                             base_path, block.header_index)

        self._add(raw, file, base_path, is_dialogue=True, block=block,
                  text_type="dialogue", speaker=speaker)

    def _add_speaker_name(self, cmd: dict, file: str, base_path: tuple,
                          index: int) -> str:
        """Добавляет имя говорящего из MZ-заголовка. Возвращает само имя."""
        params = cmd.get("parameters") or []
        if len(params) >= 5 and isinstance(params[4], str) and params[4].strip():
            self._add(params[4], file, base_path + (index, "parameters", 4),
                      text_type="name")
            return params[4].strip()
        return ""

    def _extract_map_events(self, file: str) -> None:
        data = self._read(file)
        if not data or not isinstance(data, dict):
            return
        # Имя карты (display name) лежит в самом файле как displayName
        dn = data.get("displayName")
        if isinstance(dn, str) and dn.strip():
            self._add(dn, file, ("displayName",), text_type="name")
        # note карты часто содержит плагин-теги — не трогаем по умолчанию

        events = data.get("events") or []
        for ev_idx, ev in enumerate(events):
            if not isinstance(ev, dict):
                continue
            pages = ev.get("pages") or []
            for pg_idx, page in enumerate(pages):
                if not isinstance(page, dict):
                    continue
                el = page.get("list") or []
                self._extract_event_list(
                    el, file, ("events", ev_idx, "pages", pg_idx, "list")
                )

    def _extract_common_events(self) -> None:
        file = "CommonEvents.json"
        data = self._read(file)
        if not isinstance(data, list):
            return
        for i, ce in enumerate(data):
            if not isinstance(ce, dict):
                continue
            name = ce.get("name")
            if isinstance(name, str) and name.strip():
                self._add(name, file, (i, "name"), force_technical=True)
            self._extract_event_list(ce.get("list") or [], file, (i, "list"))

    def _extract_troops(self) -> None:
        file = "Troops.json"
        data = self._read(file)
        if not isinstance(data, list):
            return
        for i, tr in enumerate(data):
            if not isinstance(tr, dict):
                continue
            # Имя группы обычно служебное и может использоваться плагинами.
            name = tr.get("name")
            if isinstance(name, str) and name.strip():
                self._add(name, file, (i, "name"), force_technical=True)
            pages = tr.get("pages") or []
            for pg_idx, page in enumerate(pages):
                if not isinstance(page, dict):
                    continue
                self._extract_event_list(
                    page.get("list") or [], file, (i, "pages", pg_idx, "list")
                )

    def _extract_db(self, file: str, fields: list[str]) -> None:
        data = self._read(file)
        if not isinstance(data, list):
            return
        # MapInfos.json — дерево карт в редакторе. Движок этот файл загружает,
        # но нигде не показывает: игрок видит только displayName самой карты.
        # Перевод этих строк тратит лимит API и ничего не даёт, а в проектах,
        # где метки написаны по-японски, ещё и портит дерево в редакторе.
        editor_only = file == "MapInfos.json" and not self.translate_map_tree
        for i, item in enumerate(data):
            if not isinstance(item, dict):
                continue
            for field_name in fields:
                val = item.get(field_name)
                if isinstance(val, str) and val.strip():
                    self._add(
                        val, file, (i, field_name),
                        text_type="database",
                        force_technical=(
                            editor_only
                            or (field_name == "name"
                                and self._is_referenced_by_script(val))
                        ),
                    )

    def _is_referenced_by_script(self, text: str) -> bool:
        """True, если имя из БД упоминается в скриптах/плагин-командах/JS.

        Плагины часто сравнивают имена из базы как строковые литералы. Если
        перевести такое имя, JSON останется валидным, а сюжет сломается.

        Реализация — множество токенов, а не поиск подстроки по мегабайтам
        текста. Раньше каждое имя прогонялось через `in` по склеенному
        содержимому всех карт и всех JS: на средней игре это ~20 секунд
        полностью замороженного интерфейса. Плюс поиск подстроки давал ложные
        срабатывания — предмет «Poison» совпадал со словом «poison» в справке
        любого плагина, и название молча оставалось непереведённым.
        """
        text = (text or "").strip()
        if len(text) < 2:
            return False
        return text in self._get_script_reference_tokens()

    def _get_script_reference_tokens(self) -> set[str]:
        """Строковые литералы из скриптов игры — ровно то, с чем сравнивают имена."""
        if self._script_reference_tokens is not None:
            return self._script_reference_tokens

        tokens: set[str] = set()

        def add_scalars(value: Any) -> None:
            if isinstance(value, str):
                tokens.update(_string_literals(value))
                # Сама параметр-строка тоже может быть именем целиком:
                # плагин-команда вида ["Открыть магазин", "Зелье"].
                stripped = value.strip()
                if 0 < len(stripped) <= 64:
                    tokens.add(stripped)
            elif isinstance(value, list):
                for item in value:
                    add_scalars(item)
            elif isinstance(value, dict):
                for item in value.values():
                    add_scalars(item)

        def scan_event_list(event_list: Any) -> None:
            if not isinstance(event_list, list):
                return
            for cmd in event_list:
                if not isinstance(cmd, dict):
                    continue
                if cmd.get("code") in SCRIPT_LIKE_EVENT_CODES:
                    add_scalars(cmd.get("parameters") or [])

        def scan_obj(obj: Any) -> None:
            if isinstance(obj, dict):
                if "list" in obj:
                    scan_event_list(obj.get("list"))
                for value in obj.values():
                    scan_obj(value)
            elif isinstance(obj, list):
                for value in obj:
                    scan_obj(value)

        for path in sorted(self.data_dir.glob("Map[0-9]*.json")):
            scan_obj(self._read(path.name))
            # Карты держим в памяти только на время сканирования: на большой
            # игре кэш всех карт — это сотни мегабайт впустую.
            self._files_cache.pop(path.name, None)
        for name in ("CommonEvents.json", "Troops.json"):
            if (self.data_dir / name).exists():
                scan_obj(self._read(name))

        # Плагины и движок тоже сравнивают имена из БД. Берём только строковые
        # литералы: комментарии и блоки @help больше не создают ложных совпадений.
        js_dir = self.data_dir.parent / "js"
        if js_dir.exists():
            for js_path in sorted(js_dir.rglob("*.js")):
                try:
                    if js_path.stat().st_size > 4_000_000:
                        continue
                    tokens.update(_string_literals(
                        js_path.read_text(encoding="utf-8", errors="ignore")
                    ))
                except OSError:
                    continue

        self._script_reference_tokens = tokens
        return tokens

    def _extract_system(self) -> None:
        file = "System.json"
        data = self._read(file)
        if not isinstance(data, dict):
            return

        for f in SYSTEM_STRING_FIELDS:
            val = data.get(f)
            if isinstance(val, str) and val.strip():
                self._add(val, file, (f,), text_type="system")

        for f in SYSTEM_LIST_FIELDS:
            arr = data.get(f)
            if isinstance(arr, list):
                for i, v in enumerate(arr):
                    if isinstance(v, str) and v.strip():
                        self._add(v, file, (f, i), text_type="system")

        terms = data.get("terms")
        if isinstance(terms, dict):
            for tl in SYSTEM_TERMS_LISTS:
                arr = terms.get(tl)
                if isinstance(arr, list):
                    for i, v in enumerate(arr):
                        if isinstance(v, str) and v.strip():
                            self._add(v, file, ("terms", tl, i), text_type="system")
            messages = terms.get("messages")
            if isinstance(messages, dict):
                for key, val in messages.items():
                    if isinstance(val, str) and val.strip():
                        self._add(val, file, ("terms", "messages", key), text_type="system")

    def _extract_i18n_texts(self) -> None:
        """Извлекает строки из I18NTexts.json — плагина мультиязычности.

        Структура файла: массив объектов вида
            {"id": 16054, "identifier": "...",
             "ja_JP": "...", "en_US": "...", "zh_CN": "...", ...}

        Извлекается поле self.i18n_field (например 'en_US' для маршрута en→ru).
        Записываем переводы обратно в ТО ЖЕ поле — замещаем язык-источник.
        Игра при выборе этого языка будет показывать переводы.
        """
        file = "I18NTexts.json"
        data = self._read(file)
        if not isinstance(data, list):
            return

        field = self.i18n_field
        for i, item in enumerate(data):
            if not isinstance(item, dict):
                continue
            val = item.get(field)
            if isinstance(val, str) and val.strip():
                # path: индекс записи в массиве + имя поля
                self._add(val, file, (i, field), is_dialogue=True)

    def filter_to_files(self, allowed_files: set[str]) -> None:
        """Оставляет только entries, относящиеся к указанным файлам.
        Используется для тестового перевода на подмножестве файлов."""
        self.entries = [e for e in self.entries if e.file in allowed_files]

    def filter_to_languages(self, allowed_langs: set[str]) -> int:
        """Помечает как не требующие перевода все entries, чей язык НЕ входит в фильтр.
        Не удаляет их — они остаются в списке для apply_translations, чтобы
        записаться в JSON как есть.
        Возвращает количество отфильтрованных (теперь technical) строк."""
        from core.lang_detect import detect_language
        filtered = 0
        for e in self.entries:
            if not e.needs_translation:
                continue
            # Детектим на «чистом» тексте без плейсхолдеров
            cleaned = clean_for_detection(e.text)
            lang = detect_language(cleaned)
            if lang not in allowed_langs:
                e.needs_translation = False
                filtered += 1
        return filtered

    # ── Применение переводов ───────────────────────────────────────────────

    def apply_translations(self, translations: dict[int, str]) -> dict:
        """Применяет переводы по индексам entries и записывает файлы.

        Возвращает статистику валидации плейсхолдеров:
            {'total': N, 'with_codes': M, 'broken': K, 'skipped': K,
             'rewrapped': R, 'extra_windows': W, 'broken_entries': [...]}
        где broken — строки, в которых переводчик потерял управляющие коды,
        rewrapped — сообщения, заново свёрстанные по ширине окна, а
        extra_windows — сколько окон добавилось, потому что перевод длиннее.
        """
        stats = {
            "total": 0,
            "with_codes": 0,
            "broken": 0,
            "skipped": 0,
            "rewrapped": 0,
            "extra_windows": 0,
            "broken_entries": [],
        }

        # Простые значения пишутся по пути; сообщения — пересборкой блока.
        by_file: dict[str, list[tuple[TextEntry, str]]] = {}
        messages: dict[str, dict[tuple, list[tuple[TextEntry, str]]]] = {}

        # Технические записи переводчику не отправлялись, но глоссарий мог
        # заменить в них имя: параметр 4 команды 101 — это часто ровно «Rin»,
        # то есть строка целиком из одного термина. Без этого шага имя
        # говорящего так и осталось бы на языке оригинала.
        pending = dict(translations)
        for idx, entry in enumerate(self.entries):
            if idx in pending or entry.needs_translation:
                continue
            substituted = restore_codes(entry.text, entry.codes)
            if substituted != entry.raw:
                pending[idx] = entry.text

        for idx, translated in pending.items():
            if idx >= len(self.entries):
                continue
            entry = self.entries[idx]
            stats["total"] += 1

            # Валидация плейсхолдеров перед восстановлением. Проверяем и строки
            # без исходных кодов: чужой <tN/> мог переехать сюда из соседней
            # реплики. Повреждённый перевод не записываем вообще — исходная
            # строка безопаснее видимого служебного мусора в игре.
            ok, missing = validate_placeholders(translated, len(entry.codes))
            if entry.codes:
                stats["with_codes"] += 1
            if not ok:
                stats["broken"] += 1
                stats["skipped"] += 1
                if len(stats["broken_entries"]) < 200:
                    stats["broken_entries"].append({
                        "file": entry.file,
                        "missing": sorted(missing),
                        "text": translated[:80],
                    })
                continue

            final = restore_codes(translated, entry.codes)
            if entry.is_message:
                messages.setdefault(entry.file, {}).setdefault(
                    entry.path, []).append((entry, final))
            else:
                by_file.setdefault(entry.file, []).append((entry, final))

        touched = set(by_file) | set(messages)
        for file in sorted(touched):
            data = self._read(file)
            if data is None:
                continue
            for entry, value in by_file.get(file, []):
                _set_by_path(data, entry.path, value)
            for list_path, items in messages.get(file, {}).items():
                self._rebuild_message_list(data, list_path, items, stats)
            self._write(file, data)

        return stats

    def _rebuild_message_list(self, data: Any, list_path: tuple,
                              items: list[tuple[TextEntry, str]],
                              stats: dict) -> None:
        """Пересобирает список команд события с новой вёрсткой сообщений.

        Переведённый текст заново переносится по РЕАЛЬНОЙ ширине окна, а если
        строк стало больше, чем окно показывает, блок разворачивается в
        несколько окон — каждое со своей копией заголовка 101. Без этого лишние
        строки просто не помещались: игрок видел обрезанный текст, а хвост
        фразы уезжал в следующее сообщение посреди слова.
        """
        try:
            event_list = _get_by_path(data, list_path)
        except (KeyError, IndexError, TypeError):
            return
        if not isinstance(event_list, list):
            return

        replacements: dict[int, tuple[TextEntry, str]] = {}
        for entry, text in items:
            block = entry.block
            if block is None:
                continue
            anchor = block.header_index if block.header_index is not None else block.first
            replacements[anchor] = (entry, text)

        if not replacements:
            return

        rebuilt: list[Any] = []
        i = 0
        while i < len(event_list):
            found = replacements.get(i)
            if found is None:
                rebuilt.append(event_list[i])
                i += 1
                continue

            entry, text = found
            block = entry.block
            pages = self._layout_pages(text, block)
            header = (event_list[block.header_index]
                      if block.header_index is not None else None)
            # Служебные ключи исходных команд (например, _original от прошлого
            # прогона перевода) переносим на первую строку блока — заново
            # созданные команды их иначе теряют.
            extra = _merge_extra_keys(event_list[block.first:block.last + 1])
            first = True

            for page_no, lines in enumerate(pages):
                if header is not None:
                    # Второе и последующие окна получают копию заголовка:
                    # тот же портрет, та же позиция, то же имя говорящего.
                    rebuilt.append(header if page_no == 0 else _clone_command(header))
                for line in lines:
                    command = {
                        "code": 401,
                        "indent": block.indent,
                        "parameters": [line],
                    }
                    if first and extra:
                        command.update(extra)
                        first = False
                    rebuilt.append(command)

            stats["rewrapped"] += 1
            if len(pages) > 1:
                stats["extra_windows"] += len(pages) - 1
            i = block.last + 1

        event_list[:] = rebuilt

    def _layout_pages(self, text: str, block: MessageBlock) -> list[list[str]]:
        """Переносит текст сообщения по ширине окна и режет на окна."""
        if self.measurer is None:
            return [text.split("\n")]
        max_lines = self.layout.max_lines
        if block.header_index is None:
            # Разложить на несколько окон без заголовка нечем — движок склеит
            # их обратно в одно. Оставляем как есть: пусть лучше будет длинное
            # окно, чем потерянные строки.
            max_lines = 0
        fitted = fit_message(text, self.measurer, has_face=block.has_face,
                             max_lines=max_lines)
        return fitted.pages


_STANDARD_COMMAND_KEYS = {"code", "indent", "parameters"}


def _merge_extra_keys(commands: list) -> dict:
    """Нестандартные поля команд блока — их нельзя терять при пересборке.

    Инструменты перевода и плагины дописывают к командам свои ключи
    (в этих картах — `_original` с исходным японским текстом). Строки блока
    после перевода перекомпонованы, поэтому привязать поле к конкретной строке
    невозможно: строковые значения склеиваются в исходном порядке и кладутся
    на первую строку — там они описывают всё сообщение целиком, как и раньше.
    """
    merged: dict[str, Any] = {}
    parts: dict[str, list[str]] = {}
    for cmd in commands:
        if not isinstance(cmd, dict):
            continue
        for key, value in cmd.items():
            if key in _STANDARD_COMMAND_KEYS:
                continue
            if isinstance(value, str):
                parts.setdefault(key, []).append(value)
            else:
                merged.setdefault(key, value)
    for key, values in parts.items():
        merged[key] = "\n".join(values)
    return merged


def _clone_command(cmd: Any) -> Any:
    """Копия команды события — заголовок повторяется для каждого окна."""
    import copy
    return copy.deepcopy(cmd)


def _get_by_path(data: Any, path: tuple) -> Any:
    obj = data
    for key in path:
        obj = obj[key]
    return obj


def _set_by_path(data: Any, path: tuple, value: Any) -> None:
    """Записывает value по пути в структуре data."""
    obj = data
    for key in path[:-1]:
        obj = obj[key]
    obj[path[-1]] = value


# ────────────────────────────────────────────────────────────────────────────
# Группировка диалогов для контекстного перевода
# ────────────────────────────────────────────────────────────────────────────

@dataclass
class TranslationUnit:
    """Единица для отправки в переводчик: либо одна строка, либо склеенный диалог."""
    entry_indices: list[int]   # индексы в RPGMakerProject.entries
    combined_text: str         # текст для перевода (склеенный через разделитель)
    separator: str             # разделитель, использованный при склейке
    context: str = ""          # соседние реплики/сцена: подсказка для API, не переводится
    tagged: bool = False       # части обёрнуты в HTML-теги с устойчивыми границами
    # ── Поля для провайдеров, принимающих элемент целиком ───────────────────
    # Облачные сервисы берут одну строку context на весь пакет; языковая модель
    # получает контекст ОТДЕЛЬНО для каждого элемента, иначе соседний элемент
    # из другой сцены становится подсказкой.
    item_id: str = ""              # устойчивый идентификатор: путь в JSON
    text_type: str = "other"
    speaker: str = ""
    context_before: list[str] = field(default_factory=list)
    context_after: list[str] = field(default_factory=list)
    location: dict = field(default_factory=dict)


# Обычный перенос строки внутри сообщения. Раньше использовался видимый
# Record Separator (␞), но машинные переводчики иногда удаляют или перемещают
# его. Из-за этого блок невозможно корректно разложить обратно по строкам.
JOIN_SEP = "\n"
GROUP_TAG = "rpgline"


def _wrap_group_part(index: int, text: str) -> str:
    """Оборачивает одну реплику в тег, сохраняемый HTML-режимом переводчиков."""
    return f'<{GROUP_TAG} data-i="{index}">{text}</{GROUP_TAG}>'


def build_translation_units(entries: list[TextEntry],
                            group_dialogues: bool = True,
                            item_window: int = 1) -> list[TranslationUnit]:
    """
    Превращает entries в units для перевода.
    Технические entries (needs_translation=False) пропускаются — они не идут переводчику.
    Если group_dialogues=True, склеивает подряд идущие реплики диалога
    с одним group_id, чтобы переводчик видел контекст.
    """
    units: list[TranslationUnit] = []
    i = 0
    while i < len(entries):
        e = entries[i]
        # Технические entries вообще не отправляем переводчику
        if not e.needs_translation:
            i += 1
            continue

        if group_dialogues and e.is_dialogue and e.group_id:
            # Собираем все entries с тем же group_id, пропуская технические
            group_indices = [i]
            j = i + 1
            while j < len(entries):
                ej = entries[j]
                if not ej.is_dialogue or ej.group_id != e.group_id:
                    break
                if ej.needs_translation:
                    group_indices.append(j)
                j += 1
            # Обычные переносы строк нельзя использовать как границы: переводчик
            # добавляет собственные переносы и старый код затем резал целую сцену
            # по длине, перемещая имена и управляющие коды между окнами. Парные
            # HTML-теги переживают DeepL/Yandex HTML mode и однозначно сохраняют
            # принадлежность каждой части исходной команде 401.
            combined = "".join(
                _wrap_group_part(pos, entries[k].text)
                for pos, k in enumerate(group_indices)
            )
            units.append(TranslationUnit(
                entry_indices=group_indices,
                combined_text=combined,
                separator=JOIN_SEP,
                context=_build_unit_context(entries, i, j),
                tagged=True,
                **_unit_meta(entries, i, j, item_window),
            ))
            i = j
        else:
            units.append(TranslationUnit(
                entry_indices=[i],
                combined_text=e.text,
                separator=JOIN_SEP,
                context=_build_unit_context(entries, i, i + 1),
                **_unit_meta(entries, i, i + 1, item_window),
            ))
            i += 1
    return units


def _unit_meta(entries: list[TextEntry], start: int, end: int,
               window: int) -> dict:
    """Метаданные единицы: идентификатор, тип, говорящий, контекст, место."""
    e = entries[start]
    before, _current, after = context_lines(entries, start, end, window)
    return {
        "item_id": item_id_for(e),
        "text_type": e.text_type,
        "speaker": e.speaker,
        "context_before": before,
        "context_after": after,
        "location": location_of(e),
    }


def item_id_for(entry: TextEntry) -> str:
    """Идентификатор элемента: файл, путь в JSON и начало окна сообщения.

    Берётся из настоящего положения в исходнике, а не из номера в пакете:
    номер меняется от запуска к запуску, путь — нет. Это позволяет
    сопоставлять ответ модели по идентификатору, а не по позиции.

    Номер команды в конце обязателен. У окна сообщения путь ведёт к СПИСКУ
    команд события, а не к отдельной строке, поэтому все окна одной страницы
    имеют один и тот же путь. Без этого различителя на реальной игре 35 877
    единиц из 39 717 делили идентификатор с соседями, и любой пакет с двумя
    окнами одной страницы отвергался целиком как ответ с повтором id.
    """
    ident = entry.file + ":" + "/".join(str(x) for x in entry.path)
    if entry.block is None:
        return ident
    anchor = entry.block.header_index
    if anchor is None:
        anchor = entry.block.first
    return f"{ident}#{anchor}"


def location_of(entry: TextEntry) -> dict:
    """Место реплики в понятиях редактора: карта, событие, страница."""
    loc: dict = {"file": entry.file}
    path = entry.path
    for key, label in (("events", "event"), ("pages", "page")):
        try:
            idx = path.index(key)
        except ValueError:
            continue
        if idx + 1 < len(path):
            loc[label] = path[idx + 1]
    if entry.file == "CommonEvents.json" and path and isinstance(path[0], int):
        loc["common_event"] = path[0]
    if entry.file == "Troops.json" and path and isinstance(path[0], int):
        loc["troop"] = path[0]
    return loc


def split_translated_unit(unit: TranslationUnit, translated: str) -> dict[int, str]:
    """Разбивает переведённый текст обратно по entries unit'а."""
    if len(unit.entry_indices) == 1:
        return {unit.entry_indices[0]: translated}

    target_count = len(unit.entry_indices)
    if unit.tagged:
        parts = _split_tagged_translation(translated, target_count)
        return {idx: parts[k] for k, idx in enumerate(unit.entry_indices)}

    parts = _split_dialogue_translation(translated, target_count)
    return {idx: parts[k] for k, idx in enumerate(unit.entry_indices)}


_GROUP_TAG_RE = re.compile(
    r'<\s*rpgline\b[^>]*\bdata-i\s*=\s*["\']?(\d+)["\']?[^>]*>'
    r'(.*?)'
    r'<\s*/\s*rpgline\s*>',
    re.IGNORECASE | re.DOTALL,
)


def _split_tagged_translation(translated: str, target_count: int) -> list[str]:
    """Извлекает реплики из защищённого HTML-блока или останавливает сборку.

    Молчаливый fallback здесь опасен: именно он раньше создавал видимые <tN/>.
    Если сервис потерял/дублировал контейнер, лучше оставить исходные JSON и
    показать ошибку пользователю, чем собрать повреждённую игру.
    """
    found: dict[int, str] = {}
    duplicates: set[int] = set()
    for match in _GROUP_TAG_RE.finditer(translated):
        index = int(match.group(1))
        if index in found:
            duplicates.add(index)
        else:
            found[index] = match.group(2).strip()

    expected = set(range(target_count))
    actual = set(found)
    if actual != expected or duplicates:
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        raise ValueError(
            "Переводчик повредил границы RPG Maker: "
            f"missing={missing}, unexpected={unexpected}, duplicates={sorted(duplicates)}"
        )
    return [found[i] for i in range(target_count)]


def context_lines(entries: list[TextEntry], start: int, end: int,
                  window: int = 3) -> tuple[list[str], list[str], list[str]]:
    """Соседние реплики В ПРЕДЕЛАХ ОДНОЙ СЦЕНЫ: (до, текущее, после).

    Ограничение по scope — не украшение, а условие правильности. Записи лежат
    в порядке обхода JSON, поэтому за последней репликой одного события сразу
    идёт первая реплика следующего. Без границы фраза из Event 4 получала в
    качестве продолжения текст Event 5 и Event 6 — сцену, которая в игре может
    вообще не случиться рядом. Модель и DeepL честно учитывали эту подсказку и
    уводили перевод в сторону.

    Граница действует для всех провайдеров: контекст из чужой сцены вреден
    любому переводчику, а не только языковой модели.
    """
    if not entries or not (0 <= start < len(entries)):
        return [], [], []
    scope = entries[start].scope

    def readable(e: TextEntry) -> str:
        text = clean_for_detection(e.text)
        return re.sub(r'\s+', ' ', text).strip()

    prev_lines: list[str] = []
    i = start - 1
    while i >= 0 and len(prev_lines) < window:
        e = entries[i]
        if e.scope != scope:
            break
        text = readable(e)
        if text:
            prev_lines.append(text)
        i -= 1
    prev_lines.reverse()

    current_lines = [readable(entries[i]) for i in range(start, end)]
    current_lines = [line for line in current_lines if line]

    next_lines: list[str] = []
    i = end
    while i < len(entries) and len(next_lines) < window:
        e = entries[i]
        if e.scope != scope:
            break
        text = readable(e)
        if text:
            next_lines.append(text)
        i += 1

    return prev_lines, current_lines, next_lines


def _build_unit_context(entries: list[TextEntry], start: int, end: int,
                        window: int = 3, limit: int = 1200) -> str:
    """Создаёт компактный контекст вокруг unit без изменения переводимого текста."""
    if not entries:
        return ""
    file = entries[start].file if 0 <= start < len(entries) else ""
    prev_lines, current_lines, next_lines = context_lines(entries, start, end, window)

    chunks = [f"RPG Maker scene/file: {file}"]
    if prev_lines:
        chunks.append("Previous text: " + " / ".join(prev_lines))
    if current_lines:
        chunks.append("Current full message: " + " ".join(current_lines))
    if next_lines:
        chunks.append("Next text: " + " / ".join(next_lines))
    return "\n".join(chunks)[:limit]


def _split_dialogue_translation(translated: str, target_count: int) -> list[str]:
    """Разбивает цельный перевод сообщения на исходное число 401-строк.

    RPG Maker хранит одно окно сообщения как несколько команд 401. Переводить
    эти строки по отдельности плохо: предложения часто разорваны посередине.
    Поэтому переводим сообщение целиком, а затем раскладываем результат обратно
    по строкам. Если переводчик сохранил переносы, используем их; если нет —
    мягко переносим по словам.
    """
    if target_count <= 1:
        return [translated]

    normalized = translated.replace("\r\n", "\n").replace("\r", "\n").strip()
    # Совместимость со старыми кэшами/результатами, где мог остаться ␞.
    if "␞" in normalized:
        parts = [p.strip() for p in re.split(r'\s*␞\s*', normalized)]
    else:
        parts = [p.strip() for p in normalized.split("\n") if p.strip()]

    if len(parts) == target_count:
        return parts
    if 1 < len(parts) < target_count:
        return parts + [""] * (target_count - len(parts))

    flat = re.sub(r'\s+', ' ', normalized).strip()
    if not flat:
        return [""] * target_count

    target_width = max(24, (len(flat) + target_count - 1) // target_count)
    words = flat.split(" ")
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and len(candidate) > target_width and len(lines) < target_count - 1:
            lines.append(current)
            current = word
        else:
            current = candidate
    lines.append(current)

    if len(lines) < target_count:
        lines.extend([""] * (target_count - len(lines)))
    elif len(lines) > target_count:
        lines = lines[:target_count - 1] + [" ".join(lines[target_count - 1:])]
    return lines


# ────────────────────────────────────────────────────────────────────────────
# Анализ проекта (без перевода)
# ────────────────────────────────────────────────────────────────────────────

@dataclass
class ProjectStats:
    """Сводка по проекту — для показа пользователю перед запуском."""
    total_entries: int                       # всего строк
    translatable_entries: int                # из них переводимых
    technical_entries: int                   # технические (не идут переводчику)
    total_chars_raw: int                     # символы исходного текста (с кодами)
    total_chars_clean: int                   # символы без управляющих кодов
    translatable_chars: int                  # символы только переводимых строк
    units_count: int                         # количество пакетов
    units_with_context: int                  # многострочные пакеты с контекстом
    by_file: dict[str, int]                  # количество переводимых строк по файлам
    files_with_text: list[str]               # файлы, содержащие переводимый текст
    by_language: dict[str, int] = None       # язык → количество строк
    chars_by_language: dict[str, int] = None # язык → количество символов

    def __post_init__(self):
        if self.by_language is None:
            self.by_language = {}
        if self.chars_by_language is None:
            self.chars_by_language = {}

    def estimate_chars_per_stage(self, stages: int = 1, expansion: float = 1.5,
                                 lang_filter: list[str] | None = None) -> int:
        """Оценка расхода символов на всю цепочку.
        lang_filter — если задан, считаем только строки на указанных языках.
        """
        if lang_filter:
            base = sum(self.chars_by_language.get(l, 0) for l in lang_filter)
        else:
            base = self.translatable_chars
        if stages == 1:
            return base
        return base + int(base * expansion)


def analyze_project(data_dir, group_dialogues: bool = True,
                    crypto=None, i18n_field: str | None = None,
                    glossary=None, layout=None
                    ) -> tuple[ProjectStats, RPGMakerProject]:
    """Парсит проект и возвращает статистику. Без перевода и без записи.
    Если файлы зашифрованы, передай экземпляр GameCrypto.
    i18n_field — поле в I18NTexts.json для маршрута (например 'en_US')."""
    from core.lang_detect import detect_language

    proj = RPGMakerProject(data_dir, crypto=crypto, i18n_field=i18n_field,
                           glossary=glossary, layout=layout)
    entries = proj.extract_all()

    translatable = [e for e in entries if e.needs_translation]
    technical = [e for e in entries if not e.needs_translation]

    raw_chars = 0
    clean_chars = 0
    translatable_chars = 0
    for e in entries:
        original = restore_codes(e.text, e.codes)
        raw_chars += len(original)
        # «Чистый» — без управляющих кодов; используем уже защищённый text,
        # из которого убираем плейсхолдеры
        cleaned = clean_for_detection(e.text)
        clean_chars += len(cleaned)
        if e.needs_translation:
            translatable_chars += len(cleaned)

    by_file: dict[str, int] = {}
    for e in translatable:
        by_file[e.file] = by_file.get(e.file, 0) + 1
    files_with_text = sorted(by_file.keys())

    # Распределение по языкам — детектим на «чистом» тексте без управляющих кодов
    by_language: dict[str, int] = {}
    chars_by_language: dict[str, int] = {}
    for e in translatable:
        cleaned = clean_for_detection(e.text)
        lang = detect_language(cleaned)
        by_language[lang] = by_language.get(lang, 0) + 1
        chars_by_language[lang] = chars_by_language.get(lang, 0) + len(cleaned)

    units = build_translation_units(entries, group_dialogues=group_dialogues)
    multi = sum(1 for u in units if len(u.entry_indices) > 1)

    stats = ProjectStats(
        total_entries=len(entries),
        translatable_entries=len(translatable),
        technical_entries=len(technical),
        total_chars_raw=raw_chars,
        total_chars_clean=clean_chars,
        translatable_chars=translatable_chars,
        units_count=len(units),
        units_with_context=multi,
        by_file=by_file,
        files_with_text=files_with_text,
        by_language=by_language,
        chars_by_language=chars_by_language,
    )
    return stats, proj
