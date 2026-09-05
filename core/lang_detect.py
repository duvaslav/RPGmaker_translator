"""
Локальное определение языка строки по Unicode-диапазонам.
Без сетевых API, без внешних библиотек — мгновенно работает.

Поддерживаются основные языки RPG Maker-игр:
- ja (японский: хирагана, катакана, плюс кандзи если без хангыля)
- ko (корейский: хангыль)
- zh (китайский: только кандзи/ханьцзы, без хираганы/катаканы)
- ru (русский: кириллица)
- en (английский / латиница без диакритики)
- mixed (смешанный — например, английская реплика с японскими именами в \\N<...>)
- unknown (не удалось определить — пустые строки, только знаки препинания)
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass


# ────────────────────────────────────────────────────────────────────────────
# Диапазоны Unicode
# ────────────────────────────────────────────────────────────────────────────

HIRAGANA = (0x3040, 0x309F)
KATAKANA = (0x30A0, 0x30FF)
# Часть пунктуации тоже в этом диапазоне, но для целей детекции это ок
CJK_UNIFIED = (0x4E00, 0x9FFF)     # «иероглифы» — общие для JA и ZH
HANGUL = (0xAC00, 0xD7AF)
HANGUL_JAMO = (0x1100, 0x11FF)
CYRILLIC = (0x0400, 0x04FF)
LATIN_BASIC = (0x0041, 0x007A)     # A-Z и a-z (с дырой посередине, но проверим строго)


def _in_range(ch: int, rng: tuple[int, int]) -> bool:
    return rng[0] <= ch <= rng[1]


def _is_latin_letter(ch: int) -> bool:
    return (0x41 <= ch <= 0x5A) or (0x61 <= ch <= 0x7A)


# ────────────────────────────────────────────────────────────────────────────
# Подсчёт «весов» письменностей
# ────────────────────────────────────────────────────────────────────────────

@dataclass
class ScriptCounts:
    """Сколько символов каждой письменности в строке."""
    hiragana: int = 0
    katakana: int = 0
    cjk: int = 0       # ханьцзы/кандзи
    hangul: int = 0
    cyrillic: int = 0
    latin: int = 0
    other: int = 0     # пунктуация, цифры, пробелы — НЕ считаются «другим письмом»
    total_letters: int = 0  # всё кроме пунктуации/пробелов

    def language(self) -> str:
        """Возвращает код языка строки."""
        if self.total_letters == 0:
            return "unknown"

        # Японский: есть кана (хирагана или катакана), даже если есть кандзи
        has_kana = (self.hiragana + self.katakana) > 0
        # Корейский: есть хангыль
        has_hangul = self.hangul > 0
        # Китайский: только CJK без каны/хангыля
        has_cjk_only = self.cjk > 0 and not has_kana and not has_hangul

        # Подсчёт долей основных «несмешиваемых» письменностей
        scripts_present = sum([
            has_kana,
            has_hangul,
            self.cyrillic > 0,
            self.latin > 0 and not has_cjk_only,
            has_cjk_only,
        ])

        # Если присутствуют несколько разных письменностей — смешанная
        # (например, английский диалог с японскими именами через \N<...>)
        if scripts_present > 1:
            # Но если кана/кандзи доминирует — всё равно JA
            jp_chars = self.hiragana + self.katakana + self.cjk
            if has_kana and jp_chars / self.total_letters >= 0.6:
                return "ja"
            if self.cyrillic / self.total_letters >= 0.6:
                return "ru"
            if self.latin / self.total_letters >= 0.6 and not has_kana and not has_hangul:
                return "en"
            return "mixed"

        if has_kana:
            return "ja"
        if has_hangul:
            return "ko"
        if has_cjk_only:
            return "zh"
        if self.cyrillic > 0:
            return "ru"
        if self.latin > 0:
            return "en"
        return "unknown"


def count_scripts(text: str) -> ScriptCounts:
    """Считает по символам, к какой письменности что относится."""
    c = ScriptCounts()
    for ch in text:
        cp = ord(ch)
        # Пропускаем не-буквы: пробелы, пунктуацию, цифры, эмодзи и т.д.
        # Здесь категория «буква» определяется только по нашим диапазонам.
        if _in_range(cp, HIRAGANA):
            c.hiragana += 1
            c.total_letters += 1
        elif _in_range(cp, KATAKANA):
            c.katakana += 1
            c.total_letters += 1
        elif _in_range(cp, CJK_UNIFIED):
            c.cjk += 1
            c.total_letters += 1
        elif _in_range(cp, HANGUL) or _in_range(cp, HANGUL_JAMO):
            c.hangul += 1
            c.total_letters += 1
        elif _in_range(cp, CYRILLIC):
            c.cyrillic += 1
            c.total_letters += 1
        elif _is_latin_letter(cp):
            c.latin += 1
            c.total_letters += 1
        else:
            c.other += 1
    return c


def detect_language(text: str) -> str:
    """Главная функция: определить язык строки."""
    return count_scripts(text).language()


# ────────────────────────────────────────────────────────────────────────────
# Анализ списка строк
# ────────────────────────────────────────────────────────────────────────────

LANG_NAMES = {
    "ja": "Японский",
    "en": "Английский",
    "ru": "Русский",
    "ko": "Корейский",
    "zh": "Китайский",
    "mixed": "Смешанный",
    "unknown": "Неопределён",
}


@dataclass
class LanguageStats:
    """Сводка по языкам."""
    counts: dict[str, int]        # язык → количество строк
    chars: dict[str, int]         # язык → количество символов

    def total_strings(self) -> int:
        return sum(self.counts.values())

    def total_chars(self) -> int:
        return sum(self.chars.values())


def analyze_languages(texts: list[str]) -> LanguageStats:
    """Считает распределение языков по списку строк."""
    counts: Counter = Counter()
    chars: Counter = Counter()
    for t in texts:
        if not t or not t.strip():
            continue
        lang = detect_language(t)
        counts[lang] += 1
        chars[lang] += len(t)
    return LanguageStats(counts=dict(counts), chars=dict(chars))
