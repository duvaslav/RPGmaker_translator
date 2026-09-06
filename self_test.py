"""Самопроверка без сети и без GUI: `python self_test.py`.

Помимо базовых инвариантов здесь закреплены регрессии на реально найденные
ошибки — каждая помечена комментарием, что именно она ловит.
"""
from __future__ import annotations

import json
import re
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
    wrap_paragraph,
)
from core.text_layout import install_runtime_text_wrap
from core.translators import LOCAL_LLM, _keep_entities, _placeholders_intact


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


def test_glossary_only_string_is_substituted_without_api() -> None:
    """Регрессия: имя говорящего целиком из глоссария оставалось непереведённым.

    В MZ имя говорящего лежит в params[4] команды 101 и часто равно ровно одному
    термину («Rin»). Такая строка не должна уходить в переводчик — там от неё
    остаётся один плейсхолдер, — но подставить целевую форму всё равно обязана.
    """
    with tempfile.TemporaryDirectory() as td:
        data_dir = Path(td)
        (data_dir / "Map001.json").write_text(json.dumps({
            "events": [None, {"id": 1, "name": "E", "pages": [{"list": [
                {"code": 101, "indent": 0, "parameters": ["", 0, 0, 2, "Rin"]},
                {"code": 401, "indent": 0, "parameters": ["Hello there."]},
                {"code": 0, "indent": 0, "parameters": []},
            ]}]}],
        }), encoding="utf-8")

        glossary = Glossary()
        glossary.add("Rin", "Рин")
        project = RPGMakerProject(data_dir, glossary=glossary)
        entries = project.extract_all()

        speaker = [e for e in entries if e.path[-1] == 4][0]
        assert_equal(speaker.needs_translation, False, "pure glossary term skips the API")

        line = [e for e in entries if e.is_message][0]
        project.apply_translations({entries.index(line): "Здравствуйте."})

        data = json.loads((data_dir / "Map001.json").read_text(encoding="utf-8"))
        commands = data["events"][1]["pages"][0]["list"]
        assert_equal(commands[0]["parameters"][4], "Рин", "speaker name substituted")
        assert_equal(commands[1]["parameters"][0], "Здравствуйте.", "line translated")


def test_rebuild_keeps_custom_command_fields() -> None:
    """Регрессия: пересборка окон теряла служебные поля команд (_original)."""
    with tempfile.TemporaryDirectory() as td:
        data_dir = Path(td)
        (data_dir / "Map001.json").write_text(json.dumps({
            "events": [None, {"id": 1, "name": "E", "pages": [{"list": [
                {"code": 101, "indent": 0, "parameters": ["", 0, 0, 2, ""]},
                {"code": 401, "indent": 0, "parameters": ["First half"],
                 "_original": "前半"},
                {"code": 401, "indent": 0, "parameters": ["second half."],
                 "_original": "後半。"},
                {"code": 0, "indent": 0, "parameters": []},
            ]}]}],
        }), encoding="utf-8")

        project = RPGMakerProject(data_dir)
        entries = project.extract_all()
        index = entries.index([e for e in entries if e.is_message][0])
        project.apply_translations({index: "Первая половина, вторая половина."})

        data = json.loads((data_dir / "Map001.json").read_text(encoding="utf-8"))
        lines = [c for c in data["events"][1]["pages"][0]["list"] if c["code"] == 401]
        originals = [c["_original"] for c in lines if "_original" in c]
        assert_equal(len(originals), 1, "metadata attached once, to the first line")
        assert_equal(originals[0], "前半\n後半。", "both originals preserved in order")


def test_identifier_lists_are_technical() -> None:
    """Регрессия: перечисление ресурсов через запятую уходило в переводчик.

    Плагины хранят в поле «имя актёра» пары идентификаторов картинок. Одиночный
    путь распознавался, а список — нет: запятая в шаблон пути не входит. После
    перевода такое значение ломало показ картинок.
    """
    technical = [
        "TaikiSperm/11Zakozu,TaikiSperm/11ZakozuRanshi",
        "TaikiSperm/39Founder ,TaikiSperm/39Founder Ranshi",
        "Bote0/00Kenji",
        "HP, MP",
    ]
    for text in technical:
        assert_equal(is_probably_technical_text(text), True, f"technical {text}")

    translatable = ["Iron Sword, Potion", "Привет, мир!", "おおネズミ",
                    "Ты идёшь в город, а он ждёт"]
    for text in translatable:
        assert_equal(is_probably_technical_text(text), False, f"translatable {text}")


