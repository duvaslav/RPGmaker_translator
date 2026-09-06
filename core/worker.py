"""
Асинхронный воркер: парсинг, перевод, сборка проекта в отдельном потоке.
Поддерживает паузу, отмену, тестовый режим (только выбранные файлы).
"""
from __future__ import annotations

import json
import shutil
import threading
import traceback
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

from core.safety import UnsafeOutputDir, check_output_dir
from core.rpgmaker_parser import (
    RPGMakerProject, build_translation_units, split_translated_unit,
)
from core.translators import (
    ChainConfig, TranslationRoute, translate_with_chain, TranslationError, LOCAL_LLM,
)


class TranslationWorker(QThread):
    # Сигналы наружу
    log = pyqtSignal(str, str)              # (level, message)
    progress = pyqtSignal(int, int, str)    # (done, total, stage)
    phase = pyqtSignal(str)                 # текущая фаза
    finished_ok = pyqtSignal(str)           # путь к итоговой папке
    failed = pyqtSignal(str)
    paused_state = pyqtSignal(bool)         # True когда на паузе, False когда возобновлено

    def __init__(
        self,
        project_dir: str,
        output_dir: str,
        route: TranslationRoute,
        stage_providers: list[tuple],
        batch_size: int = 40,
        item_window: int = 1,
        group_dialogues: bool = True,
        test_files: list[str] | None = None,
        lang_filter: list[str] | None = None,
        encryption_key: str | None = None,
        install_text_wrap: bool = True,
        auto_glossary: bool = True,
        fit_messages: bool = True,
        parent=None,
    ):
        super().__init__(parent)
        self.project_dir = Path(project_dir)
        self.output_dir = Path(output_dir)
        self.route = route
        self.stage_providers = stage_providers
        self.batch_size = batch_size
        self.item_window = max(0, int(item_window))
        self.group_dialogues = group_dialogues
        # Если задан — переводим только указанные файлы
        self.test_files = set(test_files) if test_files else None
        # Если задан — переводим только строки на указанных языках
        self.lang_filter = set(lang_filter) if lang_filter else None
        # Если задан — файлы зашифрованы CryptoJS AES с этим ключом
        self.encryption_key = encryption_key.strip() if encryption_key else None
        self.install_text_wrap = install_text_wrap
        # Автоматически пополнять глоссарий именами из Actors.json
        self.auto_glossary = auto_glossary
        # Пересобирать вёрстку сообщений (расклейка + перенос по ширине окна)
        self.fit_messages = fit_messages

        self._stop = False
        # Event для паузы: set() = идём, clear() = ждём
        self._pause_event = threading.Event()
        self._pause_event.set()
        self._is_paused = False

    # ── Управление извне ───────────────────────────────────────────────────

    def request_stop(self) -> None:
        self._stop = True
        # На случай паузы — разблокируем, чтобы поток мог выйти
        self._pause_event.set()

    def request_pause(self) -> None:
        if not self._is_paused:
            self._pause_event.clear()
            self._is_paused = True
            self.paused_state.emit(True)

    def request_resume(self) -> None:
        if self._is_paused:
            self._pause_event.set()
            self._is_paused = False
            self.paused_state.emit(False)

    @property
    def is_paused(self) -> bool:
        return self._is_paused

    # ── Колбэки для translate_with_chain ───────────────────────────────────

    def _should_stop(self) -> bool:
        return self._stop

    def _wait_if_paused(self) -> None:
        """Блокируется до снятия паузы. Просыпается также при request_stop."""
        if not self._pause_event.is_set():
            self.phase.emit("⏸ Пауза — ожидание возобновления…")
            self.log.emit("warn", "Перевод приостановлен")
            # С таймаутом, а не навсегда: закрытие окна во время паузы раньше
            # оставляло поток висеть, и приложение не завершалось.
            while not self._pause_event.wait(timeout=0.2):
                if self._stop:
                    self._pause_event.set()
                    break
            if not self._stop:
                self.log.emit("info", "Возобновление перевода")
                self.phase.emit("Перевод…")

    # ── Основная работа ────────────────────────────────────────────────────

    def run(self) -> None:
        try:
            self._run()
        except TranslationError as e:
            self.failed.emit(str(e))
        except Exception as e:
            tb = traceback.format_exc()
            self.failed.emit(f"{e}\n\n{tb}")

    def _run(self) -> None:
        is_test = self.test_files is not None

        # 1. Копирование проекта
        # Особенность: если выходная папка УЖЕ существует и в ней есть кэш переводов,
        # значит это продолжение прерванной работы — не пересоздаём папку, переиспользуем.
        cache_path = self.output_dir / "_translation_cache.json"
        is_resume = self.output_dir.exists() and cache_path.exists() and not is_test

        if is_resume:
            self.phase.emit("Возобновление прерванной работы…")
            self.log.emit("info", "Найден кэш предыдущего запуска — возобновляем")
            self.log.emit("info", f"Папка: {self.output_dir}")
        else:
            if is_test:
                self.phase.emit("Копирование тестовой выборки…")
                self.log.emit("info", f"Тестовый режим: {len(self.test_files)} файл(ов)")
            else:
                self.phase.emit("Копирование проекта…")
            self.log.emit("info", f"Источник: {self.project_dir}")
            self.log.emit("info", f"Цель: {self.output_dir}")

            try:
                check_output_dir(self.project_dir, self.output_dir)
            except UnsafeOutputDir as exc:
                raise TranslationError(str(exc))
            if self.output_dir.exists():
                self.log.emit(
                    "warn",
                    f"Выходная папка существует и будет перезаписана: {self.output_dir}",
                )
                shutil.rmtree(self.output_dir)
            shutil.copytree(self.project_dir, self.output_dir)
            self.log.emit("success", "Копия проекта создана")

        if self._stop:
            self.log.emit("warn", "Остановлено пользователем")
            return

        # Инициализируем кэш ДО парсинга — пригодится для статистики
        from core.cache import TranslationCache
        cache = TranslationCache(cache_path)
        if len(cache) > 0:
            self.log.emit("info", f"В кэше: {len(cache)} ранее переведённых строк")

        # Инициализация шифрования, если задан ключ
        crypto = None
        if self.encryption_key:
            from core.crypto import GameCrypto, GameCryptoError
            try:
                crypto = GameCrypto(self.encryption_key)
                self.log.emit(
                    "info",
                    f"🔒 Шифрование активно (ключ: {self.encryption_key[:4]}…)",
                )
            except GameCryptoError as e:
                raise TranslationError(f"Ошибка инициализации шифрования: {e}")

        # Определяем поле для I18NTexts.json по исходному языку маршрута.
        # Например, маршрут en→ru → читаем/пишем поле "en_US" в I18NTexts.json
        # (заместим английский русским; игра при выборе English покажет русский).
        i18n_field = RPGMakerProject.I18N_FIELD_BY_LANG.get(self.route.src)
        i18n_active = (
            i18n_field is not None
            and (self.output_dir / "I18NTexts.json").exists()
        )
        if i18n_active:
            self.log.emit(
                "info",
                f"📚 Обнаружен I18NTexts.json — будут переведены тексты поля "
                f"{i18n_field} (замещение языка-источника).",
            )

        # Глоссарий: имена персонажей не должны переводиться как обычные слова.
        # Без него DeepL/Yandex превращают персонажа Airy в «Воздушного».
        from core.glossary import Glossary, build_auto_glossary, GLOSSARY_FILENAME
        glossary_path = self.output_dir / GLOSSARY_FILENAME
        glossary = Glossary.load(glossary_path)
        before = len(glossary)
        if self.auto_glossary:
            glossary = build_auto_glossary(self.project_dir, glossary)
        if len(glossary) != before or not glossary_path.exists():
            try:
                glossary.save(glossary_path)
            except OSError as exc:
                self.log.emit("warn", f"Не удалось сохранить глоссарий: {exc}")
        if glossary:
            self.log.emit(
                "info",
                f"📖 Глоссарий: {len(glossary)} терминов не пойдут в переводчик "
                f"(правится в {glossary_path.name})",
            )

        # 2. Парсинг (всегда заново — на случай если файлы изменились)
        self.phase.emit("Извлечение текста…")
        project = RPGMakerProject(self.output_dir, crypto=crypto, i18n_field=i18n_field,
                                  glossary=glossary, fit_messages=self.fit_messages)
        # ВАЖНО: парсим ИСХОДНУЮ папку, потому что output может уже содержать
        # частично переведённый текст. Для resume используем source.
        if is_resume:
            source_project = RPGMakerProject(
                self.project_dir, crypto=crypto, i18n_field=i18n_field,
                glossary=glossary, layout=project.layout,
                fit_messages=self.fit_messages,
            )
            source_project.extract_all()
            # Копируем entries, но привязываем к выходным файлам
            project.entries = source_project.entries
            # Однако _files_cache у нас будет для output папки, потому что apply_translations пишет туда
            project._files_cache = {}
        else:
            project.extract_all()

        if is_test:
            project.filter_to_files(self.test_files)
            self.log.emit(
                "info",
                f"После фильтра по тестовым файлам: {len(project.entries)} строк",
            )

        # Языковой фильтр
        if self.lang_filter:
            filtered_count = project.filter_to_languages(self.lang_filter)
            lang_list = ", ".join(sorted(self.lang_filter)).upper()
            self.log.emit(
                "info",
                f"Языковой фильтр [{lang_list}]: исключено {filtered_count} строк "
                f"на других языках",
            )

        translatable = [e for e in project.entries if e.needs_translation]
        technical = len(project.entries) - len(translatable)
        self.log.emit(
            "info",
            f"Всего строк: {len(project.entries)} | "
            f"переводимых: {len(translatable)} | технических: {technical}",
        )

        if not project.entries:
            raise TranslationError(
                "Не найдено ни одной строки. "
                "Проверь, что выбрана папка Data/www/data."
            )
        if not translatable:
            raise TranslationError(
                "Все найденные строки отфильтрованы. Переводить нечего."
            )

        # 3. Группировка
        self.phase.emit("Подготовка пакетов…")
        # item_window — сколько соседних реплик кладётся в контекст КАЖДОГО
        # элемента (для провайдеров, принимающих элемент целиком). Считается
        # только внутри своей сцены; шире — не лучше: на широком контексте
        # модель чаще теряла управляющие коды.
        units = build_translation_units(
            project.entries, group_dialogues=self.group_dialogues,
            item_window=self.item_window,
        )
        multi = sum(1 for u in units if len(u.entry_indices) > 1)
        self.log.emit(
            "info",
            f"Пакетов: {len(units)} (из них с контекстом: {multi})",
        )

        # 4. Перевод с кэшем
        self.phase.emit("Перевод…")
        cfg = ChainConfig(route=self.route, stage_providers=self.stage_providers)
        cfg.validate()
        unit_texts = [u.combined_text for u in units]
        unit_contexts = [u.context for u in units]

        # Провайдеры, принимающие элемент целиком (локальная модель), получают
        # для каждой строки свой контекст и метаданные. Общая строка контекста
        # на весь пакет им не подходит: пакет может собрать реплики из разных
        # сцен, и подсказка от чужой сцены увела бы перевод.
        unit_items_list = None
        batch_size = self.batch_size
        local_stages = [p for p in self.stage_providers if p[0] == LOCAL_LLM]
        if local_stages:
            from core.local_llm import glossary_fingerprint, unit_items
            unit_items_list = unit_items(units, project.glossary)
            # Глоссарий обязан входить в ключ кэша: он меняет и то, что видит
            # модель, и то, что до неё доходит вообще — закрытые им термины
            # заменяются плейсхолдерами ещё до отправки.
            version = glossary_fingerprint(glossary)
            for stage in self.stage_providers:
                if stage[0] == LOCAL_LLM and len(stage) > 2 and isinstance(stage[2], dict):
                    stage[2]["glossary_version"] = version
            # Размер пакета для локальной модели свой и заметно меньше: она
            # держит ответ целиком в бюджете токенов, и на большом пакете он
            # обрывается по лимиту, а вместе с ним рушится весь JSON.
            extra = local_stages[0][2] if len(local_stages[0]) > 2 else {}
            local_batch = int(extra.get("batch_size") or 5)
            if batch_size > local_batch:
                self.log.emit(
                    "info",
                    f"Размер пакета уменьшен до {local_batch}: столько "
                    "выдерживает локальная модель.",
                )
                batch_size = local_batch

        # При возобновлении подсчёт «уже переведённых» из кэша
        if is_resume or len(cache) > 0:
            # Считаем, сколько unit'ов уже целиком в кэше
            first_stage = self.route.stages()[0]
            src, dst = first_stage
            in_cache = sum(1 for t in unit_texts if cache.has(src, dst, t))
            if in_cache > 0:
                pct = in_cache / len(unit_texts) * 100 if unit_texts else 0
                self.log.emit(
                    "info",
                    f"Уже в кэше: {in_cache}/{len(unit_texts)} пакетов "
                    f"({pct:.1f}%) — будут пропущены",
                )

        # Главный вызов. Любой выход (успех/ошибка/stop) — сохраняем то, что есть.
        translated_texts: list[str] = []
        translation_error: Exception | None = None
        chain_stats: dict = {}
        try:
            translated_texts = translate_with_chain(
                unit_texts,
                cfg,
                batch_size=batch_size,
                contexts=unit_contexts,
                progress_cb=lambda d, t, s: self.progress.emit(d, t, s),
                should_stop=self._should_stop,
                wait_if_paused=self._wait_if_paused,
                cache=cache,
                stats=chain_stats,
                items=unit_items_list,
            )
        except TranslationError as e:
            translation_error = e
            self.log.emit("error", f"Перевод прерван ошибкой: {e}")
            # Восстанавливаем то, что успели сохранить в кэше
            translated_texts = self._recover_from_cache(unit_texts, cache, self.route.stages())
        except Exception as e:
            translation_error = e
            self.log.emit("error", f"Непредвиденная ошибка: {e}")
            translated_texts = self._recover_from_cache(unit_texts, cache, self.route.stages())

        failed_items = chain_stats.get("failed_items") or []
        if failed_items:
            # Квитанция по каждой строке, которую переводчик не смог отдать в
            # приемлемом виде. «Перевод завершён» не должно означать «всё
            # переведено»: эти строки остались исходным текстом.
            report_path = self.output_dir / "_translation_failures.json"
            try:
                report_path.write_text(
                    json.dumps({"count": len(failed_items), "items": failed_items},
                               ensure_ascii=False, indent=1),
                    encoding="utf-8",
                )
                self.log.emit(
                    "warn",
                    f"⚠ {len(failed_items)} строк не прошли проверку и остались "
                    f"исходным текстом. Список: {report_path.name}",
                )
            except OSError as e:
                self.log.emit("warn", f"Не удалось записать отчёт об отказах: {e}")

        if chain_stats.get("rejected_placeholders"):
            self.log.emit(
                "warn",
                f"⚠ Провайдер повредил управляющие коды в "
                f"{chain_stats['rejected_placeholders']} строках. В кэш они НЕ "
                "попали — повторный запуск переведёт их заново.",
            )

        # 5. Применяем то, что у нас есть, к JSON-файлам. Делается даже при ошибке —
        # чтобы частичный перевод сохранился и можно было продолжить с того же места.
        self.phase.emit("Сборка переводов…")
        translations: dict[int, str] = {}
        damaged = 0
        for unit, translated in zip(units, translated_texts):
            # Не записываем «пустые» переводы поверх исходного текста.
            # Пустой = unit не успел перевестись (stop/ошибка), оставляем исходник.
            if not translated.strip() and unit.combined_text.strip():
                continue
            try:
                translations.update(split_translated_unit(unit, translated))
            except ValueError:
                # Провайдер испортил границы этой единицы. Раньше исключение
                # улетало наружу и обрывало ВСЮ запись: пользователь получал
                # трейсбек и ни одного записанного файла. Теперь теряется
                # только эта строка — она останется на языке источника.
                damaged += 1
        if damaged:
            self.log.emit(
                "warn",
                f"⚠ Переводчик повредил границы в {damaged} пакет(ах) — "
                "эти строки оставлены на языке источника, остальные записаны.",
            )

        if translations:
            self.phase.emit("Запись файлов…")
            val_stats = project.apply_translations(translations)
            self.log.emit("success", f"Записано переводов: {len(translations)}")
            if val_stats.get("rewrapped"):
                extra = val_stats.get("extra_windows", 0)
                width = project.layout.available_width() if project.layout else 0
                tail = (f", добавлено окон: {extra}" if extra else "")
                self.log.emit(
                    "success",
                    f"✓ Пересобрана вёрстка {val_stats['rewrapped']} сообщений "
                    f"по ширине {width} px{tail}",
                )
            # Отчёт о целостности управляющих кодов
            if val_stats["with_codes"] > 0:
                broken = val_stats["broken"]
                total_codes = val_stats["with_codes"]
                if broken == 0:
                    self.log.emit(
                        "success",
                        f"✓ Управляющие коды целы во всех {total_codes} строках с кодами",
                    )
                else:
                    pct = broken / total_codes * 100
                    self.log.emit(
                        "warn",
                        f"⚠ Переводчик исказил коды в {broken}/{total_codes} строках "
                        f"({pct:.1f}%). Эти строки оставлены на языке источника, "
                        "чтобы служебные теги не попали в игру.",
                    )
                    for ex in val_stats["broken_entries"][:5]:
                        self.log.emit(
                            "warn",
                            f"   {ex['file']}: потеряны {ex['missing']} в «{ex['text']}»",
                        )
        else:
            self.log.emit("warn", "Нет переводов для записи")

        cache.save()

        # Перенос выполняется внутри самой игры, поэтому учитывает реальную
        # ширину конкретного шрифта, портрет, размер окна и управляющие коды.
        # Тестовый прогон не меняет игровой www.
        if self.install_text_wrap and not is_test and translation_error is None and not self._stop:
            self.phase.emit("Установка автопереноса текста…")
            try:
                from core.text_layout import install_runtime_text_wrap
                wrap_result = install_runtime_text_wrap(self.output_dir)
                action = "установлен" if wrap_result.changed else "уже установлен"
                self.log.emit(
                    "success",
                    f"✓ Пиксельный автоперенос {action}: {wrap_result.plugin_path}",
                )
                if wrap_result.backup_path:
                    self.log.emit("info", f"Резервная копия index.html: {wrap_result.backup_path}")
            except FileNotFoundError as exc:
                self.log.emit(
                    "warn",
                    f"Автоперенос не установлен: {exc}. "
                    "Укажи Data внутри полной папки игры или установи модуль вручную.",
                )
            except Exception as exc:
                self.log.emit("warn", f"Не удалось установить автоперенос: {exc}")

        # Если был stop или ошибка — сигнализируем соответствующе
        if self._stop:
            self.log.emit(
                "warn",
                "Остановлено пользователем. Прогресс сохранён в кэше — "
                "запусти на ту же выходную папку, чтобы продолжить.",
            )
            self.failed.emit("Остановлено пользователем (прогресс сохранён)")
            return

        if translation_error is not None:
            self.log.emit(
                "warn",
                "Прогресс сохранён в кэше. Запусти повторно на ту же выходную "
                "папку, чтобы продолжить с этого места.",
            )
            self.failed.emit(str(translation_error))
            return

        if is_test:
            self.log.emit(
                "success",
                "Тестовый перевод готов. Проверь результат — "
                "если устраивает, запускай полный перевод."
            )

        self.finished_ok.emit(str(self.output_dir))

    def _recover_from_cache(self, unit_texts, cache, stages):
        """Восстанавливает переводы из кэша для прерванного процесса.
        Возвращает список той же длины что unit_texts: где есть в кэше — перевод,
        где нет — исходник (пустая строка для индикации «не переведено»)."""
        result = []
        for t in unit_texts:
            current = t
            # Проходим все стадии цепочки
            for src, dst in stages:
                cached = cache.get(src, dst, current)
                if cached is None:
                    current = ""  # не удалось пройти всю цепочку
                    break
                current = cached
            result.append(current)
        return result


