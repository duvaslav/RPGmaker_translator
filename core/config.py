"""
Хранение настроек и API-ключей в локальном JSON-файле в папке пользователя.
Ключи шифруются простым XOR + base64 — это не криптография, а защита от
случайного просмотра в логах/скриншотах.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path


def _config_dir() -> Path:
    """Кросс-платформенная папка для конфига."""
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home()))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    p = base / "rpgmaker_translator"
    p.mkdir(parents=True, exist_ok=True)
    return p


CONFIG_PATH = _config_dir() / "config.json"
_XOR_KEY = b"rpgm-translator-local-obfuscation-v1"


def _xor(data: bytes) -> bytes:
    return bytes(b ^ _XOR_KEY[i % len(_XOR_KEY)] for i, b in enumerate(data))


def _obfuscate(s: str) -> str:
    if not s:
        return ""
    return base64.b64encode(_xor(s.encode("utf-8"))).decode("ascii")


def _deobfuscate(s: str) -> str:
    if not s:
        return ""
    try:
        return _xor(base64.b64decode(s.encode("ascii"))).decode("utf-8")
    except Exception:
        return ""


DEFAULT_CONFIG = {
    "api_keys": {
        "DeepL": "",
        "Google": "",
        "Yandex": "",
    },
    "yandex_folder_id": "",
    "deepl_free_tier": True,
    "last_route": {
        "src": "ja",
        "pivot": "en",
        "dst": "ru",
    },
    "last_stage_providers": ["DeepL", "DeepL"],
    "last_project_dir": "",
    "batch_size": 40,
    "group_dialogues": True,
    "lang_filter": [],
    "encryption_key": "",
    # ── Локальная модель ────────────────────────────────────────────────────
    # Значения по умолчанию взяты из протокола испытаний Qwen3.5-4B Q4_K_M:
    # профиль 4096 / GPU 0.8 / parallel 1, температура 0, рассуждения выключены.
    # Пакет 5 — безопасный для 16 ГиБ ОЗУ; 10 быстрее и проверен на 32 ГиБ;
    # 20 экспериментальный, на нём терялся управляющий код.
    "local_llm": {
        "base_url": "http://127.0.0.1:1234/v1",
        "model": "qwen3.5-4b-rpg-ru-safe",
        "system_prompt": "",        # пусто = промпт по умолчанию из core.local_llm
        "temperature": 0.0,
        "top_p": 0.8,
        "top_k": 20,
        "reasoning_effort": "none",
        "max_tokens": 0,            # 0 = не ограничивать
        "timeout": 180.0,
        "use_json_schema": True,
        "repair_retries": 1,
        "batch_size": 5,
        "context_window": 1,        # 1 до и 1 после, в пределах Event/Page
        "allow_remote": False,
    },
}


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return dict(DEFAULT_CONFIG)

    cfg = dict(DEFAULT_CONFIG)
    cfg.update(raw)
    # Расшифровываем ключи
    keys = cfg.get("api_keys", {})
    cfg["api_keys"] = {k: _deobfuscate(v) for k, v in keys.items()}
    # Гарантируем все провайдеры
    for p in DEFAULT_CONFIG["api_keys"]:
        cfg["api_keys"].setdefault(p, "")
    # Новые настройки, появившиеся после того как конфиг был записан: без
    # этого обновление приложения падало бы на отсутствующем ключе.
    local = dict(DEFAULT_CONFIG["local_llm"])
    local.update(cfg.get("local_llm") or {})
    cfg["local_llm"] = local
    return cfg


def save_config(cfg: dict) -> None:
    to_save = dict(cfg)
    to_save["api_keys"] = {
        k: _obfuscate(v or "") for k, v in cfg.get("api_keys", {}).items()
    }
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(to_save, f, ensure_ascii=False, indent=2)