def test_kinsoku_never_overflows_the_window() -> None:
    """Регрессия: правило кинсоку выталкивало строку за край окна.

    «…» нельзя оставлять в начале строки, поэтому знак переносился в конец
    предыдущей — раньше без проверки ширины. На тексте из сплошных многоточий
    строка вырастала за габарит окна.
    """
    measurer = _measurer("MZ")
    avail = measurer.layout.available_width()
    text = "\\I[31]" + "ез……ез♡" * 12
    lines = wrap_paragraph(text, measurer, avail)
    for line in lines:
        assert_true(measurer.width(line) <= avail,
                    f"kinsoku kept the line inside {avail}px: {measurer.width(line):.0f}px")
    assert_equal("".join(lines).replace(" ", ""), text.replace(" ", ""),
                 "no character lost or added while wrapping")


def test_wrapping_preserves_every_character() -> None:
    """Вёрстка не имеет права терять или добавлять знаки — только переносы."""
    measurer = _measurer("MZ")
    samples = [
        "\\C[6]出撃していたピュアエレメンツが敗北し、敵に捕らわれてしまったようです。",
        "\\I[31]" + "ез……ез♡" * 20,
        "Короткая строка.",
        "Очень длинное предложение " * 12,
    ]
    for text in samples:
        fitted = fit_message(text, measurer)
        flat = "".join(line for page in fitted.pages for line in page)
        assert_equal(re.sub(r"\s+", "", flat), re.sub(r"\s+", "", text),
                     f"characters preserved: {text[:40]!r}")


def test_mv_resolution_comes_from_plugins_js() -> None:
    """Регрессия: у MV разрешение лежит в plugins.js, а не в System.json.

    Игра с Community_Basic screenWidth=1000 верстелась по стоковым 816 px,
    и текст переносился на 184 px раньше края окна.
    """
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "data").mkdir()
        (root / "data" / "System.json").write_text("{}", encoding="utf-8")
        js = root / "js"
        js.mkdir()
        (js / "rpg_core.js").write_text("// MV", encoding="utf-8")
        (js / "plugins.js").write_text(
            'var $plugins =\n[\n'
            '{"name":"Community_Basic","status":true,'
            '"parameters":{"screenWidth":"1000","screenHeight":"800"}},\n'
            '{"name":"Disabled_Wide","status":false,'
            '"parameters":{"screenWidth":"2000"}}\n];\n',
            encoding="utf-8")

        layout = MessageLayout.detect(root / "data")
        assert_equal(layout.engine, "MV", "MV detected")
        assert_equal(layout.box_width, 1000, "width taken from the enabled plugin")
        assert_equal(layout.available_width(), 964, "text area = 1000 - 2*18")


def test_map_tree_labels_are_not_translated() -> None:
    """Регрессия: имена из MapInfos.json — метки дерева в редакторе.

    Движок этот файл загружает, но игроку не показывает: видно только
    displayName самой карты. Перевод таких строк жёг лимит API впустую.
    """
    with tempfile.TemporaryDirectory() as td:
        data_dir = Path(td)
        (data_dir / "MapInfos.json").write_text(json.dumps([
            None, {"id": 1, "name": "Village square", "parentId": 0},
        ]), encoding="utf-8")

        default = RPGMakerProject(data_dir).extract_all()
        assert_equal(default[0].needs_translation, False,
                     "editor label skipped by default")

        opted_in = RPGMakerProject(data_dir, translate_map_tree=True).extract_all()
        assert_equal(opted_in[0].needs_translation, True,
                     "still translatable when explicitly requested")


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


# ────────────────────────────────────────────────────────────────────────────
# Локальная модель: контракт, валидатор и поведение на реальных отказах
# ────────────────────────────────────────────────────────────────────────────

