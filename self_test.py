from __future__ import annotations

import json
import tempfile
from pathlib import Path

from core.rpgmaker_parser import (
    RPGMakerProject,
    TextEntry,
    build_translation_units,
    is_probably_technical_text,
    protect_codes,
    restore_codes,
    split_translated_unit,
    validate_placeholders,
)
from core.text_layout import install_runtime_text_wrap


def assert_equal(actual, expected, label: str) -> None:
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")


def test_code_roundtrip() -> None:
    source = r"\C[1]<WordWrap>Hello %1 \N<Actor>\n<name:foo>"
    protected, codes = protect_codes(source)
    assert_equal(len(codes), 5, "protected code count")
    translated = protected.replace("Hello", "Привет")
    assert_equal(restore_codes(translated, codes), source.replace("Hello", "Привет"), "roundtrip")


def test_duplicate_placeholder_is_dropped() -> None:
    source = r"\V[1] Gold"
    protected, codes = protect_codes(source)
    translated = protected + " " + protected
    ok, missing = validate_placeholders(translated, len(codes))
    assert_equal(ok, False, "duplicate placeholder validation")
    assert_equal(missing, set(), "duplicate placeholder missing set")
    assert_equal(restore_codes(translated, codes), r"\V[1] Gold  Gold", "duplicate placeholder restore")


def test_bare_plugin_codes_are_protected() -> None:
    source = "\\c[8]Name\\c\\nText\\w"
    protected, codes = protect_codes(source)
    assert_equal(codes, [r"\c[8]", r"\c", r"\n", r"\w"], "bare control codes")
    assert_equal(restore_codes(protected, codes), source, "bare code roundtrip")


def test_group_boundaries_survive_translator_line_wraps() -> None:
    entries = [
        TextEntry("<t0/>Alice\nHello", [r"\C[1]"], "Map001.json", (0,), "g", True),
        TextEntry("<t0/>Bob\nHow are you?", [r"\C[2]"], "Map001.json", (1,), "g", True),
    ]
    unit = build_translation_units(entries, group_dialogues=True)[0]
    assert_equal(unit.tagged, True, "tagged dialogue unit")
    translated = (
        '<rpgline data-i="0"><t0 />Алиса\nПривет!</rpgline>'
        '<rpgline data-i="1"><t0 />Боб\nКак дела?</rpgline>'
    )
    parts = split_translated_unit(unit, translated)
    assert_equal(parts[0], "<t0 />Алиса\nПривет!", "first tagged part")
    assert_equal(parts[1], "<t0 />Боб\nКак дела?", "second tagged part")

    try:
        split_translated_unit(unit, "<t0/>границы потеряны")
    except ValueError:
        pass
    else:
        raise AssertionError("broken group tags must stop assembly")


def test_technical_detection() -> None:
    technical = [
        "$gameSwitches.value(12)",
        "Quest_Flag_01",
        "img/pictures/TitleLogo",
        "DataManager.loadDatabase",
        "HP",
    ]
    for text in technical:
        assert_equal(is_probably_technical_text(text), True, f"technical {text}")

    translatable = ["Iron Sword", "Potion", "The door is locked.", "Dreamland"]
    for text in translatable:
        assert_equal(is_probably_technical_text(text), False, f"translatable {text}")


