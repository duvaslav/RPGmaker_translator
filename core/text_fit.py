"""Расклейка, перенос и разбивка на окна текста сообщений RPG Maker MV/MZ.

Зачем это нужно
───────────────
В RPG Maker текст сообщения хранится уже разбитым на строки так, как автор
подогнал его под ширину окна. Строки рвутся посреди предложения::

    ~Link Event 1 (Panty Flash Discovery)~ Trigger
    Condition: Break Time \\N[1] accidentally catches a
    glimpse of Natsubo's panties... What action does
    Airy, who saw him looking, take...?

Если отдать это переводчику как есть, DeepL/Yandex переведут КАЖДУЮ СТРОКУ
отдельным сегментом: «Trigger» без «Condition» превращается в «спусковой
крючок», «catches a» и «glimpse of» переводятся как два обрывка. Именно это
делает машинный перевод через утилиту заметно хуже, чем тот же Yandex, куда
человек вставляет абзац целиком.

Поэтому конвейер такой:

1. ``unwrap_message``  — склеиваем авторские переносы обратно в связный текст.
   Переводчик получает целые предложения и переводит их как проза.
2. ``wrap_message``    — после перевода заново переносим по РЕАЛЬНОЙ ширине
   окна, измеряя строку тем же шрифтом, которым её нарисует игра.
3. ``paginate``        — если строк стало больше, чем помещается в окно,
   режем на несколько окон по границам предложений, а не по счёту символов.

Шаг 3 решает вторую жалобу: раньше лишние строки уезжали за край окна или
попадали в следующее сообщение посреди слова.

Измерение ширины
────────────────
Если установлен Pillow, ширина считается настоящим шрифтом игры
(``fonts/*.ttf``) — это точность до пикселя. Без Pillow используется таблица
ширин для M+ 1m (штатный шрифт MV/MZ): моноширинный, латиница и кириллица
0.5em, CJK 1.0em. Для стоковых игр таблица совпадает со шрифтом.
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


# ────────────────────────────────────────────────────────────────────────────
# Геометрия окна сообщения
# ────────────────────────────────────────────────────────────────────────────

@dataclass
class MessageLayout:
    """Размеры окна сообщения — как их считает сам движок.

    MV:  Window_Message.windowWidth() = Graphics.boxWidth (816)
         standardPadding() = 18  →  contentsWidth = 816 - 36 = 780
    MZ:  Window_Base padding = $gameSystem.windowPadding() = 12
         →  contentsWidth = 816 - 24 = 792
    В обоих движках Window_Message.newLineX() = 168 при наличии портрета,
    а numVisibleRows() = 4.
    """
    engine: str = "MZ"            # "MV" | "MZ"
    box_width: int = 816
    padding: int = 12
    face_offset: int = 168
    max_lines: int = 4
    font_size: int = 26
    font_path: Path | None = None

    @property
    def text_width(self) -> int:
        """Ширина области текста без портрета."""
        return self.box_width - self.padding * 2

    def available_width(self, has_face: bool = False) -> int:
        return self.text_width - (self.face_offset if has_face else 0)

    @classmethod
    def detect(cls, data_dir: str | Path) -> "MessageLayout":
        """Определяет движок, размер шрифта и ширину окна по файлам игры."""
        data_dir = Path(data_dir)
        www = _find_www(data_dir)

        engine = "MZ"
        if www is not None:
            js = www / "js"
            if (js / "rpg_core.js").is_file() and not (js / "rmmz_core.js").is_file():
                engine = "MV"

        layout = cls(engine=engine)
        layout.padding = 18 if engine == "MV" else 12
        layout.font_size = 28 if engine == "MV" else 26

        # System.json → advanced.fontSize / uiAreaWidth
        system = _load_json(data_dir / "System.json")
        advanced = (system or {}).get("advanced") if isinstance(system, dict) else None
        if isinstance(advanced, dict):
            size = advanced.get("fontSize")
            if isinstance(size, int) and 8 <= size <= 96:
                layout.font_size = size
            width = advanced.get("uiAreaWidth") or advanced.get("screenWidth")
            if isinstance(width, int) and 320 <= width <= 4096:
                layout.box_width = width

        if www is not None:
            layout.font_path = _pick_font(www / "fonts")
        return layout


def _find_www(data_dir: Path) -> Path | None:
    """Ближайший корень игры (там, где лежат js/ и fonts/)."""
    data_dir = Path(data_dir).resolve()
    for candidate in (data_dir.parent, data_dir.parent.parent, data_dir):
        if (candidate / "js").is_dir():
            return candidate
    return None


def _load_json(path: Path):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return None


# Штатные шрифты MV/MZ идут первыми: если игра поставила свой, берём его.
_FONT_PREFERENCE = ("mplus-1m-regular", "mplus-1p-regular", "gamefont")


def _pick_font(fonts_dir: Path) -> Path | None:
    if not fonts_dir.is_dir():
        return None
    files = sorted(
        p for p in fonts_dir.iterdir()
        if p.suffix.lower() in (".ttf", ".otf", ".woff", ".woff2")
    )
    if not files:
        return None
    # Шрифт игры важнее штатного: если есть что-то помимо M+, скорее всего
    # именно им и рисуется текст.
    custom = [p for p in files if p.stem.lower() not in _FONT_PREFERENCE]
    for pool in (custom, files):
        for path in pool:
            if path.suffix.lower() in (".ttf", ".otf"):
                return path
    return None


# ────────────────────────────────────────────────────────────────────────────
# Управляющие коды: что занимает место на экране, а что нет
# ────────────────────────────────────────────────────────────────────────────

# Коды, которые движок заменяет на текст ДО отрисовки (convertEscapeCharacters).
_SUBSTITUTING = re.compile(r'\\([NPV])\[(\d+)\]', re.IGNORECASE)
# Иконка занимает реальное место: Window_Base._iconWidth + 4.
_ICON = re.compile(r'\\I\[(\d+)\]', re.IGNORECASE)
# Всё остальное (цвет, размер, пауза, скорость) ширины не имеет.
_ZERO_WIDTH = re.compile(
    r'\\[A-Za-z][A-Za-z0-9]*\[[^\]]*\]'     # \C[1], \FS[24], \PX[10]
    r'|\\[A-Za-z][A-Za-z0-9]*<[^>]*>'        # \N<имя> — плагинный вариант
    r'|\\[.!|^><{}$G\\]'                      # \. \! \| \^ \> \< \{ \} \$ \G \\
    r'|\\[A-Za-z]'                            # голые \c, \w и прочие плагинные
)
ICON_WIDTH = 36


class EscapeResolver:
    """Подставляет \\N[n], \\P[n], \\V[n] реальными значениями из игры.

    Без этого измерение врёт: `\\N[1]` — это два символа в JSON, но на экране
    имя героя из пяти-десяти букв. Именно в таких строках текст и вылезал
    за край окна.
    """

    def __init__(self, data_dir: str | Path | None = None):
        self.actors: list[str] = []
        self.variables_fallback = "0"
        if data_dir is not None:
            self._load(Path(data_dir))

    def _load(self, data_dir: Path) -> None:
        actors = _load_json(data_dir / "Actors.json")
        if isinstance(actors, list):
            self.actors = [
                a.get("name", "") if isinstance(a, dict) else ""
                for a in actors
            ]

    def actor_name(self, index: int) -> str:
        if 0 <= index < len(self.actors) and self.actors[index]:
            return self.actors[index]
        # Разумная замена: имя средней длины, чтобы оценка не была слишком
        # оптимистичной, если Actors.json недоступен.
        return "Player"

    def __call__(self, text: str) -> str:
        def repl(m: re.Match) -> str:
            kind = m.group(1).upper()
            num = int(m.group(2))
            if kind in ("N", "P"):
                return self.actor_name(num)
            return self.variables_fallback
        return _SUBSTITUTING.sub(repl, text)


# ────────────────────────────────────────────────────────────────────────────
# Измерение ширины
# ────────────────────────────────────────────────────────────────────────────

class TextMeasurer:
    """Ширина строки в пикселях с учётом управляющих кодов."""

    def __init__(self, layout: MessageLayout,
                 resolver: EscapeResolver | Callable[[str], str] | None = None):
        self.layout = layout
        self.resolver = resolver or (lambda s: s)
        self._font = self._load_font(layout)
        self._cache: dict[str, float] = {}

    @staticmethod
    def _load_font(layout: MessageLayout):
        if layout.font_path is None:
            return None
        try:
            from PIL import ImageFont
        except ImportError:
            return None
        try:
            return ImageFont.truetype(str(layout.font_path), layout.font_size)
        except Exception:
            return None

    @property
    def exact(self) -> bool:
        """True, если ширина считается настоящим шрифтом игры."""
        return self._font is not None

    # ── Ширина одного символа без Pillow ────────────────────────────────────

    def _fallback_char_width(self, ch: str) -> float:
        """Таблица для M+ 1m — штатного моноширинного шрифта MV/MZ."""
        if ch == "\t":
            return self.layout.font_size * 2.0
        width_class = unicodedata.east_asian_width(ch)
        if width_class in ("W", "F"):
            return self.layout.font_size          # полноширинный CJK
        if unicodedata.combining(ch):
            return 0.0
        return self.layout.font_size * 0.5        # латиница, кириллица, знаки

    def raw_width(self, text: str) -> float:
        """Ширина текста БЕЗ разбора управляющих кодов."""
        if not text:
            return 0.0
        cached = self._cache.get(text)
        if cached is not None:
            return cached
        if self._font is not None:
            try:
                value = float(self._font.getlength(text))
            except Exception:
                value = sum(self._fallback_char_width(c) for c in text)
        else:
            value = sum(self._fallback_char_width(c) for c in text)
        if len(self._cache) < 100_000:
            self._cache[text] = value
        return value

    def width(self, text: str) -> float:
        """Ширина строки так, как её нарисует игра."""
        if not text:
            return 0.0
        resolved = self.resolver(text)
        icons = len(_ICON.findall(resolved))
        resolved = _ICON.sub("", resolved)
        resolved = _ZERO_WIDTH.sub("", resolved)
        return self.raw_width(resolved) + icons * ICON_WIDTH

    def visible_text(self, text: str) -> str:
        """Текст без управляющих кодов — для эвристик и диагностики."""
        resolved = self.resolver(text)
        resolved = _ICON.sub("", resolved)
        return _ZERO_WIDTH.sub("", resolved)


# ────────────────────────────────────────────────────────────────────────────
# Шаг 1. Расклейка авторских переносов
# ────────────────────────────────────────────────────────────────────────────

# Конец предложения: после этих знаков перенос почти наверняка авторский.
_SENTENCE_END = ".!?…。！？♪〜~）)]】」』"
# Строка-подпись: заканчивается двоеточием (часто «Имя:» перед репликой).
_SOFT_RATIO = 0.72


def _ends_sentence(visible: str) -> bool:
    stripped = visible.rstrip()
    if not stripped:
        return True
    return stripped[-1] in _SENTENCE_END


def unwrap_message(text: str, measurer: TextMeasurer,
                   avail: float | None = None) -> list[str]:
    """Склеивает строки, разорванные ради ширины окна, в цельные абзацы.

    Возвращает список абзацев. Перенос считается «вёрсточным» (его склеиваем),
    если строка почти дотянулась до края окна И не заканчивается знаком конца
    предложения. Всё остальное — авторский перенос, он сохраняется.

    Примеры::

        "…What action does" + "Airy, who saw him looking, take…?"
            → склеиваем: строка длинная, кончается на "does"

        "(It's unlocked… Did someone forget to lock it…?" + "No… That guy…)"
            → НЕ склеиваем: первая строка кончается на "?"

        "\\C[8]\\N[1]\\C" + "(It's unlocked…"
            → НЕ склеиваем: строка с именем говорящего короткая
    """
    if avail is None:
        avail = measurer.layout.available_width()
    lines = text.split("\n")
    if len(lines) <= 1:
        return [text]

    paragraphs: list[str] = []
    current = lines[0]
    for nxt in lines[1:]:
        visible = measurer.visible_text(current)
        # Пустая строка — авторский отступ, сохраняем как есть.
        soft = (
            visible.strip() != ""
            and nxt.strip() != ""
            and not _ends_sentence(visible)
            and measurer.width(current) >= avail * _SOFT_RATIO
        )
        if soft:
            current = _join_wrapped(current, nxt)
        else:
            paragraphs.append(current)
            current = nxt
    paragraphs.append(current)
    return paragraphs


def _join_wrapped(left: str, right: str) -> str:
    """Склеивает две половины разорванной строки.

    В CJK пробел не ставится (в японском и китайском его нет между словами),
    в остальных случаях — обычный пробел.
    """
    if not left:
        return right
    if not right:
        return left
    if left.endswith(" ") or right.startswith(" "):
        return left + right
    if _is_cjk(left[-1]) and _is_cjk(right[0]):
        return left + right
    return left + " " + right


def _is_cjk(ch: str) -> bool:
    return unicodedata.east_asian_width(ch) in ("W", "F")


# ────────────────────────────────────────────────────────────────────────────
# Шаг 2. Перенос по реальной ширине
# ────────────────────────────────────────────────────────────────────────────

# Кинсоку: знаки, которые нельзя оставлять в начале строки.
_NO_LINE_START = "、。，．・？！」』）］｝〉》〕を\u3001\u3002,.!?:;)]}»…"
# Знаки, которые нельзя оставлять в конце строки.
_NO_LINE_END = "「『（［｛〈《〔([{«"


def wrap_paragraph(text: str, measurer: TextMeasurer, avail: float) -> list[str]:
    """Переносит один абзац по ширине avail. Возвращает список строк."""
    if not text.strip():
        return [text]
    if measurer.width(text) <= avail:
        return [text]

    tokens = _tokenize(text)
    lines: list[str] = []
    current = ""
    for token in tokens:
        candidate = current + token
        if current and measurer.width(candidate.rstrip()) > avail:
            lines.append(current.rstrip())
            current = token.lstrip() if token.strip() else ""
        else:
            current = candidate
        # Один «токен» шире всей строки (длинное слово или ссылка) — режем силой.
        while measurer.width(current.rstrip()) > avail and len(current) > 1:
            head, tail = _force_split(current, measurer, avail)
            if not head:
                break
            lines.append(head)
            current = tail
    if current.strip():
        lines.append(current.rstrip())
    return _apply_kinsoku(lines) or [text]


# Токен = управляющий код целиком, либо слово с ведущими пробелами, либо
# одиночный CJK-символ (в японском переносить можно почти где угодно).
_TOKEN = re.compile(
    r'\\[A-Za-z][A-Za-z0-9]*\[[^\]]*\]'
    r'|\\[A-Za-z][A-Za-z0-9]*<[^>]*>'
    r'|\\[^A-Za-z]'
    r'|\\[A-Za-z]'
    r'|\s+\S*'
    r'|\S'
)


def _tokenize(text: str) -> list[str]:
    """Режет строку на единицы переноса.

    Управляющий код никогда не отрывается от следующего за ним слова —
    иначе `\\C[2]` осталось бы висеть в конце строки, а раскрашенное слово
    уехало на следующую.
    """
    raw = _TOKEN.findall(text)
    tokens: list[str] = []
    pending_codes = ""
    for piece in raw:
        if piece.startswith("\\"):
            pending_codes += piece
            continue
        if _is_cjk(piece[-1]) or (len(piece) == 1 and not piece.isspace()):
            tokens.append(pending_codes + piece)
            pending_codes = ""
            continue
        tokens.append(pending_codes + piece)
        pending_codes = ""
    if pending_codes:
        if tokens:
            tokens[-1] += pending_codes
        else:
            tokens.append(pending_codes)
    return tokens


def _force_split(text: str, measurer: TextMeasurer, avail: float) -> tuple[str, str]:
    """Режет слишком длинный фрагмент по символам, не разрывая коды."""
    guard = 0
    for idx in range(len(text), 0, -1):
        head = text[:idx]
        # Не режем внутри управляющего кода.
        if _splits_code(text, idx):
            continue
        if measurer.width(head.rstrip()) <= avail:
            return head.rstrip(), text[idx:].lstrip()
        guard += 1
        if guard > 4096:
            break
    return "", text


_CODE_SPANS = re.compile(
    r'\\[A-Za-z][A-Za-z0-9]*\[[^\]]*\]'
    r'|\\[A-Za-z][A-Za-z0-9]*<[^>]*>'
    r'|\\.'
)


def _splits_code(text: str, idx: int) -> bool:
    for m in _CODE_SPANS.finditer(text):
        if m.start() < idx < m.end():
            return True
    return False


def _apply_kinsoku(lines: list[str]) -> list[str]:
    """Не даём строке начинаться с запятой/точки и кончаться открывающей скобкой."""
    result = list(lines)
    for i in range(1, len(result)):
        while result[i] and result[i][0] in _NO_LINE_START and result[i - 1]:
            result[i - 1] += result[i][0]
            result[i] = result[i][1:].lstrip()
            if not result[i]:
                break
    for i in range(len(result) - 1):
        while result[i] and result[i][-1] in _NO_LINE_END:
            result[i + 1] = result[i][-1] + result[i + 1]
            result[i] = result[i][:-1].rstrip()
            if not result[i]:
                break
    return [line for line in result if line != ""]


# ────────────────────────────────────────────────────────────────────────────
# Шаг 3. Разбивка на окна
# ────────────────────────────────────────────────────────────────────────────

# Конец предложения = знак из набора, за которым идёт пробел или конец строки.
# Многоточие «...» и японские 。！？ учитываются как один разделитель.
_SENTENCE_SPLIT = re.compile(
    r'(?<=[.!?…。！？])(?<!\.\.)\s+(?=[^\s])'
)


def split_sentences(text: str) -> list[str]:
    """Режет абзац на предложения, сохраняя знаки препинания.

    Нужно для разбивки на окна: рвать сообщение между предложениями гораздо
    читабельнее, чем ровно по счёту строк.
    """
    text = text.strip()
    if not text:
        return []
    parts = [p.strip() for p in _SENTENCE_SPLIT.split(text)]
    return [p for p in parts if p]


def paginate(lines: list[str], max_lines: int,
             measurer: TextMeasurer | None = None) -> list[list[str]]:
    """Режет уже готовые строки на окна по max_lines.

    Запасной вариант для случаев, когда разбить по предложениям не вышло
    (одно предложение длиннее целого окна).
    """
    if max_lines <= 0 or len(lines) <= max_lines:
        return [lines]
    return [lines[i:i + max_lines] for i in range(0, len(lines), max_lines)]


def paginate_by_sentences(paragraphs: list[str], measurer: TextMeasurer,
                          avail: float, max_lines: int) -> list[list[str]]:
    """Раскладывает абзацы по окнам, разрывая текст между предложениями.

    Работает так: предложения набираются в окно по одному, и каждый раз текст
    окна переносится ЦЕЛИКОМ — поэтому строки остаются плотно заполненными, а
    граница окна всегда совпадает с концом фразы. Разбивка «ровно по четыре
    строки» такого не давала: конец предложения то и дело оказывался посреди
    строки, и хвост фразы уезжал в следующее окно.

    Предложение, которое само по себе не влезает в окно, режется по строкам —
    иначе его было бы негде показать.
    """
    pages: list[list[str]] = []
    pending: list[str] = []      # предложения, набранные в текущее окно
    carry: list[str] = []        # строки, оставшиеся от слишком длинного предложения

    def wrap_all(sentences: list[str]) -> list[str]:
        # Предложения окна переносятся ОДНИМ куском, иначе каждое начиналось бы
        # с новой строки и окно заполнялось бы наполовину.
        if not sentences:
            return []
        joined = sentences[0]
        for chunk in sentences[1:]:
            joined = _join_wrapped(joined, chunk)
        return wrap_paragraph(joined, measurer, avail)

    def flush() -> None:
        nonlocal pending, carry
        lines = carry + (wrap_all(pending) if pending else [])
        if lines:
            pages.append(lines)
        pending, carry = [], []

    for paragraph in paragraphs:
        sentences = split_sentences(paragraph) or [paragraph]
        for sentence in sentences:
            trial = wrap_all(pending + [sentence])
            if len(carry) + len(trial) <= max_lines:
                pending.append(sentence)
                continue

            # Не помещается: закрываем окно тем, что уже набрано.
            if pending or carry:
                flush()

            solo = wrap_paragraph(sentence, measurer, avail)
            if len(solo) <= max_lines:
                pending = [sentence]
                continue

            # Предложение длиннее окна — придётся резать по строкам.
            for i in range(0, len(solo), max_lines):
                chunk = solo[i:i + max_lines]
                if len(chunk) == max_lines:
                    pages.append(chunk)
                else:
                    carry = chunk
        # Абзац закончился: следующий начнётся с новой строки, но то же окно.
        if pending:
            carry = carry + wrap_all(pending)
            pending = []

    flush()
    return pages or [[""]]


def _strip_codes(text: str) -> str:
    text = _ICON.sub("", text)
    return _ZERO_WIDTH.sub("", text)


# ────────────────────────────────────────────────────────────────────────────
# Готовый конвейер
# ────────────────────────────────────────────────────────────────────────────

@dataclass
class FittedMessage:
    """Результат вёрстки одного сообщения."""
    pages: list[list[str]] = field(default_factory=list)
    overflowed: bool = False        # понадобилось больше одного окна

    @property
    def line_count(self) -> int:
        return sum(len(p) for p in self.pages)


def fit_message(text: str, measurer: TextMeasurer, *, has_face: bool = False,
                max_lines: int | None = None) -> FittedMessage:
    """Полный цикл: расклеить → перенести по ширине → разбить на окна."""
    layout = measurer.layout
    avail = layout.available_width(has_face)
    limit = layout.max_lines if max_lines is None else max_lines

    paragraphs = unwrap_message(text, measurer, avail)

    if limit <= 0:
        # Разбивать на окна нечем (нет команды-заголовка, которую можно
        # размножить) — отдаём один длинный блок.
        lines: list[str] = []
        for paragraph in paragraphs:
            lines.extend(wrap_paragraph(paragraph, measurer, avail))
        return FittedMessage(pages=[lines or [""]], overflowed=False)

    pages = paginate_by_sentences(paragraphs, measurer, avail, limit)
    return FittedMessage(pages=pages, overflowed=len(pages) > 1)


def flatten_for_translation(text: str, measurer: TextMeasurer,
                            has_face: bool = False) -> str:
    """Готовит текст сообщения к отправке переводчику.

    Абзацы разделяются одиночным ``\\n``; внутри абзаца текст цельный, поэтому
    сервис видит законченные предложения, а не обрывки строк.
    """
    avail = measurer.layout.available_width(has_face)
    return "\n".join(unwrap_message(text, measurer, avail))
