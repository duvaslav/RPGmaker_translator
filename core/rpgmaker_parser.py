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

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from collections import Counter
from typing import Any, Iterator


# Коды команд событий, содержащих текст
EVENT_TEXT_CODES = {
    101: "name_speaker",   # Show Text — header (имя говорящего в параметре 4 для MZ)
    401: "text_line",      # Show Text — строка диалога
    102: "choices",        # Show Choices — массив вариантов
    402: "choice_when",    # When [choice] — параметр 1 это текст
    405: "scroll_text",    # Scrolling Text — строка
    324: "name_input",     # Name Input — обычно не текст, но имя актёра
    320: "change_name",    # Change Name — новое имя актёра
    324: "name_input_processing",
    355: "script",         # Script — не трогаем
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


def protect_codes(text: str) -> tuple[str, list[str]]:
    """Заменяет управляющие коды на HTML-теги-плейсхолдеры.

    Возвращает (текст_с_плейсхолдерами, список_кодов).

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
        # Текст до кода — экранируем
        plain_segment = text[last_end:m.start()]
        result_parts.append(_escape_for_html(plain_segment))
        # Сам код → плейсхолдер
        idx = len(codes)
        codes.append(m.group(0))
        result_parts.append(make_placeholder(idx))
        last_end = m.end()

    # Хвост после последнего кода
    result_parts.append(_escape_for_html(text[last_end:]))

    return "".join(result_parts), codes


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
    """Расэкранирует < > & обратно из html-сущностей. Идемпотентна."""
    if not text or "&" not in text:
        return text
    return (text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&"))


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
    if "&" in no_ph:
        no_ph = (no_ph.replace("&lt;", "<")
                      .replace("&gt;", ">")
                      .replace("&amp;", "&"))
    return no_ph


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

    return False


# ────────────────────────────────────────────────────────────────────────────
# Структуры
# ────────────────────────────────────────────────────────────────────────────

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

    def __init__(self, data_dir: Path, crypto=None, i18n_field: str | None = None):
        self.data_dir = Path(data_dir)
        if not self.data_dir.exists():
            raise FileNotFoundError(f"Папка не найдена: {data_dir}")
        self.entries: list[TextEntry] = []
        self._files_cache: dict[str, Any] = {}
        self.crypto = crypto  # GameCrypto | None
        self.i18n_field = i18n_field   # имя поля в I18NTexts.json, или None
        self._script_reference_text: str | None = None

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
        path = self.data_dir / name
        # Сериализуем в JSON-строку. ensure_ascii=False сохранит юникод.
        json_text = json.dumps(data, ensure_ascii=False)
        if self.crypto is not None:
            # Зашифровываем обратно — игра должна суметь прочитать
            ciphertext = self.crypto.encrypt(json_text)
            with open(path, "w", encoding="utf-8") as f:
                f.write(ciphertext)
        else:
            with open(path, "w", encoding="utf-8") as f:
                f.write(json_text)

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
             force_technical: bool = False) -> None:
        if not isinstance(raw_text, str) or not raw_text.strip():
            return
        protected, codes = protect_codes(raw_text)

        # Технические строки: после удаления кодов остался только мусор —
        # пробелы, знаки препинания, цифры. Такие строки переводить нельзя —
        # обычно это «header»-команды плагинов вроде \FFF[eriya9]\AA[FFF]\N<エリヤ>.
        #
        # ВАЖНО: проверяем по ИСХОДНОМУ тексту (raw_text) с вырезанными кодами,
        # а НЕ по protected. Потому что protected содержит html-эскейпы (&lt; &gt;
        # &amp;), в которых есть латинские буквы lt/gt/amp — они дали бы ложное
        # срабатывание «тут есть текст» на строках вида «\C[1] < >».
        raw_without_codes = CONTROL_CODE_PATTERN.sub('', raw_text)
        has_letters = bool(re.search(
            r'[A-Za-zА-Яа-яЁё'
            r'\u3040-\u309F'   # хирагана
            r'\u30A0-\u30FF'   # катакана
            r'\u4E00-\u9FFF'   # кандзи/китайские
            r'\uAC00-\uD7AF'   # хангыль
            r']',
            raw_without_codes,
        ))
        needs_translation = (
            has_letters
            and not force_technical
            and not is_probably_technical_text(raw_text)
        )

        self.entries.append(TextEntry(
            text=protected, codes=codes, file=file, path=path,
            group_id=group_id, is_dialogue=is_dialogue,
            needs_translation=needs_translation,
        ))

    def _extract_event_list(self, event_list: list, file: str, base_path: tuple) -> None:
        """Обрабатывает массив команд событий, склеивая подряд идущие 401 в группы.
        Между блоками 401 может попадаться 101 (header с именем говорящего/плагин-тегами) —
        она не разрывает диалоговую группу.
        """
        dialogue_group = 0
        # Учитываем только содержательные «разрывы»: любой код кроме 401, 101 и комментариев
        # сбрасывает группу. 101 между 401-ми — часть того же диалога (это смена портрета/имени).
        DIALOGUE_NEUTRAL_CODES = {101, 108, 408}  # 108/408 — комментарии в эвенте
        prev_dialogue_code = None
        for i, cmd in enumerate(event_list):
            if not isinstance(cmd, dict):
                continue
            code = cmd.get("code")
            params = cmd.get("parameters", [])

            if code == 401 and isinstance(params, list) and params:
                # Подряд идущие 401 (с возможным 101 между ними) = одно сообщение
                if prev_dialogue_code != 401:
                    dialogue_group += 1
                gid = f"{file}:{base_path}:dlg{dialogue_group}"
                self._add(params[0], file, base_path + (i, "parameters", 0),
                          group_id=gid, is_dialogue=True)
                prev_dialogue_code = 401

            elif code == 102 and isinstance(params, list) and params:
                # Варианты выбора — params[0] список строк
                choices = params[0]
                if isinstance(choices, list):
                    for j, ch in enumerate(choices):
                        if isinstance(ch, str):
                            self._add(ch, file, base_path + (i, "parameters", 0, j))
                prev_dialogue_code = code

            elif code == 402 and isinstance(params, list) and len(params) >= 2:
                if isinstance(params[1], str):
                    self._add(params[1], file, base_path + (i, "parameters", 1))
                prev_dialogue_code = code

            elif code == 405 and isinstance(params, list) and params:
                if isinstance(params[0], str):
                    self._add(params[0], file, base_path + (i, "parameters", 0))
                prev_dialogue_code = code

            elif code == 101 and isinstance(params, list) and len(params) >= 5:
                # MV/MZ: имя говорящего в параметре 4 (только в MZ; в MV это может быть face name)
                if isinstance(params[4], str) and params[4].strip():
                    self._add(params[4], file, base_path + (i, "parameters", 4))
                # 101 НЕ сбрасывает diaolgue группу — это header следующего 401-блока

            elif code == 320 and isinstance(params, list) and len(params) >= 2:
                if isinstance(params[1], str):
                    self._add(params[1], file, base_path + (i, "parameters", 1))
                prev_dialogue_code = code

            elif code in DIALOGUE_NEUTRAL_CODES:
                # Нейтральные коды (комментарии, 101 без подходящих параметров)
                # не сбрасывают группу
                pass

            else:
                # Любая другая команда разрывает диалоговую цепочку
                prev_dialogue_code = code

    def _extract_map_events(self, file: str) -> None:
        data = self._read(file)
        if not data or not isinstance(data, dict):
            return
        # Имя карты (display name) лежит в самом файле как displayName
        dn = data.get("displayName")
        if isinstance(dn, str) and dn.strip():
            self._add(dn, file, ("displayName",))
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
        for i, item in enumerate(data):
            if not isinstance(item, dict):
                continue
            for field_name in fields:
                val = item.get(field_name)
                if isinstance(val, str) and val.strip():
                    self._add(
                        val, file, (i, field_name),
                        force_technical=(
                            field_name == "name"
                            and self._is_referenced_by_script(val)
                        ),
                    )

    def _is_referenced_by_script(self, text: str) -> bool:
        """True if a DB name is mentioned in scripts/plugin commands/comments.

        RPG Maker plugins often compare database names as literal strings. If a
        translated name changes, the JSON remains valid but story progression can
        break. Exact substring matching is intentionally conservative: if scripts
        mention the same DB name, keep that name untouched.
        """
        if not text or len(text.strip()) < 2:
            return False
        refs = self._get_script_reference_text()
        return text.strip() in refs

    def _get_script_reference_text(self) -> str:
        if self._script_reference_text is not None:
            return self._script_reference_text

        chunks: list[str] = []

        def add_scalars(value: Any) -> None:
            if isinstance(value, str):
                chunks.append(value)
            elif isinstance(value, (int, float)):
                chunks.append(str(value))
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
        for name in ("CommonEvents.json", "Troops.json"):
            if (self.data_dir / name).exists():
                scan_obj(self._read(name))

        # Plugin and engine JS can also contain literal DB names. We only read
        # text files near the selected data folder and cap very large files.
        js_dir = self.data_dir.parent / "js"
        if js_dir.exists():
            for js_path in js_dir.rglob("*.js"):
                try:
                    if js_path.stat().st_size > 2_000_000:
                        continue
                    chunks.append(js_path.read_text(encoding="utf-8", errors="ignore"))
                except OSError:
                    continue

        self._script_reference_text = "\n".join(chunks)
        return self._script_reference_text

    def _extract_system(self) -> None:
        file = "System.json"
        data = self._read(file)
        if not isinstance(data, dict):
            return

        for f in SYSTEM_STRING_FIELDS:
            val = data.get(f)
            if isinstance(val, str) and val.strip():
                self._add(val, file, (f,))

        for f in SYSTEM_LIST_FIELDS:
            arr = data.get(f)
            if isinstance(arr, list):
                for i, v in enumerate(arr):
                    if isinstance(v, str) and v.strip():
                        self._add(v, file, (f, i))

        terms = data.get("terms")
        if isinstance(terms, dict):
            for tl in SYSTEM_TERMS_LISTS:
                arr = terms.get(tl)
                if isinstance(arr, list):
                    for i, v in enumerate(arr):
                        if isinstance(v, str) and v.strip():
                            self._add(v, file, ("terms", tl, i))
            messages = terms.get("messages")
            if isinstance(messages, dict):
                for key, val in messages.items():
                    if isinstance(val, str) and val.strip():
                        self._add(val, file, ("terms", "messages", key))

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
            {'total': N, 'with_codes': M, 'broken': K, 'broken_entries': [...]}
        где broken — строки, в которых переводчик потерял управляющие коды.
        """
        stats = {
            "total": 0,
            "with_codes": 0,
            "broken": 0,
            "skipped": 0,
            "broken_entries": [],
        }

        # Группируем по файлам
        by_file: dict[str, list[tuple[TextEntry, str]]] = {}
        for idx, translated in translations.items():
            if idx >= len(self.entries):
                continue
            entry = self.entries[idx]
            stats["total"] += 1

            # Валидация плейсхолдеров перед восстановлением. Проверяем и строки
            # без исходных кодов: чужой <tN/> мог переехать сюда из соседней
            # реплики. Повреждённый перевод не записываем вообще — английская
            # исходная строка безопаснее видимого служебного мусора в игре.
            ok, missing = validate_placeholders(translated, len(entry.codes))
            if entry.codes:
                stats["with_codes"] += 1
            if not ok:
                stats["broken"] += 1
                stats["skipped"] += 1
                if len(stats["broken_entries"]) < 50:
                    stats["broken_entries"].append({
                        "file": entry.file,
                        "missing": sorted(missing),
                        "text": translated[:80],
                    })
                continue

            final = restore_codes(translated, entry.codes)
            by_file.setdefault(entry.file, []).append((entry, final))

        for file, items in by_file.items():
            data = self._read(file)
            if data is None:
                continue
            for entry, value in items:
                _set_by_path(data, entry.path, value)
            self._write(file, data)

        return stats


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


# Обычный перенос строки внутри сообщения. Раньше использовался видимый
# Record Separator (␞), но машинные переводчики иногда удаляют или перемещают
# его. Из-за этого блок невозможно корректно разложить обратно по строкам.
JOIN_SEP = "\n"
GROUP_TAG = "rpgline"


def _wrap_group_part(index: int, text: str) -> str:
    """Оборачивает одну реплику в тег, сохраняемый HTML-режимом переводчиков."""
    return f'<{GROUP_TAG} data-i="{index}">{text}</{GROUP_TAG}>'


def build_translation_units(entries: list[TextEntry],
                            group_dialogues: bool = True) -> list[TranslationUnit]:
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
            ))
            i = j
        else:
            units.append(TranslationUnit(
                entry_indices=[i],
                combined_text=e.text,
                separator=JOIN_SEP,
                context=_build_unit_context(entries, i, i + 1),
            ))
            i += 1
    return units


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


def _build_unit_context(entries: list[TextEntry], start: int, end: int,
                        window: int = 3, limit: int = 1200) -> str:
    """Создаёт компактный контекст вокруг unit без изменения переводимого текста."""
    if not entries:
        return ""
    file = entries[start].file if 0 <= start < len(entries) else ""

    def readable(e: TextEntry) -> str:
        text = clean_for_detection(e.text)
        return re.sub(r'\s+', ' ', text).strip()

    prev_lines: list[str] = []
    i = start - 1
    while i >= 0 and len(prev_lines) < window:
        e = entries[i]
        if e.file != file:
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
        if e.file != file:
            break
        text = readable(e)
        if text:
            next_lines.append(text)
        i += 1

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
                    crypto=None, i18n_field: str | None = None
                    ) -> tuple[ProjectStats, RPGMakerProject]:
    """Парсит проект и возвращает статистику. Без перевода и без записи.
    Если файлы зашифрованы, передай экземпляр GameCrypto.
    i18n_field — поле в I18NTexts.json для маршрута (например 'en_US')."""
    from core.lang_detect import detect_language

    proj = RPGMakerProject(data_dir, crypto=crypto, i18n_field=i18n_field)
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