class MockLLMServer:
    """Локальный OpenAI-совместимый сервер со сценарием ответов.

    Настоящую модель в тестах использовать нельзя: она вне репозитория, весит
    2.7 ГБ и на одинаковый запрос отвечает одинаково — то есть воспроизвести
    на ней ошибочный ответ по требованию невозможно. Мок отдаёт заранее
    записанные ответы из протокола испытаний, включая испорченные.
    """

    def __init__(self, script, models=("qwen3.5-4b-rpg-ru-safe",)):
        import http.server
        import threading

        self.script = list(script)     # список ответов; последний повторяется
        self.models = list(models)
        self.requests: list[dict] = []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):        # тишина в отчёте теста
                pass

            def _send(self, payload, status=200):
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path.endswith("/models"):
                    self._send({"data": [{"id": m} for m in outer.models]})
                else:
                    self._send({"error": "not found"}, 404)

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
                outer.requests.append(body)
                idx = min(len(outer.requests) - 1, len(outer.script) - 1)
                entry = outer.script[idx]
                if callable(entry):
                    entry = entry(body)
                if isinstance(entry, tuple):        # (status, payload)
                    self._send(entry[1], entry[0])
                    return
                self._send({
                    "choices": [{
                        "message": {"role": "assistant", "content": entry},
                        "finish_reason": "stop",
                    }]
                })

        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]
        self.base_url = f"http://127.0.0.1:{self.port}/v1"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._server.shutdown()
        self._server.server_close()
        return False


def _reply(pairs) -> str:
    return json.dumps({"translations": [{"id": i, "translation": t} for i, t in pairs]},
                      ensure_ascii=False)


def test_item_ids_are_unique_within_a_scene():
    """Идентификатор обязан различать соседние окна одной страницы.

    У окна сообщения путь ведёт к СПИСКУ команд события, а не к отдельной
    строке, поэтому все окна одной страницы имели один и тот же путь. На
    реальной игре 35 877 единиц из 39 717 делили идентификатор с соседями:
    ответ модели на такой пакет отвергался целиком как содержащий повтор id,
    и вся страница уходила на поэлементный ремонт.
    """
    from core.rpgmaker_parser import item_id_for

    page = [
        {"code": 101, "parameters": ["", 0, 0, 2], "indent": 0},
        {"code": 401, "parameters": ["First window."], "indent": 0},
        {"code": 0, "parameters": [], "indent": 0},
        {"code": 101, "parameters": ["", 0, 0, 2], "indent": 0},
        {"code": 401, "parameters": ["Second window."], "indent": 0},
        {"code": 0, "parameters": [], "indent": 0},
    ]
    data = {"events": [None, {"id": 1, "pages": [{"list": page}]}]}
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "Map001.json").write_text(json.dumps(data), encoding="utf-8")
        entries = RPGMakerProject(Path(tmp)).extract_all()

    ids = [item_id_for(e) for e in entries]
    assert_equal(len(set(ids)), len(ids), f"идентификаторы повторяются: {ids}")
    # И оба окна обязаны остаться в одной сцене — иначе развалится контекст.
    assert_equal(len({e.scope for e in entries}), 1, "окна разошлись по сценам")


def test_llm_validator_catches_documented_failures():
    """F-001/F-003/F-004: подтверждённые отказы модели обязаны отклоняться.

    Все три взяты из протокола испытаний дословно. Каждый из них проходит
    какую-нибудь одну проверку — поэтому проверок несколько.
    """
    from core.llm_contract import verify

    # F-001: маркер \\N<...> просто исчез. Восстановить код будет нечем.
    v = verify("real-004", "<t0/>Need something?", "Нужно что-то?")
    assert_equal(v.ok, False, "F-001 принят")

    # F-003: код валюты переехал перед суммой — «¥700» вместо «700¥».
    # Количество маркеров и чисел при этом не изменилось, поэтому наивная
    # проверка «все ли маркеры на месте» такой ответ пропускает.
    v = verify("real-001", "You obtained 700<t0/>!", "Вы получили<t0/>700!")
    assert_equal(v.ok, False, "F-003 принят")
    assert_equal(verify("real-001", "You obtained 700<t0/>!",
                        "Вы получили 700<t0/>!").ok, True, "верный порядок отклонён")

    # F-004: сумма пропала, маркер уцелел.
    v = verify("real-014", "You obtained 4000<t0/>!", "Вы получили<t0/>!")
    assert_equal(v.ok, False, "F-004 принят")

    # Негативные находки протокола: заборов Markdown и преамбул не было, но
    # проверять их всё равно надо — это условие приёмки, а не наблюдение.
    assert_equal(verify("x", "Hi", "```\nПривет\n```").ok, False, "Markdown принят")
    assert_equal(verify("x", "Hi", "Перевод: Привет").ok, False, "преамбула принята")
    assert_equal(verify("x", "Save", "Save").ok, False, "непереведённое принято")
    assert_equal(verify("x", "Good morning!", "Доброе утро!").ok, True, "хороший отклонён")


