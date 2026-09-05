"""Install a pixel-accurate runtime text wrapper into RPG Maker MV/MZ games.

Static character limits are unreliable: glyph widths depend on the active game
font, font-size escape codes, portraits and the actual message-window width.
The bundled runtime plugin measures text through RPG Maker itself and lets the
engine's native message pagination handle any extra wrapped lines.
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path


PLUGIN_FILENAME = "Translator_AutoWrap.js"
PLUGIN_NAME = "Translator_AutoWrap"
SCRIPT_SRC = f"js/plugins/{PLUGIN_FILENAME}"
REGISTRATION_MARKER = "RPGMAKER_TRANSLATOR_AUTOWRAP"
REGISTRATION_TAG = (
    f'        <script type="text/javascript">/* {REGISTRATION_MARKER} */ '
    f'$plugins.push({{"name":"{PLUGIN_NAME}","status":true,'
    '"description":"Pixel-accurate automatic message wrapping",'
    '"parameters":{}});</script>'
)


@dataclass(frozen=True)
class TextWrapInstallResult:
    www_dir: Path
    plugin_path: Path
    index_path: Path
    backup_path: Path | None
    changed: bool


def find_rpgmaker_www(data_dir: str | Path) -> Path | None:
    """Return the nearest MV/MZ ``www`` root for a selected data directory."""
    selected = Path(data_dir).resolve()
    candidates = [selected, selected.parent, selected.parent.parent]
    for candidate in candidates:
        index_path = candidate / "index.html"
        js_dir = candidate / "js"
        if not index_path.is_file() or not js_dir.is_dir():
            continue
        if (js_dir / "rpg_windows.js").is_file() or (js_dir / "rmmz_windows.js").is_file():
            return candidate
    return None


def _asset_path() -> Path:
    path = Path(__file__).resolve().parent.parent / "assets" / PLUGIN_FILENAME
    if not path.is_file():
        raise FileNotFoundError(f"Не найден модуль автопереноса: {path}")
    return path


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def _inject_script_tag(index_text: str) -> str:
    if REGISTRATION_MARKER in index_text:
        return index_text

    # Migrate the short-lived direct-script variant. Registering through
    # $plugins is important because PluginManager then loads this module last,
    # after all game plugins that might override Window_Message methods.
    direct_tag = re.compile(
        r'^\s*<script\b[^>]*\bsrc=["\']js/plugins/Translator_AutoWrap\.js["\'][^>]*>'
        r'</script>\s*$',
        re.IGNORECASE | re.MULTILINE,
    )
    index_text = direct_tag.sub('', index_text)

    pattern = re.compile(
        r'^(?P<indent>\s*)<script\b[^>]*\bsrc=["\']js/plugins\.js["\'][^>]*></script>\s*$',
        re.IGNORECASE | re.MULTILINE,
    )
    match = pattern.search(index_text)
    if not match:
        raise ValueError("В index.html не найдено подключение js/plugins.js")
    insertion = match.group(0).rstrip() + "\n" + match.group("indent") + REGISTRATION_TAG.strip()
    return index_text[:match.start()] + insertion + index_text[match.end():]


def install_runtime_text_wrap(data_dir: str | Path) -> TextWrapInstallResult:
    """Install/update the wrapper and register it after ``plugins.js``.

    ``index.html`` is backed up once. Repeated calls are idempotent, while the
    plugin file is refreshed from the translator's bundled copy.
    """
    www_dir = find_rpgmaker_www(data_dir)
    if www_dir is None:
        raise FileNotFoundError(
            "Рядом с выбранной папкой Data не найден проект RPG Maker MV/MZ "
            "(нужны index.html и js/rpg*_windows.js)"
        )

    index_path = www_dir / "index.html"
    plugin_path = www_dir / "js" / "plugins" / PLUGIN_FILENAME
    backup_path = www_dir / "index.html.pre_translator_autowrap.bak"
    old_index = index_path.read_text(encoding="utf-8-sig")
    new_index = _inject_script_tag(old_index)
    asset_bytes = _asset_path().read_bytes()
    plugin_changed = not plugin_path.is_file() or plugin_path.read_bytes() != asset_bytes
    index_changed = new_index != old_index

    if index_changed and not backup_path.exists():
        shutil.copy2(index_path, backup_path)
    if plugin_changed:
        plugin_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=PLUGIN_FILENAME + ".", suffix=".tmp", dir=plugin_path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(asset_bytes)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, plugin_path)
        except Exception:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise
    if index_changed:
        _atomic_write_text(index_path, new_index)

    return TextWrapInstallResult(
        www_dir=www_dir,
        plugin_path=plugin_path,
        index_path=index_path,
        backup_path=backup_path if backup_path.exists() else None,
        changed=plugin_changed or index_changed,
    )