class AnalysisWorker(QThread):
    """Анализ проекта в фоне.

    Раньше `analyze_project` вызывался прямо из обработчика кнопки с пометкой
    «обычно быстро (секунды)». На реальной игре разбор карт, сканирование JS и
    определение языков занимают десятки секунд, и всё это время окно висело
    без реакции — Windows успевала подписать его «Не отвечает».
    """

    done = pyqtSignal(object)     # ProjectStats
    failed = pyqtSignal(str)
    phase = pyqtSignal(str)

    def __init__(self, data_dir: str, group_dialogues: bool, crypto,
                 i18n_field: str | None, glossary=None, parent=None):
        super().__init__(parent)
        self.data_dir = data_dir
        self.group_dialogues = group_dialogues
        self.crypto = crypto
        self.i18n_field = i18n_field
        self.glossary = glossary

    def run(self) -> None:
        try:
            from core.rpgmaker_parser import analyze_project
            self.phase.emit("Анализ проекта…")
            stats, _ = analyze_project(
                self.data_dir,
                group_dialogues=self.group_dialogues,
                crypto=self.crypto,
                i18n_field=self.i18n_field,
                glossary=self.glossary,
            )
            self.done.emit(stats)
        except Exception as exc:      # noqa: BLE001 — показываем пользователю
            self.failed.emit(str(exc))