def test_llm_response_parsing_rejects_broken_batches():
    """F-009: без схемы модель ломала JSON — разбор обязан это ловить."""
    from core.llm_contract import ResponseError, parse_response, response_schema

    ids = ["a", "b"]
    assert_equal(parse_response(_reply([("a", "А"), ("b", "Б")]), ids),
                 {"a": "А", "b": "Б"}, "корректный ответ не разобран")

    broken = [
        '{"translations":[{"id":"a","translation":"А"},',        # обрыв (batch 5)
        '{"id":"a","id":"b"}',                                   # повтор ключей (batch 10)
        _reply([("a", "А"), ("a", "Б")]),                        # дубликат id
        _reply([("a", "А")]),                                    # потерян элемент
        _reply([("a", "А"), ("c", "В")]),                        # чужой id
        "",                                                      # пусто
    ]
    for payload in broken:
        try:
            parse_response(payload, ids)
        except ResponseError:
            continue
        raise AssertionError(f"битый ответ принят: {payload[:40]!r}")

    # Схема должна запрещать и лишние, и недостающие элементы.
    schema = response_schema(ids)["json_schema"]["schema"]
    arr = schema["properties"]["translations"]
    assert_equal((arr["minItems"], arr["maxItems"]), (2, 2), "длина массива не закреплена")
    assert_equal(arr["items"]["properties"]["id"]["enum"], ids, "id не ограничены списком")
    assert_equal(arr["items"]["additionalProperties"], False, "разрешены лишние поля")


def test_llm_repairs_single_item_without_touching_the_rest():
    """F-010/F-011: один испорченный элемент чинится в одиночку.

    Пакет из трёх: второй приходит с потерянным маркером. Ремонт должен быть
    ровно один, ровно по этому элементу и с ДРУГОЙ инструкцией — повторять тот
    же запрос бесполезно, при температуре 0 модель трижды из трёх повторяла
    один и тот же дефект.
    """
    from core.local_llm import LocalLLMTranslator, REPAIR_PROMPT
    from core.llm_contract import TranslationItem

    items = [
        TranslationItem(id="i1", text="Good morning!"),
        TranslationItem(id="i2", text="<t0/>Need something?"),
        TranslationItem(id="i3", text="You obtained 700<t1/>!"),
    ]
    script = [
        _reply([("i1", "Доброе утро!"),
                ("i2", "Нужно что-то?"),               # F-001: маркер потерян
                ("i3", "Вы получили 700<t1/>!")]),
        _reply([("i2", "<t0/>Нужно что-то?")]),         # ремонт удался
    ]
    with MockLLMServer(script) as server:
        tr = LocalLLMTranslator(base_url=server.base_url)
        out = tr.translate_items(items, "en", "ru")

    assert_equal(out, ["Доброе утро!", "<t0/>Нужно что-то?", "Вы получили 700<t1/>!"],
                 "результат пакета")
    assert_equal(len(server.requests), 2, "число запросов")
    repair = server.requests[1]
    assert_equal(repair["messages"][0]["content"], REPAIR_PROMPT, "инструкция ремонта")
    repair_items = json.loads(repair["messages"][1]["content"])["items"]
    assert_equal([i["id"] for i in repair_items], ["i2"], "ремонт не изолирован")
    assert_equal("context_before" in repair_items[0], False, "в ремонте остался контекст")
    assert_equal(tr.stats["repaired"], 1, "счётчик починенных")
    assert_equal(tr.stats["failed"], 0, "счётчик отказов")


