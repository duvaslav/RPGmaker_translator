"""Repair projects produced by the pre-boundary-safe dialogue grouper.

The old translator joined many RPG Maker 401 commands with ordinary newlines.
Translation services legitimately reflowed those newlines, after which the
result was split by character count.  That moved dialogue, speaker formatting
and <tN/> control-code placeholders between message windows.

This module rebuilds the translated data from an untouched source directory.
It migrates only cache entries whose boundaries and complete control-code list
can be proven intact, retranslates everything else as independent entries, and
atomically swaps the repaired data directory into the game after validation.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from core.cache import TranslationCache
from core.config import load_config
from core.rpgmaker_parser import (
    RPGMakerProject,
    build_translation_units,
    protect_codes,
    restore_codes,
    validate_placeholders,
)
from core.translators import ChainConfig, TranslationRoute, translate_with_chain


# Exact control-code grammar used before bare \c/\w protection was added.
_LEGACY_CONTROL_CODE_PATTERN = re.compile(
    r'('
    r'\\[A-Za-z][A-Za-z0-9]*\[[^\]]*\]'
    r'|\\[A-Za-z][A-Za-z0-9]*<[^>]*>'
    r'|<\s*[Bb][Rr]\s*/?\s*>'
    r'|<\s*/?\s*[Cc][Oo][Ll][Oo][Rr][^>]*>'
    r'|<[^ \t\r\n<>][^>\r\n]{0,120}>'
    r'|%\d+'
    r'|\\\\'
    r'|\\[.!|<>^$G{}nrt]'
    r')'
)
_PLACEHOLDER = re.compile(r'<\s*t\s*(\d+)\s*/?\s*>', re.IGNORECASE)
_LINE_PREFIX = re.compile(
    r'(?mi)^[ \t]*((?:<\s*t\s*\d+\s*/?\s*>[ \t]*)+)'
)
_CYRILLIC_BACKSLASH = re.compile(r'\\[А-Яа-яЁё]')


def _escape_plain(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _legacy_protect_codes(text: str) -> tuple[str, list[str]]:
    codes: list[str] = []
    parts: list[str] = []
    last = 0
    for match in _LEGACY_CONTROL_CODE_PATTERN.finditer(text):
        parts.append(_escape_plain(text[last:match.start()]))
        codes.append(match.group(0))
        parts.append(f"<t{len(codes) - 1}/>")
        last = match.end()
    parts.append(_escape_plain(text[last:]))
    return "".join(parts), codes


def _get_by_path(data: Any, path: tuple) -> Any:
    value = data
    for key in path:
        value = value[key]
    return value


def _prefix_signature(text: str) -> tuple[int, ...] | None:
    match = re.match(
        r'^[ \t]*((?:<\s*t\s*\d+\s*/?\s*>[ \t]*)+)', text, re.IGNORECASE
    )
    if not match:
        return None
    return tuple(int(x) for x in _PLACEHOLDER.findall(match.group(1)))


def _migrate_translation(
    old_translated: str,
    old_codes: list[str],
    expected_new_codes: list[str],
) -> str | None:
    """Convert a proven old protected result to the new placeholder numbering."""
    ok, _ = validate_placeholders(old_translated, len(old_codes))
    if not ok:
        return None
    raw = restore_codes(old_translated, old_codes)
    protected, translated_codes = protect_codes(raw)
    if translated_codes != expected_new_codes:
        return None
    ok, _ = validate_placeholders(protected, len(expected_new_codes))
    return protected if ok else None


def seed_verified_individual_cache(
    source_dir: Path,
    old_cache_path: Path,
    new_cache_path: Path,
    src: str = "en",
    dst: str = "ru",
) -> dict[str, int]:
    """Reuse only translations whose entry boundaries and codes are unambiguous."""
    old_cache = TranslationCache(old_cache_path)
    new_cache = TranslationCache(new_cache_path)
    project = RPGMakerProject(source_dir)
    project.extract_all()
    project.filter_to_languages({src})
    units = build_translation_units(project.entries, group_dialogues=True)

    raw_files: dict[str, Any] = {}
    legacy: dict[int, tuple[str, list[str]]] = {}
    for index, entry in enumerate(project.entries):
        if entry.file not in raw_files:
            raw_files[entry.file] = json.loads(
                (source_dir / entry.file).read_text(encoding="utf-8-sig")
            )
        raw = _get_by_path(raw_files[entry.file], entry.path)
        legacy[index] = _legacy_protect_codes(raw)

    migrated = rejected = grouped_migrated = 0

    # First migrate legacy single-entry cache records.
    for index, entry in enumerate(project.entries):
        if not entry.needs_translation:
            continue
        old_text, old_codes = legacy[index]
        translated = old_cache.get(src, dst, old_text)
        if translated is None:
            continue
        converted = _migrate_translation(translated, old_codes, entry.codes)
        if converted is None:
            rejected += 1
            continue
        new_cache.set(src, dst, entry.text, converted)
        migrated += 1

    # Then recover old grouped records, but only when every entry has a leading
    # code signature and the translated output contains exactly the same ordered
    # signatures. This deliberately rejects ambiguous narration boundaries.
    for unit in units:
        if len(unit.entry_indices) <= 1:
            continue
        old_texts = [legacy[i][0] for i in unit.entry_indices]
        old_combined = "\n".join(old_texts)
        translated = old_cache.get(src, dst, old_combined)
        if translated is None:
            continue

        expected = [_prefix_signature(text) for text in old_texts]
        matches = list(_LINE_PREFIX.finditer(translated))
        actual = [
            tuple(int(x) for x in _PLACEHOLDER.findall(match.group(1)))
            for match in matches
        ]
        if any(signature is None for signature in expected) or actual != expected:
            rejected += len(unit.entry_indices)
            continue

        parts = [
            translated[match.start(): matches[pos + 1].start()].strip()
            if pos + 1 < len(matches)
            else translated[match.start():].strip()
            for pos, match in enumerate(matches)
        ]
        converted_parts: list[str] = []
        for entry_index, part in zip(unit.entry_indices, parts):
            converted = _migrate_translation(
                part, legacy[entry_index][1], project.entries[entry_index].codes
            )
            if converted is None:
                converted_parts = []
                break
            converted_parts.append(converted)
        if not converted_parts:
            rejected += len(unit.entry_indices)
            continue
        for entry_index, converted in zip(unit.entry_indices, converted_parts):
            entry = project.entries[entry_index]
            new_cache.set(src, dst, entry.text, converted)
            grouped_migrated += 1

    new_cache.save()
    return {
        "source_entries": len(project.entries),
        "migrated_single": migrated,
        "migrated_from_groups": grouped_migrated,
        "rejected_ambiguous_or_broken": rejected,
        "cache_entries": len(new_cache),
    }


def _scan_json_strings(root: Path) -> dict[str, Any]:
    placeholders: list[dict[str, str]] = []
    cyrillic_codes: list[dict[str, str]] = []

    def walk(value: Any, file: str, path: tuple = ()) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                walk(child, file, path + (key,))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, file, path + (index,))
        elif isinstance(value, str):
            record = {"file": file, "path": repr(path), "text": value[:180]}
            if _PLACEHOLDER.search(value):
                placeholders.append(record)
            if _CYRILLIC_BACKSLASH.search(value):
                cyrillic_codes.append(record)

    for file_path in sorted(root.glob("*.json")):
        if file_path.name == "_translation_cache.json":
            continue
        data = json.loads(file_path.read_text(encoding="utf-8-sig"))
        walk(data, file_path.name)
    return {
        "raw_placeholder_strings": placeholders,
        "cyrillic_backslash_strings": cyrillic_codes,
    }


def repair_game(game_dir: Path, install: bool = False) -> dict[str, Any]:
    game_dir = game_dir.resolve()
    www = game_dir / "www"
    current = www / "data"
    source = www / "data_old" / "data"
    old_cache = current / "_translation_cache.json"
    if not (www.is_dir() and current.is_dir() and source.is_dir() and old_cache.is_file()):
        raise FileNotFoundError("Не найдены www/data, www/data_old/data или старый кэш")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    staging = www / f"data_repaired_{stamp}"
    backup = www / f"data_before_dialogue_repair_{stamp}"
    if staging.exists() or backup.exists():
        raise FileExistsError("Путь staging/backup уже существует; повтори запуск через секунду")
    shutil.copytree(source, staging)
    shutil.copy2(old_cache, staging / "_translation_cache.json")

    seed_stats = seed_verified_individual_cache(
        source, old_cache, staging / "_translation_cache.json"
    )

    config = load_config()
    provider = (config.get("last_stage_providers") or ["Yandex"])[0]
    api_key = config.get("api_keys", {}).get(provider, "")
    extra: dict[str, Any] = {}
    if provider == "Yandex":
        extra["folder_id"] = config.get("yandex_folder_id", "")
    # free_tier НЕ передаём: DeepLTranslator сам определит endpoint по суффиксу
    # ключа («:fx» = Free). Жёсткое значение из конфига отправляло Pro-ключ на
    # free-endpoint и ловило 403.

    project = RPGMakerProject(staging)
    source_project = RPGMakerProject(source)
    source_project.extract_all()
    source_project.filter_to_languages({"en"})
    project.entries = source_project.entries
    units = build_translation_units(project.entries, group_dialogues=False)
    cache = TranslationCache(staging / "_translation_cache.json")
    cached_before = sum(cache.has("en", "ru", unit.combined_text) for unit in units)
    missing_chars = sum(
        len(unit.combined_text)
        for unit in units
        if not cache.has("en", "ru", unit.combined_text)
    )

    route = TranslationRoute(src="en", pivot=None, dst="ru")
    chain = ChainConfig(route=route, stage_providers=[(provider, api_key, extra)])
    translated = translate_with_chain(
        [unit.combined_text for unit in units],
        chain,
        batch_size=int(config.get("batch_size", 30)),
        contexts=[unit.context for unit in units],
        cache=cache,
    )
    translations = {
        unit.entry_indices[0]: result
        for unit, result in zip(units, translated)
        if result.strip() or not unit.combined_text.strip()
    }
    apply_stats = project.apply_translations(translations)
    cache.save()
    scan = _scan_json_strings(staging)

    report: dict[str, Any] = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "game": str(game_dir),
        "source": str(source),
        "staging": str(staging),
        "provider": provider,
        "units": len(units),
        "cached_before_translation": cached_before,
        "translated_missing_chars": missing_chars,
        "seed": seed_stats,
        "apply": apply_stats,
        "validation": {
            "raw_placeholder_count": len(scan["raw_placeholder_strings"]),
            "cyrillic_backslash_count": len(scan["cyrillic_backslash_strings"]),
            "raw_placeholder_examples": scan["raw_placeholder_strings"][:20],
            "cyrillic_backslash_examples": scan["cyrillic_backslash_strings"][:20],
        },
        "installed": False,
        "backup": None,
    }

    report_path = game_dir / "translation-repair-report.json"
    if scan["raw_placeholder_strings"] or scan["cyrillic_backslash_strings"]:
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        raise RuntimeError(
            "Проверка staging не пройдена; текущая игра не изменена. "
            f"Смотри {report_path}"
        )

    if install:
        # Exact, validated, same-drive swap with immediate rollback on failure.
        if current.resolve().parent != www.resolve() or current.name != "data":
            raise RuntimeError(f"Небезопасная цель замены: {current}")
        os.replace(current, backup)
        try:
            os.replace(staging, current)
        except Exception:
            os.replace(backup, current)
            raise
        report["installed"] = True
        report["backup"] = str(backup)
        report["staging"] = None

    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Repair legacy dialogue boundary corruption")
    parser.add_argument("--game", required=True, type=Path, help="Game directory containing www")
    parser.add_argument("--install", action="store_true", help="Swap validated repaired data into game")
    args = parser.parse_args()
    result = repair_game(args.game, install=args.install)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
