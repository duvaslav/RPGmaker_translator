"""
Предпросмотр перевода: берёт случайные отрывки диалогов из переведённого
проекта и показывает их рядом с оригиналом, подсвечивая возможные проблемы
с управляющими кодами.

Используется кнопкой «Предпросмотр» в GUI после (или во время) перевода —
позволяет визуально проверить качество, не запуская игру.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from pathlib import Path

from core.rpgmaker_parser import (
    RPGMakerProject, count_placeholders, validate_placeholders, restore_codes,
)


def _position_key(entry) -> tuple:
    """Стабильный ключ позиции для сопоставления оригинала с переводом.

    У блоков сообщений `path` указывает на весь список команд события, поэтому
    несколько окон в одном событии имеют одинаковый path. Различаем их по
    индексу первой команды блока.
    """
    block = getattr(entry, "block", None)
    if block is not None:
        return (entry.file, entry.path, block.first)
    return (entry.file, entry.path, None)


def _unescape(text: str) -> str:
    """Расэкранирование для показа — та же реализация, что и в парсере."""
    from core.rpgmaker_parser import _unescape_html_entities
    return _unescape_html_entities(text)


@dataclass
class PreviewItem:
    """Один отрывок для предпросмотра."""
    file: str
    original: str          # исходный текст (с кодами, как в исходном файле)
    translated: str        # переведённый текст (с кодами, как в выходном файле)
    codes_ok: bool         # целы ли управляющие коды
    issues: list[str] = field(default_factory=list)   # описания проблем


def build_preview(
    source_dir: str | Path,
    translated_dir: str | Path,
    crypto=None,
    i18n_field: str | None = None,
    sample_size: int = 8,
    seed: int | None = None,
) -> list[PreviewItem]:
    """Строит набор отрывков для предпросмотра.

    Берёт случайные диалоговые строки, сопоставляет оригинал (source_dir) с
    переводом (translated_dir) по позиции в структуре, проверяет коды.

    sample_size — сколько отрывков вернуть.
    seed — для воспроизводимости (None = по-настоящему случайно).
    """
    source_dir = Path(source_dir)
    translated_dir = Path(translated_dir)

    rng = random.Random(seed)

    # Извлекаем диалоги из перевода (это то, что показываем)
    proj_tr = RPGMakerProject(translated_dir, crypto=crypto, i18n_field=i18n_field)
    tr_entries = proj_tr.extract_all()
    tr_dialogues = [e for e in tr_entries if e.is_dialogue and e.needs_translation]

    if not tr_dialogues:
        # Может, диалогов нет — берём любые переводимые
        tr_dialogues = [e for e in tr_entries if e.needs_translation]

    if not tr_dialogues:
        return []

    # Извлекаем оригиналы и индексируем по позиции
    orig_by_pos = {}
    if source_dir.exists():
        try:
            proj_src = RPGMakerProject(source_dir, crypto=crypto, i18n_field=i18n_field)
            src_entries = proj_src.extract_all()
            for e in src_entries:
                orig_by_pos[_position_key(e)] = e
        except Exception:
            pass

    # Выбираем случайные отрывки
    sample = rng.sample(tr_dialogues, min(sample_size, len(tr_dialogues)))

    items: list[PreviewItem] = []
    for tr_entry in sample:
        src_entry = orig_by_pos.get(_position_key(tr_entry))

        # Восстанавливаем коды для отображения и расэкранируем html-сущности
        # (&lt; &gt; &amp;), которые могли остаться в protected-тексте.
        translated_display = _unescape(restore_codes(tr_entry.text, tr_entry.codes))
        original_display = ""
        if src_entry:
            original_display = _unescape(restore_codes(src_entry.text, src_entry.codes))

        # Проверка целостности кодов
        issues = []
        codes_ok = True
        if tr_entry.codes:
            ok, missing = validate_placeholders(tr_entry.text, len(tr_entry.codes))
            if not ok:
                codes_ok = False
                issues.append(
                    f"Потеряны управляющие коды (индексы {sorted(missing)})"
                )

        # Сравнение количества кодов оригинала и перевода
        if src_entry and len(src_entry.codes) != len(tr_entry.codes):
            codes_ok = False
            issues.append(
                f"Разное число кодов: оригинал {len(src_entry.codes)}, "
                f"перевод {len(tr_entry.codes)}"
            )

        # Проверка на «сырые» плейсхолдеры, оставшиеся в тексте (не должно быть
        # после restore — но если переводчик их размножил, заметим)
        leftover = count_placeholders(translated_display)
        if leftover:
            codes_ok = False
            issues.append(f"В переводе остались сырые плейсхолдеры: {sorted(leftover)}")

        items.append(PreviewItem(
            file=tr_entry.file,
            original=original_display,
            translated=translated_display,
            codes_ok=codes_ok,
            issues=issues,
        ))

    return items


def preview_summary(items: list[PreviewItem]) -> str:
    """Краткая текстовая сводка для лога."""
    if not items:
        return "Нет диалогов для предпросмотра."
    ok = sum(1 for i in items if i.codes_ok)
    return f"Показано {len(items)} отрывков, коды целы в {ok}/{len(items)}."