def test_llm_failed_item_is_not_cached():
    """§12: неисправимый элемент не попадает ни в кэш, ни в игру.

    Это защита от отравления кэша: если записать испорченный или пустой
    перевод, следующий запуск возьмёт его как готовый и строка останется
    сломанной навсегда.
    """
    from core.cache import TranslationCache
    from core.llm_contract import TranslationItem
    from core.local_llm import LocalLLMTranslator
    from core.translators import ChainConfig, TranslationRoute, translate_with_chain

    # Во второй строке НЕТ управляющих кодов — и это принципиально. Проверка
    # плейсхолдеров такую строку пропускает («кодов не было, терять нечего»),
    # поэтому пустой результат раньше уходил в кэш как готовый перевод.
    # Здесь модель возвращает исходный английский: перевода не произошло.
    texts = ["Good morning!", "Need something?"]
    items = [TranslationItem(id="i1", text=texts[0]),
             TranslationItem(id="i2", text=texts[1])]
    # Первый ответ ломает второй элемент, ремонт ломает его же ещё раз.
    script = [
        _reply([("i1", "Доброе утро!"), ("i2", "Need something?")]),
        _reply([("i2", "Need something?")]),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        cache_path = Path(tmp) / "_translation_cache.json"
        with MockLLMServer(script) as server:
            cfg = ChainConfig(
                route=TranslationRoute(src="en", pivot=None, dst="ru"),
                stage_providers=[(LOCAL_LLM, "", {"base_url": server.base_url})],
            )
            cache = TranslationCache(cache_path)
            out = translate_with_chain(texts, cfg, batch_size=5, cache=cache,
                                       items=items, stats={})
        assert_equal(out[0], "Доброе утро!", "исправный элемент")
        assert_equal(out[1], "", "испорченный элемент должен остаться пустым")

        saved = json.loads(cache_path.read_text(encoding="utf-8"))["entries"]
        values = list(saved.values())
        assert_equal(values, ["Доброе утро!"], "в кэш попало лишнее")
        assert_equal(any(texts[1] in k for k in saved), False,
                     "для непереведённой строки создана запись кэша")


def test_llm_cache_namespace_tracks_settings():
    """§12: смена модели, промпта или параметров обязана промахнуться мимо кэша.

    Иначе пользователь меняет промпт, запускает заново и получает ровно тот же
    перевод, не понимая почему.
    """
    from core.local_llm import LocalLLMTranslator

    base = LocalLLMTranslator()
    same = LocalLLMTranslator()
    assert_equal(base.cache_namespace, same.cache_namespace, "одинаковые настройки")

    for label, kwargs in [
        ("модель", {"model": "other-model"}),
        ("промпт", {"system_prompt": "Translate."}),
        ("температура", {"temperature": 0.7}),
        ("схема", {"use_json_schema": False}),
        ("контекст", {"context_mode": "event_page_3_3"}),
        ("глоссарий", {"glossary_version": "v2"}),
    ]:
        other = LocalLLMTranslator(**kwargs)
        if other.cache_namespace == base.cache_namespace:
            raise AssertionError(f"{label} не влияет на ключ кэша")

    # Облачные провайдеры остаются в своём пространстве имён.
    assert_equal(base.cache_namespace.startswith("local:"), True, "префикс кэша")


def test_llm_transport_errors_are_explicit():
    """§11: у каждой поломки должно быть внятное состояние, а не «ошибка сети»."""
    from core.local_llm import LocalLLMTranslator
    from core.llm_contract import TranslationItem
    from core.translators import TranslationError

    items = [TranslationItem(id="i1", text="Hi")]

    # Обрезанный ответ: finish_reason=length. Лечится уменьшением пакета,
    # поэтому сообщение обязано говорить именно это.
    truncated = [(200, {"choices": [{"message": {"content": '{"translations":['},
                                    "finish_reason": "length"}]})]
    with MockLLMServer(truncated) as server:
        tr = LocalLLMTranslator(base_url=server.base_url)
        try:
            tr.translate_items(items, "en", "ru")
            raise AssertionError("обрезанный ответ принят")
        except TranslationError as e:
            assert_equal("Уменьшите размер пакета" in str(e), True, f"текст ошибки: {e}")

    # Сервера нет вообще.
    tr = LocalLLMTranslator(base_url="http://127.0.0.1:1/v1")
    try:
        tr.translate_items(items, "en", "ru")
        raise AssertionError("отсутствие сервера не замечено")
    except TranslationError as e:
        assert_equal("не отвечает" in str(e), True, f"текст ошибки: {e}")
        assert_equal(e.recoverable, False, "повторять запросы к мёртвому серверу")

    # Нелокальный адрес: в запросах едет текст игры, наружу его выпускать нельзя.
    try:
        LocalLLMTranslator(base_url="http://10.0.0.5:1234/v1")
        raise AssertionError("нелокальный адрес принят молча")
    except TranslationError as e:
        assert_equal("локальным" in str(e), True, f"текст ошибки: {e}")


def test_llm_probe_reports_each_cause_separately():
    """§14: «проверить соединение» отвечает, ЧТО именно не так — лечится разным."""
    from core.local_llm import LocalLLMTranslator

    ok_script = [_reply([("probe-1", "Доброе утро!")])]

    # Сервер работает, модель загружена, формат соблюдён.
    with MockLLMServer(ok_script) as server:
        report = LocalLLMTranslator(base_url=server.base_url).probe()
    assert_equal(report["ok"], True, f"исправная связка: {report['message']}")
    assert_equal(report["model_found"], True, "модель не найдена")

    # Сервер работает, но загружена другая сборка. Похожее имя не годится:
    # другая сборка даст другой перевод и другой кэш.
    with MockLLMServer(ok_script, models=("qwen3.5-4b",)) as server:
        report = LocalLLMTranslator(base_url=server.base_url).probe()
    assert_equal(report["reachable"], True, "сервер не увиден")
    assert_equal(report["model_found"], False, "чужая модель зачтена")
    assert_equal(report["ok"], False, "проверка пройдена с чужой моделью")

    # Модель есть, но отвечает не по контракту.
    with MockLLMServer(["Конечно! Вот перевод: Доброе утро!"]) as server:
        report = LocalLLMTranslator(base_url=server.base_url).probe()
    assert_equal(report["model_found"], True, "модель не найдена")
    assert_equal(report["responds"], False, "болтовня зачтена за контракт")

    # Сервера нет.
    report = LocalLLMTranslator(base_url="http://127.0.0.1:1/v1").probe(timeout=2)
    assert_equal(report["reachable"], False, "мёртвый сервер зачтён живым")


def test_llm_receives_isolated_per_item_context():
    """§6: контекст элемента не выходит за пределы своей сцены.

    Регрессия на задокументированный случай: на реальной Map041 фраза
    Event 4 / Page 1 «Let's get along!» получала «следующим текстом» реплики
    Event 5 и Event 6 — сцену, которая в игре рядом может не случиться.
    """
    from core.local_llm import LocalLLMTranslator, unit_items
    from core.llm_contract import TranslationItem

    entries = [
        TextEntry(text="Let's get along!", codes=[], file="Map041.json",
                  path=("events", 4, "pages", 1, "list", 1, "parameters", 0),
                  scope="Map041.json|events/4/pages/1/list", text_type="dialogue"),
        TextEntry(text="You obtained 700<t0/>!", codes=[], file="Map041.json",
                  path=("events", 5, "pages", 0, "list", 2, "parameters", 0),
                  scope="Map041.json|events/5/pages/0/list", text_type="dialogue"),
        TextEntry(text="What's that supposed to mean!?", codes=[], file="Map041.json",
                  path=("events", 6, "pages", 0, "list", 1, "parameters", 0),
                  scope="Map041.json|events/6/pages/0/list", text_type="dialogue"),
    ]
    units = build_translation_units(entries, group_dialogues=False)
    items = unit_items(units)

    assert_equal(items[0].context_after, [], "контекст перетёк из чужого Event")
    assert_equal(items[0].context_before, [], "контекст перетёк из чужого Event")
    assert_equal(items[0].id, "Map041.json:events/4/pages/1/list/1/parameters/0",
                 "идентификатор элемента")
    assert_equal(items[0].location, {"file": "Map041.json", "event": 4, "page": 1},
                 "место элемента")

    # Внутри одной сцены контекст, наоборот, обязан быть.
    same_scene = [
        TextEntry(text="Line one.", codes=[], file="Map001.json",
                  path=("events", 1, "pages", 0, "list", 1, "parameters", 0),
                  scope="Map001.json|events/1/pages/0/list", text_type="dialogue"),
        TextEntry(text="Line two.", codes=[], file="Map001.json",
                  path=("events", 1, "pages", 0, "list", 2, "parameters", 0),
                  scope="Map001.json|events/1/pages/0/list", text_type="dialogue"),
    ]
    items = unit_items(build_translation_units(same_scene, group_dialogues=False))
    assert_equal(items[0].context_after, ["Line two."], "контекст сцены потерян")
    assert_equal(items[1].context_before, ["Line one."], "контекст сцены потерян")

    # И этот контекст должен реально доехать до модели — отдельно у каждого
    # элемента, а не общей строкой на весь пакет.
    with MockLLMServer([_reply([(items[0].id, "Строка один."),
                                (items[1].id, "Строка два.")])]) as server:
        LocalLLMTranslator(base_url=server.base_url).translate_items(items, "en", "ru")
        sent = json.loads(server.requests[0]["messages"][1]["content"])["items"]
    assert_equal(sent[0]["context_after"], ["Line two."], "контекст не отправлен")
    assert_equal(sent[1]["context_before"], ["Line one."], "контекст не отправлен")


def test_llm_request_shape_follows_the_protocol():
    """§16: параметры запроса — те, что признаны безопасными в испытаниях."""
    from core.local_llm import LocalLLMTranslator, DEFAULT_SYSTEM_PROMPT
    from core.llm_contract import TranslationItem

    items = [TranslationItem(id="i1", text="Good morning!")]
    with MockLLMServer([_reply([("i1", "Доброе утро!")])]) as server:
        LocalLLMTranslator(base_url=server.base_url).translate_items(items, "en", "ru")
        body = server.requests[0]

    assert_equal(body["stream"], False, "включён поток")
    assert_equal(body["temperature"], 0.0, "температура")
    assert_equal(body["top_p"], 0.8, "top_p")
    assert_equal(body["top_k"], 20, "top_k")
    assert_equal(body["reasoning_effort"], "none", "рассуждения не выключены")
    assert_equal(body["messages"][0]["content"], DEFAULT_SYSTEM_PROMPT, "промпт по умолчанию")
    # Строгая схема обязательна на любом размере пакета: без неё корректный
    # JSON приходил в 13 случаях из 15, а точные идентификаторы — в 11.
    assert_equal(body["response_format"]["json_schema"]["strict"], True, "схема не строгая")
    assert_equal(body["response_format"]["json_schema"]["schema"]["properties"]
                 ["translations"]["items"]["properties"]["id"]["enum"], ["i1"],
                 "идентификаторы не закреплены схемой")


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
    test_glossary_only_string_is_substituted_without_api,
    test_rebuild_keeps_custom_command_fields,
    test_identifier_lists_are_technical,
    test_kinsoku_never_overflows_the_window,
    test_wrapping_preserves_every_character,
    test_mv_resolution_comes_from_plugins_js,
    test_map_tree_labels_are_not_translated,
    test_output_dir_guard,
    test_runtime_text_wrap_installer,
    # ── Локальная модель ────────────────────────────────────────────────────
    test_item_ids_are_unique_within_a_scene,
    test_llm_validator_catches_documented_failures,
    test_llm_response_parsing_rejects_broken_batches,
    test_llm_repairs_single_item_without_touching_the_rest,
    test_llm_failed_item_is_not_cached,
    test_llm_cache_namespace_tracks_settings,
    test_llm_transport_errors_are_explicit,
    test_llm_probe_reports_each_cause_separately,
    test_llm_receives_isolated_per_item_context,
    test_llm_request_shape_follows_the_protocol,
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
