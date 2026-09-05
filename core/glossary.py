"""Глоссарий: имена собственные и термины, которые нельзя отдавать переводчику.

Зачем
─────
Машинный перевод переводит имена как обычные слова. В реальном прогоне этой
утилиты персонаж ``Airy`` превратился в «Воздушный», а ``Panty Flash`` — во
«флэш-память». Ни один провайдер сам по себе этого не избежит: он не знает,
что перед ним имя.

Как работает
────────────
Термин глоссария защищается тем же механизмом, что и управляющие коды: перед
отправкой он заменяется плейсхолдером ``<tN/>``, а после перевода на его место
подставляется нужная форма. Переводчик термин вообще не видит.

Файл ``_glossary.json`` лежит в выходной папке рядом с кэшем и правится руками::

    {
      "version": 1,
      "terms": {
        "Airy": "Айри",
        "Natsubo": "Нацубо",
        "Break Time": "Перемена",
        "Panty Flash": ""
      }
    }

Пустая строка означает «оставить как в оригинале».

Имена персонажей подставляются в именительном падеже и не склоняются. Для
русского это осознанный компромисс: «подошёл к Айри» читается несравнимо лучше,
чем «подошёл к Воздушному».
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path


GLOSSARY_FILENAME = "_glossary.json"
GLOSSARY_VERSION = 1

# Слишком короткие или слишком общие термины делают больше вреда, чем пользы:
# защита слова «Item» заморозила бы половину интерфейса.
MIN_TERM_LENGTH = 3

_STOPWORDS = {
    "item", "items", "skill", "skills", "weapon", "weapons", "armor", "armors",
    "state", "states", "enemy", "enemies", "actor", "actors", "class", "classes",
    "gold", "level", "attack", "guard", "escape", "fight", "save", "load",
    "yes", "no", "ok", "new", "game", "exit", "back", "next", "menu",
}


@dataclass
class Glossary:
    """Термины, защищаемые от перевода, и их целевые формы."""

    terms: dict[str, str] = field(default_factory=dict)
    _pattern: re.Pattern | None = field(default=None, init=False, repr=False)

    # ── Загрузка и сохранение ───────────────────────────────────────────────

    @classmethod
    def load(cls, path: str | Path) -> "Glossary":
        path = Path(path)
        if not path.exists():
            return cls()
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except Exception:
            # Битый глоссарий не должен ронять перевод: работаем без него.
            return cls()
        terms = raw.get("terms") if isinstance(raw, dict) else raw
        if not isinstance(terms, dict):
            return cls()
        clean = {
            str(k): str(v or "")
            for k, v in terms.items()
            if isinstance(k, str) and k.strip()
        }
        return cls(terms=clean)

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        payload = {
            "version": GLOSSARY_VERSION,
            "_comment": (
                "Термины не отправляются переводчику. Значение — форма в переводе; "
                "пустая строка = оставить как в оригинале."
            ),
            "terms": dict(sorted(self.terms.items())),
        }
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        tmp.replace(path)

    # ── Работа с терминами ──────────────────────────────────────────────────

    def add(self, source: str, target: str = "") -> bool:
        source = (source or "").strip()
        if not self.is_usable_term(source):
            return False
        if source in self.terms:
            return False
        self.terms[source] = (target or "").strip()
        self._pattern = None
        return True

    @staticmethod
    def is_usable_term(term: str) -> bool:
        term = (term or "").strip()
        if len(term) < MIN_TERM_LENGTH:
            return False
        if term.lower() in _STOPWORDS:
            return False
        # Термин должен содержать буквы, иначе это разметка или число.
        if not re.search(r'[^\W\d_]', term, re.UNICODE):
            return False
        # Не защищаем целые предложения — только имена и короткие термины.
        if len(term) > 48 or term.count(" ") > 3:
            return False
        return True

    def replacement(self, matched: str) -> str:
        """Что подставить вместо термина в переведённом тексте."""
        target = self.terms.get(matched)
        if target:
            return target
        # Регистронезависимое совпадение: ищем каноническую запись.
        lowered = matched.lower()
        for source, value in self.terms.items():
            if source.lower() == lowered:
                return value or matched
        return matched

    def pattern(self) -> re.Pattern | None:
        """Регулярка, ловящая любой термин глоссария целым словом."""
        if self._pattern is not None:
            return self._pattern
        if not self.terms:
            return None
        # Длинные термины первыми: «Break Time» должен выиграть у «Break».
        ordered = sorted(self.terms, key=len, reverse=True)
        parts = []
        for term in ordered:
            escaped = re.escape(term)
            # Границу слова ставим только там, где она осмысленна: для CJK
            # \b не работает, потому что там нет границ слов в смысле regex.
            left = r'(?<![^\W\d_])' if _has_word_edge(term[0]) else ''
            right = r'(?![^\W\d_])' if _has_word_edge(term[-1]) else ''
            parts.append(f'{left}{escaped}{right}')
        self._pattern = re.compile("|".join(parts))
        return self._pattern

    def __len__(self) -> int:
        return len(self.terms)

    def __bool__(self) -> bool:
        return bool(self.terms)


def _has_word_edge(ch: str) -> bool:
    """True для букв/цифр — там граница слова имеет смысл."""
    return bool(re.match(r'[^\W\d_]|\d', ch, re.UNICODE))


# ────────────────────────────────────────────────────────────────────────────
# Автоматическое наполнение
# ────────────────────────────────────────────────────────────────────────────

def build_auto_glossary(data_dir: str | Path,
                        existing: Glossary | None = None) -> Glossary:
    """Собирает кандидатов в глоссарий из базы данных игры.

    Источники, в порядке надёжности:
      1. Имена и прозвища из ``Actors.json`` — это точно имена собственные.
      2. Имена внутри плагин-кода ``\\N<Имя>`` в текстах событий — так плагины
         подписывают говорящего.

    Названия предметов и навыков сюда НЕ попадают: их переводить как раз нужно.
    """
    data_dir = Path(data_dir)
    glossary = existing or Glossary()

    actors = _read_json(data_dir / "Actors.json")
    if isinstance(actors, list):
        for actor in actors:
            if not isinstance(actor, dict):
                continue
            for field_name in ("name", "nickname"):
                glossary.add(actor.get(field_name, ""))

    for name in _scan_speaker_tags(data_dir):
        glossary.add(name)

    return glossary


_SPEAKER_TAG = re.compile(r'\\N<([^>\r\n]{1,48})>')


def _scan_speaker_tags(data_dir: Path) -> set[str]:
    found: set[str] = set()
    files: list[Path] = sorted(data_dir.glob("Map[0-9]*.json"))
    for name in ("CommonEvents.json", "Troops.json"):
        path = data_dir / name
        if path.exists():
            files.append(path)
    for path in files:
        try:
            text = path.read_text(encoding="utf-8-sig", errors="ignore")
        except OSError:
            continue
        for match in _SPEAKER_TAG.finditer(text):
            found.add(match.group(1).strip())
    return found


def _read_json(path: Path):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return None
