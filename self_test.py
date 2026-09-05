"""Самопроверка без сети и без GUI: `python self_test.py`.

Помимо базовых инвариантов здесь закреплены регрессии на реально найденные
ошибки — каждая помечена комментарием, что именно она ловит.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from core.glossary import Glossary
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
from core.safety import UnsafeOutputDir, check_output_dir
from core.text_fit import (
    EscapeResolver,
    MessageLayout,
    TextMeasurer,
    fit_message,
    unwrap_message,
)
from core.text_layout import install_runtime_text_wrap
from core.translators import _keep_entities, _placeholders_intact


def assert_equal(actual, expected, label: str) -> None:
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")


def assert_true(value, label: str) -> None:
    if not value:
        raise AssertionError(f"{label}: expected truthy, got {value!r}")


def _measurer(engine: str = "MV") -> TextMeasurer:
    layout = MessageLayout(engine=engine, padding=18 if engine == "MV" else 12,
                           font_size=28 if engine == "MV" else 26)
    resolver = EscapeResolver()
    resolver.actors = ["", "Кайто"]
    return TextMeasurer(layout, resolver)


# ────────────────────────────────────────────────────────────────────────────
# Защита управляющих кодов
# ────────────────────────────────────────────────────────────────────────────

def test_code_roundtrip() -> None:
    source = r"\C[1]<WordWrap>Hello %1 \N<Actor>\n<name:foo>"
    protected, codes = protect_codes(source)
    assert_equal(len(codes), 5, "protected code count")
    translated = protected.replace("Hello", "Привет")
    assert_equal(restore_codes(translated, codes), source.replace("Hello", "Привет"),
                 "roundtrip")


def test_duplicate_placeholder_is_dropped() -> None:
    source = r"\V[1] Gold"
    protected, codes = protect_codes(source)
    translated = protected + " " + protected
    ok, missing = validate_placeholders(translated, len(codes))
    assert_equal(ok, False, "duplicate placeholder validation")
    assert_equal(missing, set(), "duplicate placeholder missing set")
    assert_equal(restore_codes(translated, codes), r"\V[1] Gold  Gold",
                 "duplicate placeholder restore")


def test_bare_plugin_codes_are_protected() -> None:
    source = "\\c[8]Name\\c\\nText\\w"
    protected, codes = protect_codes(source)
    assert_equal(codes, [r"\c[8]", r"\c", r"\n", r"\w"], "bare control codes")
    assert_equal(restore_codes(protected, codes), source, "bare code roundtrip")


def test_html_entities_survive_one_unescape() -> None:
    """Регрессия: расэкранирование шло дважды и съедало «&amp;».

    Строка, где в игре реально лежит «Bread &amp; Butter», превращалась в
    «Bread & Butter» — то есть в другой текст.
    """
    for source in ("Bread &amp; Butter", "5 &lt; 10 & 3 > 1", "a &nbsp; b"):
        protected, codes = protect_codes(source)
        from_provider = _keep_entities(protected)          # провайдер вернул как есть
        assert_equal(restore_codes(from_provider, codes), source,
                     f"entity roundtrip: {source}")


def test_broken_translation_is_not_cached() -> None:
    """Регрессия: битый ответ попадал в кэш навсегда и строка не переводилась."""
    assert_true(_placeholders_intact("<t0/>Hi <t1/>", "Привет <t0/><t1/>"), "intact")
    assert_equal(_placeholders_intact("<t0/>Hi <t1/>", "Привет <t0/>"), False,
                 "missing placeholder rejected")
    assert_equal(_placeholders_intact("Hi", "Привет <t3/>"), False,
                 "stray placeholder rejected")


# ────────────────────────────────────────────────────────────────────────────
# Глоссарий
# ────────────────────────────────────────────────────────────────────────────

def test_glossary_protects_names() -> None:
    """«Airy» не должен уходить в переводчик и становиться «Воздушным»."""
    glossary = Glossary()
    glossary.add("Airy", "Айри")
    glossary.add("Natsubo", "Нацубо")

    protected, codes = protect_codes("Airy saw Natsubo there.", glossary)
    assert_true("Airy" not in protected, "name hidden from translator")
    assert_true("Natsubo" not in protected, "second name hidden")
    assert_equal(codes, ["Айри", "Нацубо"], "glossary replacements stored")

    translated = protected.replace("saw", "увидела").replace("there.", "там.")
    assert_equal(restore_codes(translated, codes), "Айри увидела Нацубо там.",
                 "glossary substitution")


def test_glossary_matches_whole_words_only() -> None:
    glossary = Glossary()
    glossary.add("Air", "Воздух")
    protected, codes = protect_codes("Airy is not Air.", glossary)
    assert_equal(len(codes), 1, "only standalone word matched")
    assert_true("Airy" in protected, "longer word untouched")


def test_glossary_rejects_useless_terms() -> None:
    glossary = Glossary()
    assert_equal(glossary.add("HP"), False, "too short")
    assert_equal(glossary.add("item"), False, "stopword")
    assert_equal(glossary.add("123"), False, "no letters")
    assert_equal(len(glossary), 0, "nothing added")


# ────────────────────────────────────────────────────────────────────────────
# Вёрстка сообщений
# ────────────────────────────────────────────────────────────────────────────

def test_unwrap_joins_layout_breaks_only() -> None:
    """Ключевая проверка качества перевода.

    Перенос посреди фразы склеивается (иначе «Trigger» и «Condition:» уходят
    в переводчик по отдельности), а авторский перенос после точки — нет.
    """
    measurer = _measurer("MV")
    source = (
        "\\c[26]~Link Event 1 (Panty Flash Discovery)~\\c Trigger\n"
        "Condition: \\c[4]Break Time\\c \\c[8]\\N[1]\\c accidentally catches a\n"
        "glimpse of \\c[13]Natsubo\\c's panties... What action does\n"
        "\\c[2]Airy\\c, who saw him looking, take...?"
    )
    paragraphs = unwrap_message(source, measurer)
    assert_equal(len(paragraphs), 1, "wrapped lines joined into one paragraph")
    assert_true("Trigger Condition:" in paragraphs[0], "split phrase reunited")
    assert_true("catches a glimpse of" in paragraphs[0], "idiom reunited")


def test_unwrap_keeps_authored_breaks() -> None:
    measurer = _measurer("MV")
    source = (
        "\\c[8]\\N[1]\\c\n"
        "(It's unlocked... Did someone forget to lock it...?\n"
        "No... That guy...)"
    )
    paragraphs = unwrap_message(source, measurer)
    assert_equal(len(paragraphs), 3, "speaker line and sentences stay separate")


def test_wrapped_lines_fit_the_window() -> None:
    measurer = _measurer("MV")
    text = ("\\c[26]~Событие 1 (Случайный взгляд под юбку)~\\c Условие срабатывания: "
            "\\c[4]Перемена\\c \\c[8]\\N[1]\\c случайно замечает трусики "
            "\\c[13]Нацубо\\c... Как поступит \\c[2]Айри\\c, заметившая его взгляд...?")
    for has_face in (False, True):
        limit = measurer.layout.available_width(has_face)
        fitted = fit_message(text, measurer, has_face=has_face)
        for page in fitted.pages:
            assert_true(len(page) <= measurer.layout.max_lines,
                        f"page fits row limit (face={has_face})")
            for line in page:
                assert_true(measurer.width(line) <= limit,
                            f"line width {measurer.width(line):.0f} <= {limit}")


def test_pagination_prefers_sentence_boundaries() -> None:
    measurer = _measurer("MV")
    text = ("Первое предложение достаточно длинное, чтобы занять сразу две строки "
            "в окне сообщения игры и не поместиться в одну. Второе предложение "
            "тоже длинное и тоже займёт целых две строки в этом же окне. "
            "Третье предложение вынуждено переехать в следующее окно, потому что "
            "места в первом уже не осталось совсем. Четвёртое закрывает текст.")
    fitted = fit_message(text, measurer)
    assert_true(len(fitted.pages) > 1, "long text needs more than one window")
    for page in fitted.pages[:-1]:
        assert_true(page[-1].rstrip().endswith((".", "!", "?", "…")),
                    f"page ends on a sentence: {page[-1]!r}")


def test_control_codes_stay_with_their_word() -> None:
    measurer = _measurer("MV")
    text = "Слово " * 30 + "\\c[2]финал\\c"
    fitted = fit_message(text, measurer)
    for page in fitted.pages:
        for line in page:
            assert_equal(line.rstrip().endswith("\\c[2]"), False,
                         "colour code never dangles at end of line")


# ────────────────────────────────────────────────────────────────────────────
# Разбор проекта
# ────────────────────────────────────────────────────────────────────────────

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


def _write_sample_project(data_dir: Path) -> None:
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
            "messages": {"obtainGold": "%1 gold obtained!"},
        },
    }), encoding="utf-8")
    (data_dir / "Items.json").write_text(json.dumps([
        None,
        {"name": "Potion", "description": r"Heals \V[1] HP. <WordWrap>"},
        {"name": "Story Key", "description": "Opens a sealed door."},
    ]), encoding="utf-8")
    (data_dir / "CommonEvents.json").write_text(json.dumps([
        None,
        {
            "name": "GateCheck",
            "list": [
                {"code": 355, "parameters": ["if ($dataItems[2].name === 'Story Key') {"]},
                {"code": 655, "parameters": ["  $gameSwitches.setValue(1, true);"]},
                {"code": 655, "parameters": ["}"]},
            ],
        },
    ]), encoding="utf-8")


def test_project_write_keeps_system_markers() -> None:
    with tempfile.TemporaryDirectory() as td:
        data_dir = Path(td)
        _write_sample_project(data_dir)

        project = RPGMakerProject(data_dir)
        entries = project.extract_all()
        texts = {tuple(e.path): e for e in entries}

        assert ("switches", 1) not in texts
        assert ("variables", 1) not in texts
        assert_equal(texts[(1, "name")].needs_translation, True,
                     "unreferenced item name translatable")
        assert_equal(texts[(1, "description")].needs_translation, True,
                     "item description translatable")
        assert_equal(texts[(2, "name")].needs_translation, False,
                     "script-referenced item name technical")
        assert_equal(texts[(2, "description")].needs_translation, True,
                     "referenced item description translatable")
        assert_equal(texts[("terms", "params", 0)].needs_translation, False,
                     "HP technical")

        translations = {}
        for idx, entry in enumerate(entries):
            if entry.needs_translation:
                translations[idx] = entry.text.replace("Potion", "Зелье").replace(
                    "Heals", "Лечит")
        stats = project.apply_translations(translations)
        assert_equal(stats["broken"], 0, "placeholder integrity")

        system = json.loads((data_dir / "System.json").read_text(encoding="utf-8"))
        item = json.loads((data_dir / "Items.json").read_text(encoding="utf-8"))[1]
        assert_equal(system["switches"][1], "Quest_Flag_01", "switch untouched")
        assert_equal(system["variables"][1], "HeroName", "variable untouched")
        assert_equal(item["description"], r"Лечит \V[1] HP. <WordWrap>", "item description")


def test_script_reference_ignores_comments() -> None:
    """Регрессия: поиск подстроки помечал техническим любое слово из справки плагина."""
    with tempfile.TemporaryDirectory() as td:
        data_dir = Path(td) / "data"
        data_dir.mkdir(parents=True)
        (data_dir / "Items.json").write_text(json.dumps([
            None, {"name": "Poison", "description": "A nasty brew."},
        ]), encoding="utf-8")
        js_dir = Path(td) / "js"
        js_dir.mkdir()
        (js_dir / "plugin.js").write_text(
            "/*: @help This plugin handles Poison effects nicely. */\n"
            "var x = 1;\n", encoding="utf-8")

        project = RPGMakerProject(data_dir)
        entries = {tuple(e.path): e for e in project.extract_all()}
        assert_equal(entries[(1, "name")].needs_translation, True,
                     "name mentioned only in a comment stays translatable")


def test_message_block_is_rewrapped_into_windows() -> None:
    """Сквозная проверка: один блок 401 → связный текст → окна по 4 строки."""
    with tempfile.TemporaryDirectory() as td:
        data_dir = Path(td)
        (data_dir / "Map001.json").write_text(json.dumps({
            "events": [None, {"id": 1, "name": "E", "pages": [{"list": [
                {"code": 101, "indent": 0, "parameters": ["", 0, 0, 2]},
                {"code": 401, "indent": 0, "parameters": [
                    "This is the first half of a sentence that was\n"
                    "hand wrapped by the author to fit the window."]},
                {"code": 0, "indent": 0, "parameters": []},
            ]}]}],
        }), encoding="utf-8")

        project = RPGMakerProject(data_dir)
        entries = project.extract_all()
        messages = [e for e in entries if e.is_message]
        assert_equal(len(messages), 1, "one message block")
        # Авторский перенос склеен — переводчик увидит целое предложение.
        assert_true("that was hand wrapped" in messages[0].text, "unwrapped for API")

        index = entries.index(messages[0])
        long_ru = ("Это очень длинное предложение на русском языке, которое "
                   "заведомо не помещается в одно окно сообщения и потому должно "
                   "быть аккуратно разложено движком на несколько окон. "
                   "Второе предложение здесь тоже присутствует. "
                   "И третье предложение для верности.")
        project.apply_translations({index: long_ru})

        data = json.loads((data_dir / "Map001.json").read_text(encoding="utf-8"))
        commands = data["events"][1]["pages"][0]["list"]
        headers = [c for c in commands if c["code"] == 101]
        lines = [c["parameters"][0] for c in commands if c["code"] == 401]
        assert_true(len(headers) > 1, "long text split into several windows")
        assert_equal(commands[-1]["code"], 0, "terminator preserved")

        measurer = project.measurer
        limit = measurer.layout.available_width()
        for line in lines:
            assert_true(measurer.width(line) <= limit, f"line fits window: {line!r}")

        # Ни одно слово не потеряно при пересборке.
        assert_equal(" ".join(lines).split(), long_ru.split(),
                     "no words lost or duplicated")


# ────────────────────────────────────────────────────────────────────────────
# Безопасность путей
# ────────────────────────────────────────────────────────────────────────────

def test_output_dir_guard() -> None:
    """Регрессия: выбор `www` выходной папкой удалял игру вместе с исходником."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        source = root / "www" / "data"
        source.mkdir(parents=True)
        (source / "System.json").write_text("{}", encoding="utf-8")
        foreign = root / "documents"
        foreign.mkdir()
        (foreign / "thesis.docx").write_text("x", encoding="utf-8")

        dangerous = [
            (root / "www", "output contains source"),
            (source / "out", "output inside source"),
            (source, "output equals source"),
            (foreign, "output holds unrelated files"),
        ]
        for target, label in dangerous:
            try:
                check_output_dir(source, target)
            except UnsafeOutputDir:
                continue
            raise AssertionError(f"must be rejected: {label}")

        # Нормальный вариант и повторный запуск в ту же папку — разрешены.
        check_output_dir(source, root / "www" / "data_translated")
        resume = root / "www" / "data_resume"
        resume.mkdir()
        (resume / "_translation_cache.json").write_text("{}", encoding="utf-8")
        check_output_dir(source, resume)