class KeySearchWorker(QThread):
    """Подбор ключа шифрования в фоне.

    Перебор строковых литералов обфусцированного rmmz_managers.js с проверкой
    каждого кандидата через AES — это тысячи расшифровок. В GUI-потоке они
    полностью замораживали окно.
    """

    done = pyqtSignal(object, object)   # (key | None, managers_path | None)

    def __init__(self, data_dir: str, parent=None):
        super().__init__(parent)
        self.data_dir = data_dir

    def run(self) -> None:
        try:
            from core.crypto import auto_find_key
            key, managers = auto_find_key(Path(self.data_dir))
        except Exception:
            key, managers = None, None
        self.done.emit(key, managers)


class LocalLLMProbeWorker(QThread):
    """Проверка связи с локальной моделью в фоне.

    Первый запрос после загрузки модели занимает секунды, а при выключенном
    сервере ожидание упирается в таймаут. В GUI-потоке это выглядело бы как
    зависшее окно.
    """

    done = pyqtSignal(object)    # dict-отчёт из LocalLLMTranslator.probe()

    def __init__(self, settings: dict, parent=None):
        super().__init__(parent)
        self.settings = dict(settings)

    def run(self) -> None:
        try:
            from core.local_llm import LocalLLMTranslator
            report = LocalLLMTranslator(**self.settings).probe()
        except Exception as e:
            report = {"ok": False, "reachable": False, "model_found": False,
                      "responds": False, "message": str(e), "models": []}
        self.done.emit(report)