def test_project_write_keeps_system_markers() -> None:
    with tempfile.TemporaryDirectory() as td:
        data_dir = Path(td)
        (data_dir / "System.json").write_text(json.dumps({
            "gameTitle": "Test Game",
            "currencyUnit": "G",
            "switches": ["", "Quest_Flag_01"],
            "variables": ["", "HeroName"],
            "armorTypes": ["", "Light Armor"],
            "terms": {
                "basic": ["Level", "Lv"],
                "commands": ["Fight"],
                "params": ["HP"],
                "messages": {"obtainGold": "%1 gold obtained!"}
            },
        }), encoding="utf-8")
        (data_dir / "Items.json").write_text(json.dumps([
            None,
            {"name": "Potion", "description": r"Heals \V[1] HP. <WordWrap>"},
            {"name": "Story Key", "description": "Opens a sealed door."}
        ]), encoding="utf-8")
        (data_dir / "CommonEvents.json").write_text(json.dumps([
            None,
            {
                "name": "GateCheck",
                "list": [
                    {"code": 355, "parameters": ["if ($dataItems[2].name === 'Story Key') {" ]},
                    {"code": 655, "parameters": ["  $gameSwitches.setValue(1, true);"]},
                    {"code": 655, "parameters": ["}"]},
                ],
            },
        ]), encoding="utf-8")

        project = RPGMakerProject(data_dir)
        entries = project.extract_all()
        texts = {tuple(e.path): e for e in entries}

        assert ("switches", 1) not in texts
        assert ("variables", 1) not in texts
        assert_equal(texts[(1, "name")].needs_translation, True, "unreferenced item name translatable")
        assert_equal(texts[(1, "description")].needs_translation, True, "item description translatable")
        assert_equal(texts[(2, "name")].needs_translation, False, "script-referenced item name technical")
        assert_equal(texts[(2, "description")].needs_translation, True, "referenced item description translatable")
        assert_equal(texts[("terms", "params", 0)].needs_translation, False, "HP technical")

        translations = {}
        for idx, entry in enumerate(entries):
            if entry.needs_translation:
                translations[idx] = entry.text.replace("Potion", "Зелье").replace("Heals", "Лечит")
        stats = project.apply_translations(translations)
        assert_equal(stats["broken"], 0, "placeholder integrity")

        system = json.loads((data_dir / "System.json").read_text(encoding="utf-8"))
        item = json.loads((data_dir / "Items.json").read_text(encoding="utf-8"))[1]
        assert_equal(system["switches"][1], "Quest_Flag_01", "switch untouched")
        assert_equal(system["variables"][1], "HeroName", "variable untouched")
        assert_equal(item["description"], r"Лечит \V[1] HP. <WordWrap>", "item description")


def test_runtime_text_wrap_installer() -> None:
    with tempfile.TemporaryDirectory() as td:
        www = Path(td) / "www"
        data = www / "data_translated"
        plugins = www / "js" / "plugins"
        data.mkdir(parents=True)
        plugins.mkdir(parents=True)
        (www / "js" / "rpg_windows.js").write_text("// MV", encoding="utf-8")
        original = (
            '<script type="text/javascript" src="js/plugins.js"></script>\n'
            '<script type="text/javascript" src="js/main.js"></script>\n'
        )
        (www / "index.html").write_text(original, encoding="utf-8")

        first = install_runtime_text_wrap(data)
        assert_equal(first.changed, True, "first autowrap install changed")
        installed = (www / "index.html").read_text(encoding="utf-8")
        assert_equal(installed.count("RPGMAKER_TRANSLATOR_AUTOWRAP"), 1, "registration count")
        assert_equal(installed.count('"name":"Translator_AutoWrap"'), 1, "plugin registration")
        assert_equal(first.plugin_path.is_file(), True, "autowrap plugin exists")
        assert_equal(first.backup_path.is_file(), True, "index backup exists")
        assert_equal(first.backup_path.read_text(encoding="utf-8"), original, "index backup")

        second = install_runtime_text_wrap(data)
        assert_equal(second.changed, False, "second autowrap install idempotent")
        installed_again = (www / "index.html").read_text(encoding="utf-8")
        assert_equal(
            installed_again.count("RPGMAKER_TRANSLATOR_AUTOWRAP"),
            1,
            "idempotent registration count",
        )


def main() -> None:
    test_code_roundtrip()
    test_duplicate_placeholder_is_dropped()
    test_bare_plugin_codes_are_protected()
    test_group_boundaries_survive_translator_line_wraps()
    test_technical_detection()
    test_project_write_keeps_system_markers()
    test_runtime_text_wrap_installer()
    print("self_test: OK")


if __name__ == "__main__":
    main()
