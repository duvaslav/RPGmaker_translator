"""
Постоянный кэш переводов для возобновления прерванной работы.

Кэш хранится в файле _translation_cache.json в выходной папке проекта.
Ключ: (исходный_язык, целевой_язык, исходный_текст) → переведённый_текст.

При новом запуске:
- Кэш загружается из файла
- Строки, для которых перевод уже есть, не отправляются переводчику
- Это даёт два бонуса: возобновление после прерывания + экономия на дубликатах
  (одинаковые имена/реплики переводятся один раз)

Кэш сохраняется автоматически:
- После каждого пакета (батча) переводов
- При остановке/паузе
- При ошибке/исключении

Безопасность записи: пишем в temp-файл, потом атомарно заменяем, чтобы кэш
не пострадал при внезапном завершении процесса.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path


CACHE_FILENAME = "_translation_cache.json"
CACHE_VERSION = 1


def _make_key(src: str, dst: str, text: str) -> str:
    """Ключ кэша: src→dst:text. Разделитель такой, который точно не встретится
    в коде языка (ISO 639-1 — две буквы)."""
    return f"{src}|{dst}|{text}"


class TranslationCache:
    """Кэш переводов с поточно-безопасным доступом и атомарной записью."""

    def __init__(self, cache_path: Path):
        self.path = Path(cache_path)
        self._data: dict[str, str] = {}
        self._lock = threading.Lock()
        self._dirty = False  # есть ли несохранённые изменения
        self.load()

    # ── Загрузка/сохранение ────────────────────────────────────────────────

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except Exception:
            # Битый кэш — лучше начать с пустого, чем падать
            self._data = {}
            return
        if not isinstance(raw, dict):
            return
        # Поддержка форматов: либо плоский {key: value},
        # либо {version, entries: {key: value}}
        if "entries" in raw and isinstance(raw["entries"], dict):
            self._data = {k: v for k, v in raw["entries"].items()
                          if isinstance(v, str)}
        else:
            self._data = {k: v for k, v in raw.items() if isinstance(v, str)}

    def save(self) -> None:
        """Атомарная запись на диск."""
        with self._lock:
            if not self._dirty:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            data_to_save = {
                "version": CACHE_VERSION,
                "entries": self._data,
            }
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data_to_save, f, ensure_ascii=False, indent=0)
            os.replace(tmp, self.path)
            self._dirty = False

    # ── Доступ ─────────────────────────────────────────────────────────────

    def get(self, src: str, dst: str, text: str) -> str | None:
        with self._lock:
            return self._data.get(_make_key(src, dst, text))

    def set(self, src: str, dst: str, text: str, translated: str) -> None:
        with self._lock:
            key = _make_key(src, dst, text)
            if self._data.get(key) != translated:
                self._data[key] = translated
                self._dirty = True

    def set_many(self, src: str, dst: str, pairs: list[tuple[str, str]]) -> None:
        """Массовая вставка: pairs = [(исходный, переведённый), ...]"""
        with self._lock:
            for src_text, translated in pairs:
                key = _make_key(src, dst, src_text)
                if self._data.get(key) != translated:
                    self._data[key] = translated
                    self._dirty = True

    def has(self, src: str, dst: str, text: str) -> bool:
        with self._lock:
            return _make_key(src, dst, text) in self._data

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)

    def stats_for_route(self, src: str, dst: str) -> int:
        """Сколько записей кэша для конкретного маршрута src→dst."""
        prefix = f"{src}|{dst}|"
        with self._lock:
            return sum(1 for k in self._data if k.startswith(prefix))
