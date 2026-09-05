"""
Главное окно приложения. PyQt6.
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QSize, QTimer
from PyQt6.QtGui import QPalette, QColor
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QLineEdit, QComboBox, QCheckBox, QSpinBox, QGroupBox,
    QFileDialog, QPlainTextEdit, QProgressBar, QTabWidget, QMessageBox,
    QFrame, QScrollArea, QDialog, QListWidget, QListWidgetItem,
    QAbstractItemView, QTextBrowser,
)

from core.config import load_config, save_config
from core.rpgmaker_parser import ProjectStats
from core.translators import TranslationRoute
from core.worker import TranslationWorker, AnalysisWorker, KeySearchWorker


# ────────────────────────────────────────────────────────────────────────────
# Тема
# ────────────────────────────────────────────────────────────────────────────

STYLESHEET = """
* {
    font-family: "Segoe UI", "SF Pro Text", "Inter", sans-serif;
    font-size: 10pt;
    color: #E6E1D7;
}
QMainWindow, QWidget#central {
    background-color: #14130F;
}
QGroupBox {
    background-color: #1C1A15;
    border: 1px solid #2D2A22;
    border-radius: 8px;
    margin-top: 14px;
    padding-top: 16px;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 10px;
    left: 12px;
    color: #C9A86A;
    font-size: 9pt;
    letter-spacing: 1px;
    text-transform: uppercase;
}
QLabel {
    color: #E6E1D7;
}
QLabel#hint {
    color: #7A7264;
    font-size: 9pt;
}
QLabel#phase {
    color: #C9A86A;
    font-size: 10pt;
    font-weight: 600;
}
QLineEdit, QComboBox, QSpinBox {
    background-color: #0F0E0B;
    border: 1px solid #2D2A22;
    border-radius: 5px;
    padding: 6px 9px;
    selection-background-color: #C9A86A;
    selection-color: #14130F;
    min-height: 18px;
    min-width: 120px;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {
    border: 1px solid #C9A86A;
}
QComboBox::drop-down {
    border: none;
    width: 22px;
}
QComboBox::down-arrow {
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid #C9A86A;
    margin-right: 8px;
}
QComboBox QAbstractItemView {
    background-color: #1C1A15;
    border: 1px solid #2D2A22;
    selection-background-color: #C9A86A;
    selection-color: #14130F;
    outline: 0;
}
QPushButton {
    background-color: #2D2A22;
    border: 1px solid #3A3629;
    border-radius: 5px;
    padding: 7px 16px;
    color: #E6E1D7;
    font-weight: 500;
}
QPushButton:hover {
    background-color: #3A3629;
    border: 1px solid #4A4536;
}
QPushButton:pressed {
    background-color: #1C1A15;
}
QPushButton:disabled {
    color: #4A4536;
    background-color: #1C1A15;
    border: 1px solid #2D2A22;
}
QPushButton#primary {
    background-color: #C9A86A;
    color: #14130F;
    border: 1px solid #C9A86A;
    font-weight: 600;
    padding: 9px 20px;
}
QPushButton#primary:hover {
    background-color: #D4B47A;
    border: 1px solid #D4B47A;
}
QPushButton#primary:disabled {
    background-color: #5A4F35;
    color: #2D2A22;
    border: 1px solid #5A4F35;
}
QPushButton#danger {
    background-color: #3D1F1A;
    color: #E6B5A5;
    border: 1px solid #5A2D22;
}
QPushButton#danger:hover {
    background-color: #5A2D22;
}
QCheckBox {
    spacing: 8px;
}
QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border: 1px solid #3A3629;
    border-radius: 3px;
    background-color: #0F0E0B;
}
QCheckBox::indicator:hover {
    border: 1px solid #C9A86A;
}
QCheckBox::indicator:checked {
    background-color: #C9A86A;
    border: 1px solid #C9A86A;
    image: none;
}
QTabWidget::pane {
    border: 1px solid #2D2A22;
    border-radius: 8px;
    background-color: #1C1A15;
    top: -1px;
}
QTabBar::tab {
    background-color: transparent;
    color: #7A7264;
    padding: 8px 18px;
    border: none;
    font-weight: 500;
}
QTabBar::tab:selected {
    color: #C9A86A;
    border-bottom: 2px solid #C9A86A;
}
QTabBar::tab:hover:!selected {
    color: #E6E1D7;
}
QPlainTextEdit {
    background-color: #0A0907;
    border: 1px solid #2D2A22;
    border-radius: 6px;
    padding: 8px;
    font-family: "JetBrains Mono", "Cascadia Code", "Consolas", monospace;
    font-size: 9pt;
    color: #C9C2B0;
}
QProgressBar {
    background-color: #0F0E0B;
    border: 1px solid #2D2A22;
    border-radius: 4px;
    height: 8px;
    text-align: center;
    color: transparent;
}
QProgressBar::chunk {
    background-color: #C9A86A;
    border-radius: 3px;
}
QScrollBar:vertical {
    background-color: transparent;
    width: 10px;
    margin: 2px;
}
QScrollBar::handle:vertical {
    background-color: #3A3629;
    border-radius: 4px;
    min-height: 24px;
}
QScrollBar::handle:vertical:hover {
    background-color: #4A4536;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
QFrame#separator {
    background-color: #2D2A22;
    max-height: 1px;
    min-height: 1px;
}
"""


# ────────────────────────────────────────────────────────────────────────────
# Хелперы
# ────────────────────────────────────────────────────────────────────────────

LANGUAGES = [
    ("ja", "Японский"),
    ("en", "Английский"),
    ("ru", "Русский"),
    ("ko", "Корейский"),
    ("zh", "Китайский"),
    ("de", "Немецкий"),
    ("fr", "Французский"),
    ("es", "Испанский"),
]
LANG_DICT = dict(LANGUAGES)

PROVIDERS = ["DeepL", "Google", "Yandex"]


def lang_display(code: str) -> str:
    return f"{LANG_DICT.get(code, code)} ({code})"


def wrap_in_scroll(widget: QWidget) -> QScrollArea:
    """Заворачивает виджет в вертикальный скролл, чтобы интерфейс не ломался
    в маленьком окне."""
    scroll = QScrollArea()
    scroll.setWidget(widget)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    return scroll


# ────────────────────────────────────────────────────────────────────────────
# Главное окно
# ────────────────────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("RPG Maker Translator")
        self.resize(980, 720)
        self.setMinimumSize(QSize(560, 480))

        self.config = load_config()
        self.worker: TranslationWorker | None = None
        self._last_stats: ProjectStats | None = None

        central = QWidget()
        central.setObjectName("central")
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(14)

        # Заголовок
        title_row = QHBoxLayout()
        title = QLabel("RPG Maker Translator")
        title.setStyleSheet(
            "font-size: 18pt; font-weight: 700; color: #E6E1D7; letter-spacing: 0.5px;"
        )
        subtitle = QLabel("MV / MZ  ·  JSON-парсер  ·  цепочки перевода")
        subtitle.setObjectName("hint")
        subtitle.setStyleSheet("color: #7A7264; font-size: 9pt; padding-left: 8px; padding-bottom: 3px;")
        title_row.addWidget(title)
        title_row.addWidget(subtitle)
        title_row.addStretch()
        root.addLayout(title_row)

        sep = QFrame()
        sep.setObjectName("separator")
        root.addWidget(sep)

        # Вкладки
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)

        self.tabs.addTab(wrap_in_scroll(self._build_translate_tab()), "Перевод")
        self.tabs.addTab(wrap_in_scroll(self._build_keys_tab()), "API-ключи")

        # Низ: фаза + прогресс
        bottom = QVBoxLayout()
        bottom.setSpacing(6)

        self.phase_label = QLabel("Готов к запуску")
        self.phase_label.setObjectName("phase")
        bottom.addWidget(self.phase_label)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        bottom.addWidget(self.progress)

        root.addLayout(bottom)

        # Если папка Data уже задана из конфига — запускаем автодетект шифрования
        if self.src_edit.text().strip():
            # Делаем через таймер, чтобы окно успело отрисоваться
            QTimer.singleShot(50, self._detect_encryption)

    # ── Завершение работы ──────────────────────────────────────────────────

    def closeEvent(self, event) -> None:
        """Корректно гасим фоновые потоки при закрытии окна.

        Без этого Qt уничтожал живой QThread («QThread: Destroyed while thread
        is still running»), а если перевод стоял на паузе — приложение вообще
        не закрывалось: поток ждал снятия паузы бесконечно.
        """
        worker = getattr(self, "worker", None)
        if worker is not None and worker.isRunning():
            reply = QMessageBox.question(
                self, "Перевод ещё идёт",
                "Перевод выполняется. Остановить и выйти?\n\n"
                "Прогресс сохранён в кэше — при следующем запуске "
                "работа продолжится с этого места.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            worker.request_stop()
            worker.request_resume()      # снимаем паузу, иначе поток не выйдет
            if not worker.wait(15000):
                worker.terminate()
                worker.wait(2000)

        for name in ("analysis_worker", "key_worker"):
            thread = getattr(self, name, None)
            if thread is not None and thread.isRunning():
                thread.wait(5000)

        event.accept()

    # ── Вкладка «Перевод» ──────────────────────────────────────────────────

    def _build_translate_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(16, 18, 16, 16)
        lay.setSpacing(12)

        # Папки
        paths_box = QGroupBox("ПУТИ")
        g = QGridLayout(paths_box)
        g.setHorizontalSpacing(10)
        g.setVerticalSpacing(8)
        # Колонка с лейблом узкая, с инпутом — растяжимая
        g.setColumnStretch(0, 0)
        g.setColumnStretch(1, 1)
        g.setColumnStretch(2, 0)

        g.addWidget(QLabel("Папка Data игры"), 0, 0)
        self.src_edit = QLineEdit(self.config.get("last_project_dir", ""))
        self.src_edit.setPlaceholderText("…/Game/www/data  или  …/Game/data")
        self.src_edit.textChanged.connect(lambda _: setattr(self, "_last_stats", None))
        g.addWidget(self.src_edit, 0, 1)
        src_btn = QPushButton("Обзор…")
        src_btn.clicked.connect(self._pick_src)
        g.addWidget(src_btn, 0, 2)

        g.addWidget(QLabel("Куда сохранить перевод"), 1, 0)
        self.dst_edit = QLineEdit()
        self.dst_edit.setPlaceholderText("Папка с переведённой копией Data")
        g.addWidget(self.dst_edit, 1, 1)
        dst_btn = QPushButton("Обзор…")
        dst_btn.clicked.connect(self._pick_dst)
        g.addWidget(dst_btn, 1, 2)

        hint = QLabel("Исходная папка не изменяется — создаётся копия со всеми переводами.")
        hint.setObjectName("hint")
        g.addWidget(hint, 2, 0, 1, 3)

        # ── Поле «Ключ шифрования» — для зашифрованных CryptoJS-игр ──────────
        # Скрыто по умолчанию; появляется когда автодетект увидел шифрование.
        g.addWidget(QLabel("Ключ шифрования"), 3, 0)

        crypto_row = QHBoxLayout()
        crypto_row.setContentsMargins(0, 0, 0, 0)
        crypto_row.setSpacing(6)
        self.crypto_key_edit = QLineEdit(self.config.get("encryption_key", ""))
        self.crypto_key_edit.setPlaceholderText(
            "Пусто = не зашифровано. Заполнится автоматически, если найден."
        )
        crypto_row.addWidget(self.crypto_key_edit, 1)
        self.crypto_auto_btn = QPushButton("Найти автоматически")
        self.crypto_auto_btn.setToolTip(
            "Поискать ключ в js/rmmz_managers.js рядом с папкой Data"
        )
        self.crypto_auto_btn.clicked.connect(self._find_crypto_key)
        crypto_row.addWidget(self.crypto_auto_btn)

        crypto_widget = QWidget()
        crypto_widget.setLayout(crypto_row)
        g.addWidget(crypto_widget, 3, 1, 1, 2)

        # Статусная плашка под полем
        self.crypto_status = QLabel("")
        self.crypto_status.setObjectName("hint")
        self.crypto_status.setWordWrap(True)
        self.crypto_status.setTextFormat(Qt.TextFormat.RichText)
        g.addWidget(self.crypto_status, 4, 0, 1, 3)

        lay.addWidget(paths_box)

        # Маршрут перевода
        route_box = QGroupBox("МАРШРУТ ПЕРЕВОДА")
        rg = QGridLayout(route_box)
        rg.setHorizontalSpacing(10)
        rg.setVerticalSpacing(8)
        # Лейбл-колонки 0,2 узкие, поле-колонки 1,3 — растяжимые
        rg.setColumnStretch(0, 0)
        rg.setColumnStretch(1, 1)
        rg.setColumnStretch(2, 0)
        rg.setColumnStretch(3, 1)

        rg.addWidget(QLabel("Исходный язык"), 0, 0)
        self.src_lang = QComboBox()
        for code, _ in LANGUAGES:
            self.src_lang.addItem(lang_display(code), code)
        rg.addWidget(self.src_lang, 0, 1)

        rg.addWidget(QLabel("Промежуточный (pivot)"), 0, 2)
        self.pivot_lang = QComboBox()
        self.pivot_lang.addItem("— без промежуточного —", "")
        for code, _ in LANGUAGES:
            self.pivot_lang.addItem(lang_display(code), code)
        rg.addWidget(self.pivot_lang, 0, 3)

        rg.addWidget(QLabel("Целевой язык"), 1, 0)
        self.dst_lang = QComboBox()
        for code, _ in LANGUAGES:
            self.dst_lang.addItem(lang_display(code), code)
        rg.addWidget(self.dst_lang, 1, 1)

        # Установка значений из конфига
        last_route = self.config.get("last_route", {})
        self._set_combo_value(self.src_lang, last_route.get("src", "ja"))
        self._set_combo_value(self.pivot_lang, last_route.get("pivot", "en") or "")
        self._set_combo_value(self.dst_lang, last_route.get("dst", "ru"))

        rg.addWidget(QLabel("Провайдер для стадии 1"), 2, 0)
        self.provider1 = QComboBox()
        self.provider1.addItems(PROVIDERS)
        rg.addWidget(self.provider1, 2, 1)

        rg.addWidget(QLabel("Провайдер для стадии 2"), 2, 2)
        self.provider2 = QComboBox()
        self.provider2.addItems(PROVIDERS)
        rg.addWidget(self.provider2, 2, 3)

        last_sp = self.config.get("last_stage_providers", ["DeepL", "DeepL"])
        if len(last_sp) >= 1:
            self._set_combo_value(self.provider1, last_sp[0])
        if len(last_sp) >= 2:
            self._set_combo_value(self.provider2, last_sp[1])

        self.pivot_lang.currentIndexChanged.connect(self._update_stage2_state)
        self._update_stage2_state()

        lay.addWidget(route_box)

        # Опции
        opt_box = QGroupBox("ОПЦИИ")
        og = QGridLayout(opt_box)
        og.setHorizontalSpacing(14)
        og.setVerticalSpacing(8)
        og.setColumnStretch(0, 0)
        og.setColumnStretch(1, 0)
        og.setColumnStretch(2, 1)  # пустая колонка-распорка

        self.fit_messages_cb = QCheckBox(
            "Пересобирать вёрстку сообщений (рекомендуется)"
        )
        self.fit_messages_cb.setChecked(self.config.get("fit_messages", True))
        self.fit_messages_cb.setToolTip(
            "Склеивает авторские переносы перед отправкой в переводчик, чтобы он "
            "видел целые предложения, а не обрывки строк. После перевода заново "
            "переносит текст по реальной ширине окна и разбивает его на окна по "
            "границам предложений.\n\n"
            "Без этой опции каждая визуальная строка переводится отдельно — "
            "именно из-за этого «Trigger Condition» превращается в «спусковой крючок»."
        )
        og.addWidget(self.fit_messages_cb, 0, 0, 1, 3)

        self.auto_glossary_cb = QCheckBox(
            "Защищать имена персонажей от перевода"
        )
        self.auto_glossary_cb.setChecked(self.config.get("auto_glossary", True))
        self.auto_glossary_cb.setToolTip(
            "Имена из Actors.json и тегов \\N<…> не отправляются в переводчик. "
            "Без этого персонаж Airy становится «Воздушным».\n"
            "Список правится в файле _glossary.json в выходной папке."
        )
        og.addWidget(self.auto_glossary_cb, 1, 0, 1, 3)

        self.group_dialogues_cb = QCheckBox("Склеивать соседние реплики для контекста")
        self.group_dialogues_cb.setChecked(self.config.get("group_dialogues", True))
        og.addWidget(self.group_dialogues_cb, 2, 0, 1, 3)

        self.text_wrap_cb = QCheckBox(
            "Ставить в игру страховочный модуль переноса (MV/MZ)"
        )
        self.text_wrap_cb.setChecked(self.config.get("install_text_wrap", True))
        self.text_wrap_cb.setToolTip(
            "Подстраховка на случай, если игрок сменит шрифт или другой плагин "
            "изменит ширину окна: модуль домеряет строки уже внутри игры."
        )
        og.addWidget(self.text_wrap_cb, 3, 0, 1, 3)

        og.addWidget(QLabel("Размер пакета:"), 4, 0)
        self.batch_spin = QSpinBox()
        self.batch_spin.setRange(1, 200)
        self.batch_spin.setValue(self.config.get("batch_size", 40))
        self.batch_spin.setMinimumWidth(80)
        self.batch_spin.setMaximumWidth(120)
        og.addWidget(self.batch_spin, 4, 1)

        opt_hint = QLabel(
            "Меньший пакет = больше запросов, но стабильнее. Для DeepL подходит 40–50, "
            "для Google лучше 20–30."
        )
        opt_hint.setObjectName("hint")
        opt_hint.setWordWrap(True)
        og.addWidget(opt_hint, 5, 0, 1, 3)

        lay.addWidget(opt_box)

        # Языковой фильтр
        lang_box = QGroupBox("ЯЗЫКОВОЙ ФИЛЬТР")
        self.lang_box_widget = lang_box
        lf_layout = QVBoxLayout(lang_box)
        lf_layout.setSpacing(6)

        self.lang_filter_hint = QLabel(
            "Нажми «Анализ объёма», чтобы увидеть, какие языки есть в проекте. "
            "Если переводишь частично переведённую игру — отметь только исходный язык, "
            "и уже переведённые строки останутся нетронутыми."
        )
        self.lang_filter_hint.setObjectName("hint")
        self.lang_filter_hint.setWordWrap(True)
        lf_layout.addWidget(self.lang_filter_hint)

        # Контейнер для чекбоксов — заполняется после анализа
        self.lang_checks_container = QWidget()
        self.lang_checks_layout = QHBoxLayout(self.lang_checks_container)
        self.lang_checks_layout.setContentsMargins(0, 4, 0, 0)
        self.lang_checks_layout.setSpacing(16)
        self.lang_checks: dict[str, QCheckBox] = {}
        lf_layout.addWidget(self.lang_checks_container)

        lay.addWidget(lang_box)

        # Кнопки управления
        ctrl_row = QHBoxLayout()

        self.analyze_btn = QPushButton("Анализ объёма")
        self.analyze_btn.clicked.connect(self._analyze)
        ctrl_row.addWidget(self.analyze_btn)

        self.test_btn = QPushButton("Тестовый прогон")
        self.test_btn.setToolTip(
            "Перевести только 1–2 файла для проверки качества и работоспособности"
        )
        self.test_btn.clicked.connect(self._test_run)
        ctrl_row.addWidget(self.test_btn)

        self.clear_cache_btn = QPushButton("Очистить кэш")
        self.clear_cache_btn.setToolTip(
            "Удалить _translation_cache.json из выходной папки — "
            "следующий запуск переведёт всё заново"
        )
        self.clear_cache_btn.clicked.connect(self._clear_cache)
        ctrl_row.addWidget(self.clear_cache_btn)

        self.preview_btn = QPushButton("Предпросмотр")
        self.preview_btn.setToolTip(
            "Показать случайные отрывки переведённых диалогов и проверить "
            "целостность управляющих кодов"
        )
        self.preview_btn.clicked.connect(self._show_preview)
        ctrl_row.addWidget(self.preview_btn)

        ctrl_row.addStretch()

        self.pause_btn = QPushButton("Пауза")
        self.pause_btn.setEnabled(False)
        self.pause_btn.clicked.connect(self._toggle_pause)
        ctrl_row.addWidget(self.pause_btn)

        self.stop_btn = QPushButton("Остановить")
        self.stop_btn.setObjectName("danger")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop)
        ctrl_row.addWidget(self.stop_btn)

        self.start_btn = QPushButton("Запустить перевод")
        self.start_btn.setObjectName("primary")
        self.start_btn.clicked.connect(self._start)
        ctrl_row.addWidget(self.start_btn)

        lay.addLayout(ctrl_row)

        # Лог
        log_box = QGroupBox("ЛОГ")
        lv = QVBoxLayout(log_box)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(2000)
        lv.addWidget(self.log_view)
        lay.addWidget(log_box, 1)

        return w

    # ── Вкладка «API-ключи» ────────────────────────────────────────────────

    def _build_keys_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(16, 18, 16, 16)
        lay.setSpacing(14)

        info = QLabel(
            "Ключи хранятся локально в зашифрованном виде. "
            "Google не требует ключа — пакет deep-translator работает без него."
        )
        info.setObjectName("hint")
        info.setWordWrap(True)
        lay.addWidget(info)

        # DeepL
        deepl_box = QGroupBox("DEEPL")
        dg = QGridLayout(deepl_box)
        dg.setColumnStretch(0, 0)
        dg.setColumnStretch(1, 1)
        dg.setColumnStretch(2, 0)
        dg.addWidget(QLabel("Auth Key"), 0, 0)
        self.deepl_key = QLineEdit(self.config["api_keys"].get("DeepL", ""))
        self.deepl_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.deepl_key.setPlaceholderText("Например: a1b2c3d4-…:fx")
        dg.addWidget(self.deepl_key, 0, 1)
        show_deepl = QPushButton("Показать")
        show_deepl.setCheckable(True)
        show_deepl.toggled.connect(
            lambda checked: self.deepl_key.setEchoMode(
                QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
            )
        )
        dg.addWidget(show_deepl, 0, 2)

        self.deepl_endpoint_label = QLabel("")
        self.deepl_endpoint_label.setObjectName("hint")
        dg.addWidget(self.deepl_endpoint_label, 1, 0, 1, 3)

        def _update_deepl_endpoint_label():
            key = self.deepl_key.text().strip()
            if not key:
                self.deepl_endpoint_label.setText(
                    "Endpoint определится автоматически по суффиксу ключа."
                )
            elif key.endswith(":fx"):
                self.deepl_endpoint_label.setText(
                    "✓ Free-ключ (оканчивается на «:fx») → api-free.deepl.com"
                )
            else:
                self.deepl_endpoint_label.setText(
                    "✓ Pro-ключ (без суффикса «:fx») → api.deepl.com"
                )

        self.deepl_key.textChanged.connect(lambda _: _update_deepl_endpoint_label())
        _update_deepl_endpoint_label()

        deepl_hint = QLabel(
            "Получить: deepl.com/pro-api → раздел «API Keys» (это не то же, что подписка DeepL Pro). "
            "Free тариф — 500 000 символов в месяц."
        )
        deepl_hint.setObjectName("hint")
        deepl_hint.setWordWrap(True)
        dg.addWidget(deepl_hint, 2, 0, 1, 3)
        lay.addWidget(deepl_box)

        # Yandex
        yandex_box = QGroupBox("YANDEX")
        yg = QGridLayout(yandex_box)
        yg.setColumnStretch(0, 0)
        yg.setColumnStretch(1, 1)
        yg.setColumnStretch(2, 0)
        yg.addWidget(QLabel("API-ключ"), 0, 0)
        self.yandex_key = QLineEdit(self.config["api_keys"].get("Yandex", ""))
        self.yandex_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.yandex_key.setPlaceholderText("AQVN…  или  AQVN…|b1g…folder_id")
        yg.addWidget(self.yandex_key, 0, 1)
        show_y = QPushButton("Показать")
        show_y.setCheckable(True)
        show_y.toggled.connect(
            lambda checked: self.yandex_key.setEchoMode(
                QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
            )
        )
        yg.addWidget(show_y, 0, 2)

        yg.addWidget(QLabel("Folder ID"), 1, 0)
        self.yandex_folder = QLineEdit(self.config.get("yandex_folder_id", ""))
        self.yandex_folder.setPlaceholderText("b1g... (ID каталога Yandex Cloud)")
        yg.addWidget(self.yandex_folder, 1, 1, 1, 2)

        yandex_hint = QLabel(
            "Получить: console.cloud.yandex.ru → выбрать каталог → "
            "ID отображается в правом верхнем углу. Ключу нужна роль "
            "<b>ai.translate.user</b> в этом каталоге."
        )
        yandex_hint.setObjectName("hint")
        yandex_hint.setWordWrap(True)
        yandex_hint.setTextFormat(Qt.TextFormat.RichText)
        yg.addWidget(yandex_hint, 2, 0, 1, 3)
        lay.addWidget(yandex_box)

        # Google
        google_box = QGroupBox("GOOGLE  (без ключа)")
        gg = QVBoxLayout(google_box)
        google_hint = QLabel(
            "Использует пакет deep-translator (бесплатно, без ключа). "
            "Может ловить временные ограничения при больших объёмах — тогда уменьши размер пакета."
        )
        google_hint.setObjectName("hint")
        google_hint.setWordWrap(True)
        gg.addWidget(google_hint)
        lay.addWidget(google_box)

        save_row = QHBoxLayout()
        save_row.addStretch()
        save_btn = QPushButton("Сохранить ключи")
        save_btn.setObjectName("primary")
        save_btn.clicked.connect(self._save_keys)
        save_row.addWidget(save_btn)
        lay.addLayout(save_row)

        lay.addStretch()
        return w

    # ── Логика ─────────────────────────────────────────────────────────────

    def _set_combo_value(self, combo: QComboBox, value: str) -> None:
        for i in range(combo.count()):
            if combo.itemData(i) == value or combo.itemText(i) == value:
                combo.setCurrentIndex(i)
                return

    def _update_stage2_state(self) -> None:
        has_pivot = bool(self.pivot_lang.currentData())
        self.provider2.setEnabled(has_pivot)

    def _pick_src(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Папка Data игры")
        if d:
            self.src_edit.setText(d)
            self._last_stats = None  # сброс кэша
            # Автоподстановка выходной папки
            if not self.dst_edit.text():
                p = Path(d)
                self.dst_edit.setText(str(p.parent / f"{p.name}_translated"))
            self._detect_encryption()

    def _detect_encryption(self) -> None:
        """Проверяет, зашифрованы ли файлы, и пытается найти ключ автоматически."""
        d = self.src_edit.text().strip()
        if not d or not Path(d).exists():
            self.crypto_status.setText("")
            return

        try:
            from core.crypto import is_data_dir_encrypted
        except ImportError as e:
            self.crypto_status.setText(
                f"⚠ Не загружен модуль шифрования: {e}. "
                f"Установи зависимости: <code>pip install cryptography</code>"
            )
            return

        if not is_data_dir_encrypted(Path(d)):
            # Файлы открытые — поле всё равно оставляем, но информируем
            if not self.crypto_key_edit.text().strip():
                self.crypto_status.setText("Файлы не зашифрованы.")
            return

        # Зашифровано. Если в поле уже что-то — оставляем как есть, проверим
        # рабочий ли ключ. Если пусто — пытаемся найти.
        existing = self.crypto_key_edit.text().strip()
        if existing:
            from core.crypto import _key_works
            if _key_works(existing, Path(d)):
                self.crypto_status.setText(
                    "🔒 Файлы зашифрованы CryptoJS AES. "
                    "✓ Введённый ключ <b>работает</b>."
                )
                return
            else:
                self.crypto_status.setText(
                    "🔒 Файлы зашифрованы, но <b>текущий ключ не подходит</b>. "
                    "Очисти поле и нажми «Найти автоматически» или введи нужный."
                )
                return

        # Поиск ключа — в фоне: перебор кандидатов с расшифровкой AES
        # раньше намертво вешал окно на десятки секунд.
        self.crypto_status.setText("🔒 Файлы зашифрованы. Ищу ключ…")
        self.key_worker = KeySearchWorker(d, parent=self)
        self.key_worker.done.connect(self._on_key_found)
        self.key_worker.start()

    def _on_key_found(self, key, managers_path) -> None:
        where = Path(managers_path).name if managers_path else "js/rmmz_managers.js"
        if key:
            self.crypto_key_edit.setText(key)
            self.crypto_status.setText(
                f"🔒 Файлы зашифрованы CryptoJS AES. ✓ Ключ найден автоматически "
                f"в <code>{where}</code>."
            )
            self._log("success", f"Ключ шифрования найден: {key[:4]}…")
        else:
            self.crypto_status.setText(
                "🔒 Файлы зашифрованы CryptoJS AES, но <b>ключ найти не удалось</b>.<br>"
                f"Проверь сам: открой <code>{where}</code> рядом с папкой data, "
                "найди <code>CryptoJS.AES.decrypt(x, '…')</code> "
                "и впиши строку из кавычек выше."
            )

    def _find_crypto_key(self) -> None:
        """Кнопка «Найти автоматически»: повторный запуск автодетекта."""
        d = self.src_edit.text().strip()
        if not d or not Path(d).exists():
            QMessageBox.information(
                self, "Поиск ключа",
                "Сначала укажи папку Data игры.",
            )
            return
        try:
            from core.crypto import auto_find_key
        except ImportError as e:
            QMessageBox.critical(
                self, "Шифрование",
                f"Не загружен модуль шифрования: {e}\n\n"
                f"Установи зависимости:\npip install cryptography",
            )
            return

        # Если автоматический поиск рядом с папкой Data не дал результата,
        # предлагаем выбрать managers.js руками
        key, managers_path = auto_find_key(Path(d))
        if key:
            self.crypto_key_edit.setText(key)
            self.crypto_status.setText(
                f"🔒 ✓ Ключ найден автоматически "
                f"в <code>{managers_path.name}</code>."
            )
            self._log("success", f"Ключ шифрования найден: {key[:4]}…")
            return

        # Не нашли автоматически — предлагаем выбрать файл вручную
        reply = QMessageBox.question(
            self, "Ключ не найден",
            "Не удалось найти ключ автоматически.\n\n"
            "Выбрать файл rmmz_managers.js вручную?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        f, _ = QFileDialog.getOpenFileName(
            self, "Выбери rmmz_managers.js",
            str(Path(d).parent) if Path(d).exists() else "",
            "JavaScript files (*.js);;All files (*.*)",
        )
        if not f:
            return
        from core.crypto import find_key_in_managers_js, _collect_key_candidates, _key_works
        try:
            with open(f, "r", encoding="utf-8", errors="ignore") as fp:
                text = fp.read()
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось прочитать файл:\n{e}")
            return

        # Сначала прямой шаблон
        direct = find_key_in_managers_js(text)
        if direct and _key_works(direct, Path(d)):
            self.crypto_key_edit.setText(direct)
            self.crypto_status.setText("🔒 ✓ Ключ найден в выбранном файле.")
            return

        # Кандидаты с проверкой
        for c in _collect_key_candidates(text):
            if _key_works(c, Path(d)):
                self.crypto_key_edit.setText(c)
                self.crypto_status.setText("🔒 ✓ Ключ найден в выбранном файле.")
                return

        QMessageBox.warning(
            self, "Ключ не найден",
            "В выбранном файле не удалось найти рабочий ключ.\n\n"
            "Найди вручную: в файле должна быть строка вроде\n"
            "  CryptoJS.AES.decrypt(x, '…')\n\n"
            "Скопируй то, что внутри кавычек, и вставь в поле «Ключ шифрования».",
        )

    def _make_crypto_or_warn(self):
        """Возвращает GameCrypto если поле непустое, None если пустое,
        False если ключ задан но не работает (с показом предупреждения)."""
        key = self.crypto_key_edit.text().strip()
        if not key:
            return None
        try:
            from core.crypto import GameCrypto, GameCryptoError, is_data_dir_encrypted, _key_works
        except ImportError as e:
            QMessageBox.critical(
                self, "Шифрование",
                f"Не загружен модуль шифрования: {e}\n\n"
                f"Установи зависимости:\npip install cryptography",
            )
            return False
        src_dir = self.src_edit.text().strip()
        # Если файлы НЕ зашифрованы, но ключ задан — предупредим, что ключ зря
        if src_dir and Path(src_dir).exists():
            if not is_data_dir_encrypted(Path(src_dir)):
                # Это не ошибка, но стоит сказать
                self._log("warn", "Ключ задан, но файлы не зашифрованы — ключ будет проигнорирован.")
                return None
            # Если ключ задан и файлы зашифрованы — проверим что работает
            if not _key_works(key, Path(src_dir)):
                QMessageBox.critical(
                    self, "Неверный ключ",
                    "Введённый ключ шифрования не подходит — файлы не расшифровываются.\n\n"
                    "Очисти поле и нажми «Найти автоматически», или проверь ключ вручную.",
                )
                return False
        try:
            return GameCrypto(key)
        except GameCryptoError as e:
            QMessageBox.critical(self, "Шифрование", f"Ошибка: {e}")
            return False

    def _pick_dst(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Выходная папка")
        if d:
            self.dst_edit.setText(d)

    def _save_keys(self) -> None:
        self._sync_keys_from_fields()
        save_config(self.config)
        self._log("success", "Ключи сохранены")

    def _sync_keys_from_fields(self) -> None:
        """Переносит содержимое полей ключей в конфиг.

        Раньше ключ применялся только после нажатия «Сохранить ключи»: человек
        вставлял ключ, сразу жал «Запустить перевод» и получал «Для DeepL нужен
        API-ключ». Теперь поля — источник истины.
        """
        self.config["api_keys"]["DeepL"] = self.deepl_key.text().strip()
        self.config["api_keys"]["Yandex"] = self.yandex_key.text().strip()
        self.config["yandex_folder_id"] = self.yandex_folder.text().strip()

    def _collect_route_and_providers(self, require_keys: bool = True
                                     ) -> tuple[TranslationRoute, list[tuple]] | None:
        self._sync_keys_from_fields()
        src = self.src_lang.currentData()
        pivot = self.pivot_lang.currentData() or None
        dst = self.dst_lang.currentData()
        if not src or not dst:
            QMessageBox.warning(self, "Маршрут", "Выберите исходный и целевой языки.")
            return None
        if src == dst:
            QMessageBox.warning(self, "Маршрут", "Исходный и целевой языки совпадают.")
            return None
        route = TranslationRoute(src=src, pivot=pivot, dst=dst)

        keys = self.config["api_keys"]
        providers: list[tuple] = []
        for i, (s, d) in enumerate(route.stages()):
            combo = self.provider1 if i == 0 else self.provider2
            pname = combo.currentText()
            api_key = keys.get(pname, "")
            extra: dict = {}
            if pname in ("DeepL", "Yandex") and not api_key:
                if not require_keys:
                    return None      # анализу ключи не нужны, молча выходим
                QMessageBox.warning(
                    self, "API-ключ",
                    f"Для {pname} нужен API-ключ. Перейди на вкладку «API-ключи».",
                )
                return None
            if pname == "Yandex":
                folder_id = self.config.get("yandex_folder_id", "").strip()
                if not folder_id:
                    if not require_keys:
                        return None
                    QMessageBox.warning(
                        self, "Yandex",
                        "Для Yandex нужен Folder ID. Перейди на вкладку «API-ключи» "
                        "и заполни поле «Folder ID».",
                    )
                    return None
                extra["folder_id"] = folder_id
            providers.append((pname, api_key, extra))
        return route, providers

    def _show_preview(self) -> None:
        """Открывает диалог предпросмотра со случайными отрывками перевода."""
        dst_dir = self.dst_edit.text().strip()
        src_dir = self.src_edit.text().strip()
        if not dst_dir or not Path(dst_dir).exists():
            QMessageBox.information(
                self, "Предпросмотр",
                "Сначала нужен переведённый проект.\n\n"
                "Укажи выходную папку с уже выполненным (хотя бы частично) переводом."
            )
            return

        crypto = self._make_crypto_or_warn()
        if crypto is False:
            return

        # Определяем i18n_field по выбранному исходному языку
        from core.rpgmaker_parser import RPGMakerProject as _RP
        i18n_field = _RP.I18N_FIELD_BY_LANG.get(self.src_lang.currentData() or "")

        try:
            from core.preview import build_preview
            items = build_preview(src_dir or dst_dir, dst_dir,
                                  crypto=crypto, i18n_field=i18n_field, sample_size=8)
        except Exception as e:
            QMessageBox.critical(self, "Предпросмотр", f"Не удалось построить предпросмотр:\n{e}")
            return

        if not items:
            QMessageBox.information(
                self, "Предпросмотр",
                "В переведённом проекте не найдено диалогов для показа."
            )
            return

        dlg = PreviewDialog(items, parent=self,
                            on_refresh=lambda: self._refresh_preview_items(
                                src_dir or dst_dir, dst_dir, crypto, i18n_field))
        dlg.exec()

    def _refresh_preview_items(self, src_dir, dst_dir, crypto, i18n_field):
        """Колбэк для кнопки «Другие отрывки» в диалоге предпросмотра."""
        from core.preview import build_preview
        return build_preview(src_dir, dst_dir, crypto=crypto,
                             i18n_field=i18n_field, sample_size=8)

    def _clear_cache(self) -> None:
        dst_dir = self.dst_edit.text().strip()
        if not dst_dir:
            QMessageBox.information(
                self, "Кэш",
                "Сначала укажи выходную папку — кэш хранится в ней."
            )
            return
        cache_file = Path(dst_dir) / "_translation_cache.json"
        if not cache_file.exists():
            QMessageBox.information(
                self, "Кэш",
                f"Файл кэша не найден:\n{cache_file}\n\n"
                "(Это нормально, если ты ещё не запускал перевод в эту папку.)"
            )
            return
        reply = QMessageBox.question(
            self, "Удалить кэш?",
            f"Удалить файл кэша?\n\n{cache_file}\n\n"
            "После удаления следующий запуск переведёт всё заново "
            "(без подхвата прогресса).",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            try:
                cache_file.unlink()
                self._log("success", f"Кэш удалён: {cache_file}")
            except Exception as e:
                QMessageBox.critical(self, "Ошибка", f"Не удалось удалить:\n{e}")

    def _start(self) -> None:
        self._start_translation(test_files=None)

    def _start_translation(self, test_files: list[str] | None) -> None:
        src_dir = self.src_edit.text().strip()
        dst_dir = self.dst_edit.text().strip()
        if not src_dir or not Path(src_dir).exists():
            QMessageBox.warning(self, "Путь", "Исходная папка не существует.")
            return
        if not dst_dir:
            QMessageBox.warning(self, "Путь", "Укажи выходную папку.")
            return

        # Выходная папка удаляется целиком перед копированием проекта, поэтому
        # опасные варианты (выбрали www, выбрали Рабочий стол) отсекаем ЗДЕСЬ,
        # пока ничего не удалено.
        from core.safety import check_output_dir, UnsafeOutputDir
        probe_out = dst_dir
        if test_files:
            probe_out = str(Path(dst_dir).with_name(Path(dst_dir).name + "_TEST"))
        try:
            check_output_dir(src_dir, probe_out)
        except UnsafeOutputDir as exc:
            QMessageBox.critical(self, "Опасная выходная папка", str(exc))
            return
        if Path(probe_out).exists() and not (Path(probe_out) / "_translation_cache.json").exists():
            reply = QMessageBox.question(
                self, "Папка будет очищена",
                f"Папка уже существует и будет УДАЛЕНА целиком перед копированием:\n\n"
                f"{probe_out}\n\nПродолжить?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        rp = self._collect_route_and_providers()
        if not rp:
            return
        route, providers = rp

        # Сохраняем настройки маршрута
        self.config["last_project_dir"] = src_dir
        self.config["last_route"] = {
            "src": route.src, "pivot": route.pivot or "", "dst": route.dst,
        }
        self.config["last_stage_providers"] = [p[0] for p in providers]
        self.config["batch_size"] = self.batch_spin.value()
        self.config["group_dialogues"] = self.group_dialogues_cb.isChecked()
        self.config["install_text_wrap"] = self.text_wrap_cb.isChecked()
        self.config["fit_messages"] = self.fit_messages_cb.isChecked()
        self.config["auto_glossary"] = self.auto_glossary_cb.isChecked()
        lang_filter = self._get_language_filter()
        self.config["lang_filter"] = lang_filter or []
        encryption_key = self.crypto_key_edit.text().strip()
        self.config["encryption_key"] = encryption_key
        save_config(self.config)

        # Тестовому прогону — отдельная выходная папка, чтобы не перезаписать
        # потенциальный полный перевод
        out_dir = dst_dir
        if test_files:
            out_dir = str(Path(dst_dir).with_name(Path(dst_dir).name + "_TEST"))

        self.log_view.clear()
        self.progress.setValue(0)
        self.start_btn.setEnabled(False)
        self.analyze_btn.setEnabled(False)
        self.test_btn.setEnabled(False)
        self.clear_cache_btn.setEnabled(False)
        self.preview_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.pause_btn.setEnabled(True)
        self.pause_btn.setText("Пауза")

        if test_files:
            self._log("info", f"Тестовый прогон: {len(test_files)} файл(ов) → {out_dir}")
        if lang_filter:
            self._log("info", f"Языковой фильтр: {', '.join(lang_filter).upper()}")
        if encryption_key:
            self._log("info", "🔒 Файлы будут расшифрованы при чтении и зашифрованы при записи")

        self.worker = TranslationWorker(
            project_dir=src_dir,
            output_dir=out_dir,
            route=route,
            stage_providers=providers,
            batch_size=self.batch_spin.value(),
            group_dialogues=self.group_dialogues_cb.isChecked(),
            test_files=test_files,
            lang_filter=lang_filter,
            encryption_key=encryption_key or None,
            install_text_wrap=self.text_wrap_cb.isChecked(),
            fit_messages=self.fit_messages_cb.isChecked(),
            auto_glossary=self.auto_glossary_cb.isChecked(),
        )
        self.worker.log.connect(self._log)
        self.worker.progress.connect(self._on_progress)
        self.worker.phase.connect(self._on_phase)
        self.worker.finished_ok.connect(self._on_finished)
        self.worker.failed.connect(self._on_failed)
        self.worker.paused_state.connect(self._on_paused_state)
        self.worker.start()

    def _stop(self) -> None:
        if self.worker:
            self.worker.request_stop()
            self._log("warn", "Запрошена остановка…")

    def _toggle_pause(self) -> None:
        if not self.worker:
            return
        if self.worker.is_paused:
            self.worker.request_resume()
        else:
            self.worker.request_pause()

    def _on_paused_state(self, paused: bool) -> None:
        if paused:
            self.pause_btn.setText("Продолжить")
            self.pause_btn.setObjectName("primary")
        else:
            self.pause_btn.setText("Пауза")
            self.pause_btn.setObjectName("")
        # Перерисовать кнопку с новым стилем
        self.pause_btn.style().unpolish(self.pause_btn)
        self.pause_btn.style().polish(self.pause_btn)

    def _analyze(self) -> None:
        src_dir = self.src_edit.text().strip()
        if not src_dir or not Path(src_dir).exists():
            QMessageBox.warning(self, "Путь", "Укажи папку Data игры — её нужно проанализировать.")
            return

        rp = self._collect_route_and_providers(require_keys=False)
        if rp is None:
            # Анализ может быть без валидных ключей — нам важен только маршрут
            src_l = self.src_lang.currentData() or "?"
            pivot_l = self.pivot_lang.currentData() or ""
            dst_l = self.dst_lang.currentData() or "?"
            num_stages = 2 if pivot_l else 1
            if pivot_l:
                route_label = f"{src_l.upper()} → {pivot_l.upper()} → {dst_l.upper()}"
            else:
                route_label = f"{src_l.upper()} → {dst_l.upper()}"
        else:
            route, _ = rp
            num_stages = len(route.stages())
            if route.is_pivot:
                route_label = f"{route.src.upper()} → {route.pivot.upper()} → {route.dst.upper()}"
            else:
                route_label = f"{route.src.upper()} → {route.dst.upper()}"

        # Определяем поле I18N по исходному языку маршрута
        from core.rpgmaker_parser import RPGMakerProject as _RP
        src_lang_for_i18n = (
            route.src if rp else (self.src_lang.currentData() or "")
        )
        i18n_field = _RP.I18N_FIELD_BY_LANG.get(src_lang_for_i18n)

        # Анализ в фоновом потоке: на большой игре он занимает десятки
        # секунд, и раньше всё это время окно не отвечало.
        crypto = self._make_crypto_or_warn()
        if crypto is False:      # ключ задан, но невалидный
            return

        self.analyze_btn.setEnabled(False)
        self.test_btn.setEnabled(False)
        self.start_btn.setEnabled(False)
        self.analyze_btn.setText("Анализ…")
        self.phase_label.setText("Анализ проекта…")

        self._pending_analysis = (route_label, num_stages)
        self.analysis_worker = AnalysisWorker(
            src_dir,
            self.group_dialogues_cb.isChecked(),
            crypto,
            i18n_field,
            parent=self,
        )
        self.analysis_worker.done.connect(self._on_analysis_done)
        self.analysis_worker.failed.connect(self._on_analysis_failed)
        self.analysis_worker.phase.connect(self.phase_label.setText)
        self.analysis_worker.start()

    def _reset_analysis_buttons(self) -> None:
        self.analyze_btn.setEnabled(True)
        self.test_btn.setEnabled(True)
        self.start_btn.setEnabled(True)
        self.analyze_btn.setText("Анализ объёма")
        self.phase_label.setText("Готов к запуску")

    def _on_analysis_done(self, stats: ProjectStats) -> None:
        self._reset_analysis_buttons()
        self._last_stats = stats
        self._refresh_language_checks(stats)
        route_label, num_stages = getattr(self, "_pending_analysis", ("", 1))
        if getattr(self, "_analysis_then_test", False):
            self._analysis_then_test = False
            self._open_file_selection()
            return
        dlg = AnalysisDialog(stats, route_label, num_stages, parent=self)
        dlg.exec()

    def _on_analysis_failed(self, message: str) -> None:
        self._reset_analysis_buttons()
        self._analysis_then_test = False
        QMessageBox.critical(self, "Ошибка анализа", message)

    def _refresh_language_checks(self, stats: ProjectStats) -> None:
        """Заполняет секцию языкового фильтра чекбоксами на основе анализа."""
        from core.lang_detect import LANG_NAMES
        # Очищаем старые чекбоксы
        for cb in list(self.lang_checks.values()):
            self.lang_checks_layout.removeWidget(cb)
            cb.deleteLater()
        self.lang_checks.clear()

        if not stats.by_language:
            return

        # Восстанавливаем предыдущий фильтр из конфига
        prev_filter = set(self.config.get("lang_filter", []) or [])

        # По убыванию частоты
        ordered = sorted(stats.by_language.items(), key=lambda x: -x[1])
        for lang, cnt in ordered:
            if lang == "unknown":
                continue  # пустышки и пунктуацию в фильтр не пускаем — пользователю это бесполезно
            name = LANG_NAMES.get(lang, lang)
            cb = QCheckBox(f"{name} ({cnt})")
            cb.setProperty("lang_code", lang)
            # Если фильтр был задан, восстанавливаем; иначе все отмечены (= переводим всё)
            cb.setChecked(lang in prev_filter if prev_filter else True)
            self.lang_checks[lang] = cb
            self.lang_checks_layout.addWidget(cb)
        self.lang_checks_layout.addStretch()

        self.lang_filter_hint.setText(
            "Отметь языки, которые нужно перевести. Снятые галки = строки на этих "
            "языках остаются без изменений (их не отправят в API)."
        )

    def _get_language_filter(self) -> list[str] | None:
        """Возвращает список выбранных языков, или None если все отмечены / нет чекбоксов."""
        if not self.lang_checks:
            return None
        selected = [lang for lang, cb in self.lang_checks.items() if cb.isChecked()]
        # Если выбраны все — фильтр не нужен (это то же, что без фильтра)
        if len(selected) == len(self.lang_checks):
            return None
        return selected

    def _test_run(self) -> None:
        src_dir = self.src_edit.text().strip()
        if not src_dir or not Path(src_dir).exists():
            QMessageBox.warning(self, "Путь", "Сначала укажи папку Data игры.")
            return

        # Анализ нужен, чтобы показать список файлов. Он идёт в фоне, а диалог
        # выбора откроется в _on_analysis_done.
        if getattr(self, "_last_stats", None) is None:
            self._analysis_then_test = True
            self._analyze()
            return
        self._open_file_selection()

    def _open_file_selection(self) -> None:
        stats = self._last_stats
        if stats is None or not stats.by_file:
            QMessageBox.information(
                self, "Нет файлов",
                "В проекте не найдено файлов с переводимым текстом."
            )
            return

        dlg = FileSelectionDialog(stats, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        files = dlg.selected_files()
        if not files:
            return

        # Запускаем перевод только этих файлов
        self._start_translation(test_files=files)

    def _on_phase(self, phase: str) -> None:
        self.phase_label.setText(phase)
        self._log("info", f"▸ {phase}")

    def _on_progress(self, done: int, total: int, stage: str) -> None:
        if total <= 0:
            return
        pct = int(done / total * 100)
        self.progress.setValue(min(pct, 100))
        self.phase_label.setText(f"{stage} — {done}/{total} ({pct}%)")

    def _on_finished(self, out_dir: str) -> None:
        self.phase_label.setText("Готово ✓")
        self.progress.setValue(100)
        self._log("success", f"Перевод завершён. Результат: {out_dir}")
        self._reset_buttons()
        QMessageBox.information(self, "Готово", f"Перевод сохранён в:\n{out_dir}")

    def _on_failed(self, err: str) -> None:
        self.phase_label.setText("Ошибка")
        self._log("error", err)
        self._reset_buttons()
        QMessageBox.critical(self, "Ошибка", err)

    def _reset_buttons(self) -> None:
        self.start_btn.setEnabled(True)
        self.analyze_btn.setEnabled(True)
        self.test_btn.setEnabled(True)
        self.clear_cache_btn.setEnabled(True)
        self.preview_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.pause_btn.setEnabled(False)
        self.pause_btn.setText("Пауза")
        self.pause_btn.setObjectName("")
        self.pause_btn.style().unpolish(self.pause_btn)
        self.pause_btn.style().polish(self.pause_btn)

    def _log(self, level: str, message: str) -> None:
        colors = {
            "info":    "#C9C2B0",
            "warn":    "#E6B86A",
            "error":   "#E68A6A",
            "success": "#A6D49F",
        }
        color = colors.get(level, "#C9C2B0")
        prefix = {
            "info": "·", "warn": "!", "error": "✗", "success": "✓",
        }.get(level, "·")
        # Используем appendHtml для цветной строки
        safe = (message
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;"))
        self.log_view.appendHtml(
            f'<span style="color:{color};">{prefix} {safe}</span>'
        )


# ────────────────────────────────────────────────────────────────────────────
# Диалоги
# ────────────────────────────────────────────────────────────────────────────

# Лимит DeepL Free — 500 000 символов/месяц
DEEPL_FREE_LIMIT = 500_000


class AnalysisDialog(QDialog):
    """Показывает статистику проекта и оценку расхода символов."""

    def __init__(self, stats: ProjectStats, route_label: str, num_stages: int,
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle("Анализ объёма перевода")
        self.setMinimumSize(QSize(720, 560))

        lay = QVBoxLayout(self)
        lay.setSpacing(12)
        lay.setContentsMargins(20, 18, 20, 18)

        title = QLabel("Сводка по проекту")
        title.setStyleSheet(
            "font-size: 14pt; font-weight: 600; color: #E6E1D7;"
        )
        lay.addWidget(title)

        # Основная статистика
        box1 = QGroupBox("СТРОКИ И ПАКЕТЫ")
        g1 = QGridLayout(box1)
        g1.setColumnStretch(0, 0)
        g1.setColumnStretch(1, 1)
        rows = [
            ("Всего строк извлечено:", f"{stats.total_entries:,}"),
            ("    из них переводимых:", f"{stats.translatable_entries:,}"),
            ("    технических (пропуск):", f"{stats.technical_entries:,}"),
            ("Пакетов для перевода:", f"{stats.units_count:,}"),
            ("    с контекстом (>1 строки):", f"{stats.units_with_context:,}"),
        ]
        for i, (label, value) in enumerate(rows):
            lbl = QLabel(label)
            val = QLabel(value)
            val.setStyleSheet("color: #C9A86A; font-weight: 600;")
            g1.addWidget(lbl, i, 0)
            g1.addWidget(val, i, 1)
        lay.addWidget(box1)

        # Символы и расход
        box2 = QGroupBox("СИМВОЛЫ (для лимитов DeepL)")
        g2 = QGridLayout(box2)
        g2.setColumnStretch(0, 0)
        g2.setColumnStretch(1, 1)
        rows2 = [
            ("Символов переводимого текста:", f"{stats.translatable_chars:,}"),
            (f"Маршрут: {route_label}", ""),
        ]
        chars_estimate = stats.estimate_chars_per_stage(stages=num_stages)
        rows2.append(("Расход за весь перевод (≈):", f"{chars_estimate:,}"))

        # Доля от Free
        pct_of_free = chars_estimate / DEEPL_FREE_LIMIT * 100
        warning = ""
        if pct_of_free > 100:
            warning = " ⚠ ПРЕВЫШЕНИЕ FREE-ЛИМИТА"
            color = "#E68A6A"
        elif pct_of_free > 80:
            warning = " ⚠ близко к лимиту"
            color = "#E6B86A"
        else:
            color = "#A6D49F"
        rows2.append(
            ("От DeepL Free (500 000/мес):",
             f"{pct_of_free:.1f}%{warning}")
        )

        for i, (label, value) in enumerate(rows2):
            lbl = QLabel(label)
            val = QLabel(value)
            if "%" in value and warning:
                val.setStyleSheet(f"color: {color}; font-weight: 600;")
            else:
                val.setStyleSheet("color: #C9A86A; font-weight: 600;")
            g2.addWidget(lbl, i, 0)
            g2.addWidget(val, i, 1)
        lay.addWidget(box2)

        # Рекомендация
        if pct_of_free > 100:
            tip_text = (
                "Перевод не влезет в один месячный DeepL Free. Варианты:\n"
                "• Использовать Google (без ключа, без лимита) хотя бы на одной стадии\n"
                "• Прямой JP→RU вместо JP→EN→RU (вдвое меньше расход)\n"
                "• Разбить на месяцы (лимит сбрасывается ежемесячно)"
            )
            tip_color = "#E6B86A"
        elif pct_of_free > 80:
            tip_text = (
                "Перевод почти займёт весь месячный лимит DeepL Free. "
                "Запас на повторы небольшой — учти при тестировании."
            )
            tip_color = "#E6B86A"
        else:
            tip_text = "Запас по лимиту хороший. Можно запускать смело."
            tip_color = "#A6D49F"

        tip = QLabel(tip_text)
        tip.setWordWrap(True)
        tip.setStyleSheet(
            f"color: {tip_color}; background-color: #1C1A15; "
            f"border: 1px solid #2D2A22; border-radius: 6px; "
            f"padding: 10px 12px;"
        )
        lay.addWidget(tip)

        # Языки и файлы — в две колонки
        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(10)

        # Распределение по языкам
        from core.lang_detect import LANG_NAMES
        lang_box = QGroupBox("РАСПРЕДЕЛЕНИЕ ПО ЯЗЫКАМ")
        lv2 = QVBoxLayout(lang_box)
        lang_view = QPlainTextEdit()
        lang_view.setReadOnly(True)
        lang_view.setMaximumHeight(160)
        if stats.by_language:
            lang_lines = []
            for lang, cnt in sorted(stats.by_language.items(),
                                    key=lambda x: -x[1]):
                name = LANG_NAMES.get(lang, lang)
                chars = stats.chars_by_language.get(lang, 0)
                lang_lines.append(f"{name:<14}  {cnt:>5} строк, {chars:>7,} симв")
            lang_view.setPlainText("\n".join(lang_lines))
        else:
            lang_view.setPlainText("(данные о языках недоступны)")
        lv2.addWidget(lang_view)
        bottom_row.addWidget(lang_box, 1)

        # Список файлов
        files_box = QGroupBox("ФАЙЛЫ С ПЕРЕВОДИМЫМ ТЕКСТОМ")
        fv = QVBoxLayout(files_box)
        files_view = QPlainTextEdit()
        files_view.setReadOnly(True)
        files_view.setMaximumHeight(160)
        lines = [f"{f}  —  {stats.by_file[f]:>5} строк"
                 for f in sorted(stats.by_file, key=stats.by_file.get, reverse=True)]
        files_view.setPlainText("\n".join(lines))
        fv.addWidget(files_view)
        bottom_row.addWidget(files_box, 1)

        lay.addLayout(bottom_row, 1)

        # Кнопки
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("Закрыть")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        lay.addLayout(btn_row)


class FileSelectionDialog(QDialog):
    """Выбор 1-N файлов для тестового прогона. Сортировка по убыванию объёма."""

    def __init__(self, stats: ProjectStats, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Тестовый прогон — выбор файлов")
        self.setMinimumSize(QSize(520, 480))

        lay = QVBoxLayout(self)
        lay.setSpacing(12)
        lay.setContentsMargins(20, 18, 20, 18)

        title = QLabel("Выбери 1–2 файла для пробного перевода")
        title.setStyleSheet("font-size: 13pt; font-weight: 600; color: #E6E1D7;")
        lay.addWidget(title)

        hint = QLabel(
            "Это поможет проверить качество перевода и работоспособность "
            "ключей, не расходуя весь лимит. После теста запусти полный перевод."
        )
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        self.list = QListWidget()
        self.list.setSelectionMode(
            QAbstractItemView.SelectionMode.MultiSelection
        )
        # Сортируем по объёму — сверху самые большие, их пробовать интереснее
        for f in sorted(stats.by_file, key=stats.by_file.get, reverse=True):
            item = QListWidgetItem(f"{f}    ({stats.by_file[f]} строк)")
            item.setData(Qt.ItemDataRole.UserRole, f)
            self.list.addItem(item)
        lay.addWidget(self.list, 1)

        # Сводка выбора
        self.summary = QLabel("Выбрано: 0 файлов, 0 строк")
        self.summary.setObjectName("hint")
        lay.addWidget(self.summary)

        self._stats = stats
        self.list.itemSelectionChanged.connect(self._update_summary)

        # Кнопки
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("Отмена")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        self.ok_btn = QPushButton("Запустить тест")
        self.ok_btn.setObjectName("primary")
        self.ok_btn.setEnabled(False)
        self.ok_btn.clicked.connect(self.accept)
        btn_row.addWidget(self.ok_btn)
        lay.addLayout(btn_row)

    def _update_summary(self):
        selected = self.selected_files()
        total_lines = sum(self._stats.by_file.get(f, 0) for f in selected)
        self.summary.setText(
            f"Выбрано: {len(selected)} файл(ов), {total_lines} строк"
        )
        self.ok_btn.setEnabled(len(selected) > 0)

    def selected_files(self) -> list[str]:
        return [
            self.list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.list.count())
            if self.list.item(i).isSelected()
        ]


def _html_escape(s: str) -> str:
    """Экранирует текст для безопасного отображения в QTextBrowser."""
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


class PreviewDialog(QDialog):
    """Показывает случайные отрывки перевода рядом с оригиналом.

    Для каждого отрывка подсвечивает управляющие коды (\\N<...>, \\C[n] и т.д.)
    и помечает проблемы с их целостностью. Кнопка «Другие отрывки» подбирает
    новый случайный набор."""

    def __init__(self, items, parent=None, on_refresh=None):
        super().__init__(parent)
        self.on_refresh = on_refresh
        self.setWindowTitle("Предпросмотр перевода")
        self.setMinimumSize(QSize(760, 600))

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(10)

        title = QLabel("ПРЕДПРОСМОТР ПЕРЕВОДА")
        title.setStyleSheet("font-size: 13pt; font-weight: 600; color: #E6E1D7;")
        lay.addWidget(title)

        self.summary = QLabel("")
        self.summary.setObjectName("hint")
        self.summary.setWordWrap(True)
        lay.addWidget(self.summary)

        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(False)
        lay.addWidget(self.browser, 1)

        btn_row = QHBoxLayout()
        self.refresh_btn = QPushButton("Другие отрывки")
        self.refresh_btn.clicked.connect(self._do_refresh)
        if not self.on_refresh:
            self.refresh_btn.setEnabled(False)
        btn_row.addWidget(self.refresh_btn)
        btn_row.addStretch()
        close_btn = QPushButton("Закрыть")
        close_btn.setObjectName("primary")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        lay.addLayout(btn_row)

        self._render(items)

    def _do_refresh(self):
        if not self.on_refresh:
            return
        try:
            new_items = self.on_refresh()
        except Exception as e:
            self.summary.setText(f"Ошибка обновления: {e}")
            return
        if new_items:
            self._render(new_items)

    def _render(self, items):
        # Сводка
        ok = sum(1 for i in items if i.codes_ok)
        total = len(items)
        if ok == total:
            self.summary.setText(
                f"Показано {total} отрывков. ✓ Управляющие коды целы во всех."
            )
        else:
            self.summary.setText(
                f"Показано {total} отрывков. ⚠ Коды целы в {ok}/{total} — "
                f"проблемные отмечены красным ниже."
            )

        # Цвета (под тёмную тему)
        C_OK = "#7FB069"      # зелёный для статуса OK
        C_BAD = "#E68A6A"     # красный для проблем
        C_CODE = "#C9A86A"    # тёплый акцент для управляющих кодов
        C_ORIG = "#9A9382"    # приглушённый для оригинала
        C_LABEL = "#8A8474"
        C_TEXT = "#E8E2D0"

        def highlight_codes(text: str) -> str:
            """Экранирует текст и подсвечивает управляющие коды цветом."""
            import re
            escaped = _html_escape(text)
            # Подсветка кодов RPG Maker: \N<...>, \C[n], \V[n], \\, \. и т.д.
            # Работаем по уже экранированному тексту, поэтому < стало &lt;
            patterns = [
                r'\\N&lt;[^&]*?&gt;',          # \N<имя>
                r'\\[A-Za-z]+\[[^\]]*\]',      # \C[1], \V[5]
                r'\\\\',                        # \\
                r'\\[.!|^$G{}]',                # одиночные
            ]
            combined = "(" + "|".join(patterns) + ")"
            def repl(m):
                return f'<span style="color:{C_CODE};font-weight:bold">{m.group(0)}</span>'
            return re.sub(combined, repl, escaped)

        parts = ['<div style="font-family:sans-serif;font-size:13px;line-height:1.5">']
        for idx, item in enumerate(items):
            status_color = C_OK if item.codes_ok else C_BAD
            status_text = "✓ коды целы" if item.codes_ok else "⚠ проблема с кодами"
            border = C_OK if item.codes_ok else C_BAD

            parts.append(
                f'<div style="margin-bottom:16px;padding:10px;'
                f'border-left:3px solid {border};background:#1F1D18">'
            )
            parts.append(
                f'<div style="color:{C_LABEL};font-size:11px;margin-bottom:6px">'
                f'{_html_escape(item.file)} &nbsp;·&nbsp; '
                f'<span style="color:{status_color}">{status_text}</span></div>'
            )

            if item.original:
                parts.append(
                    f'<div style="color:{C_LABEL};font-size:11px">ОРИГИНАЛ:</div>'
                    f'<div style="color:{C_ORIG};margin-bottom:8px">'
                    f'{highlight_codes(item.original)}</div>'
                )
            parts.append(
                f'<div style="color:{C_LABEL};font-size:11px">ПЕРЕВОД:</div>'
                f'<div style="color:{C_TEXT}">{highlight_codes(item.translated)}</div>'
            )

            if item.issues:
                parts.append(
                    f'<div style="color:{C_BAD};font-size:11px;margin-top:6px">'
                )
                for iss in item.issues:
                    parts.append(f'⚠ {_html_escape(iss)}<br>')
                parts.append('</div>')

            parts.append('</div>')
        parts.append('</div>')

        self.browser.setHtml("".join(parts))


def run() -> None:
    import sys
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLESHEET)
    # Палитра как fallback
    pal = app.palette()
    pal.setColor(QPalette.ColorRole.Window, QColor("#14130F"))
    pal.setColor(QPalette.ColorRole.WindowText, QColor("#E6E1D7"))
    pal.setColor(QPalette.ColorRole.Base, QColor("#0F0E0B"))
    pal.setColor(QPalette.ColorRole.Text, QColor("#E6E1D7"))
    pal.setColor(QPalette.ColorRole.Button, QColor("#2D2A22"))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor("#E6E1D7"))
    pal.setColor(QPalette.ColorRole.Highlight, QColor("#C9A86A"))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#14130F"))
    app.setPalette(pal)

    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    run()