# ────────────────────────────────────────────────────────────────────────────
# Установщик страховочного плагина
# ────────────────────────────────────────────────────────────────────────────

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
        assert_equal(installed.count("RPGMAKER_TRANSLATOR_AUTOWRAP"), 1,
                     "registration count")
        assert_equal(installed.count('"name":"Translator_AutoWrap"'), 1,
                     "plugin registration")
        assert_equal(first.plugin_path.is_file(), True, "autowrap plugin exists")
        assert_equal(first.backup_path.is_file(), True, "index backup exists")
        assert_equal(first.backup_path.read_text(encoding="utf-8"), original,
                     "index backup")

        second = install_runtime_text_wrap(data)
        assert_equal(second.changed, False, "second autowrap install idempotent")
        installed_again = (www / "index.html").read_text(encoding="utf-8")
        assert_equal(
            installed_again.count("RPGMAKER_TRANSLATOR_AUTOWRAP"),
            1,
            "idempotent registration count",
        )


TESTS = [
    test_code_roundtrip,
    test_duplicate_placeholder_is_dropped,
    test_bare_plugin_codes_are_protected,
    test_html_entities_survive_one_unescape,
    test_broken_translation_is_not_cached,
    test_glossary_protects_names,
    test_glossary_matches_whole_words_only,
    test_glossary_rejects_useless_terms,
    test_unwrap_joins_layout_breaks_only,
    test_unwrap_keeps_authored_breaks,
    test_wrapped_lines_fit_the_window,
    test_pagination_prefers_sentence_boundaries,
    test_control_codes_stay_with_their_word,
    test_technical_detection,
    test_group_boundaries_survive_translator_line_wraps,
    test_project_write_keeps_system_markers,
    test_script_reference_ignores_comments,
    test_message_block_is_rewrapped_into_windows,
    test_output_dir_guard,
    test_runtime_text_wrap_installer,
]


def main() -> int:
    failures = 0
    for test in TESTS:
        try:
            test()
        except Exception as exc:      # noqa: BLE001 — отчёт для человека
            failures += 1
            print(f"FAIL  {test.__name__}: {exc}")
        else:
            print(f"ok    {test.__name__}")
    if failures:
        print(f"\nself_test: {failures} из {len(TESTS)} провалено")
        return 1
    print(f"\nself_test: OK ({len(TESTS)} тестов)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
