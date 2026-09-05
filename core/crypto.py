"""
Расшифровка/зашифровка JSON-файлов RPG Maker, защищённых CryptoJS AES.

Формат: некоторые разработчики патчат rmmz_managers.js и шифруют все data/*.json
через CryptoJS.AES.encrypt с фиксированным ключом-строкой. Игра при загрузке
расшифровывает их в памяти, на диске лежит Base64 со строкой Salted__ в начале.

Алгоритм CryptoJS совместим с OpenSSL `enc -aes-256-cbc`:
- KDF: EVP_BytesToKey (MD5, 1 итерация)
- Шифр: AES-256-CBC, PKCS#7 padding
- Контейнер: "Salted__" + 8-байт случайная соль + ciphertext, всё в Base64

Использование:
    from core.crypto import GameCrypto, detect_encrypted

    if detect_encrypted(file_bytes):
        crypto = GameCrypto(key="showDefault:eval")
        plain = crypto.decrypt(file_bytes)         # → str (JSON)
        cipher = crypto.encrypt(plain)             # → str (Base64)

Зависимости: cryptography (стандартная python библиотека шифрования).
В отличие от pycryptodome, cryptography входит во многие дистрибутивы Python.
Если её нет — модуль использует pycryptodome как fallback.
"""
from __future__ import annotations

import base64
import os
from hashlib import md5


# Маркер CryptoJS-зашифрованного файла после base64-декодирования
SALTED_PREFIX = b"Salted__"
# В base64 этот префикс начинается на 'U2FsdGVkX1' (длина 10 символов)
SALTED_BASE64_PREFIX = "U2FsdGVkX1"


def detect_encrypted(data: bytes | str) -> bool:
    """Определяет, зашифрован ли файл CryptoJS AES OpenSSL-форматом.
    Принимает либо первые байты, либо начало строки."""
    if isinstance(data, bytes):
        # Может быть raw base64-текст в bytes
        try:
            s = data[:32].decode("ascii", errors="ignore")
        except Exception:
            return False
    else:
        s = data
    s = s.strip()
    return s.startswith(SALTED_BASE64_PREFIX)


# ────────────────────────────────────────────────────────────────────────────
# OpenSSL EVP_BytesToKey (как делает CryptoJS)
# ────────────────────────────────────────────────────────────────────────────

def _evp_bytes_to_key(password: bytes, salt: bytes, key_len: int, iv_len: int) -> tuple[bytes, bytes]:
    """OpenSSL EVP_BytesToKey с MD5 и 1 итерацией — стандарт CryptoJS."""
    d = b""
    di = b""
    while len(d) < key_len + iv_len:
        di = md5(di + password + salt).digest()
        d += di
    return d[:key_len], d[key_len:key_len + iv_len]


# ────────────────────────────────────────────────────────────────────────────
# AES шифр — пытаемся cryptography, fallback на pycryptodome
# ────────────────────────────────────────────────────────────────────────────

def _aes_encrypt_cbc(key: bytes, iv: bytes, data: bytes) -> bytes:
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.primitives import padding
        padder = padding.PKCS7(128).padder()
        padded = padder.update(data) + padder.finalize()
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
        enc = cipher.encryptor()
        return enc.update(padded) + enc.finalize()
    except ImportError:
        from Crypto.Cipher import AES
        from Crypto.Util.Padding import pad
        cipher = AES.new(key, AES.MODE_CBC, iv)
        return cipher.encrypt(pad(data, AES.block_size))


def _aes_decrypt_cbc(key: bytes, iv: bytes, data: bytes) -> bytes:
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.primitives import padding
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
        dec = cipher.decryptor()
        decrypted = dec.update(data) + dec.finalize()
        unpadder = padding.PKCS7(128).unpadder()
        return unpadder.update(decrypted) + unpadder.finalize()
    except ImportError:
        from Crypto.Cipher import AES
        from Crypto.Util.Padding import unpad
        cipher = AES.new(key, AES.MODE_CBC, iv)
        return unpad(cipher.decrypt(data), AES.block_size)


# ────────────────────────────────────────────────────────────────────────────
# Главный класс
# ────────────────────────────────────────────────────────────────────────────

class GameCryptoError(Exception):
    pass


class GameCrypto:
    """Зашифровка/расшифровка JSON в формате CryptoJS AES-256-CBC OpenSSL.

    Параметры:
        key — строка-пароль из rmmz_managers.js (в найденной игре: "showDefault:eval")
    """

    def __init__(self, key: str, key_len: int = 32, iv_len: int = 16):
        if not key:
            raise GameCryptoError("Ключ шифрования не задан")
        self.password = key.encode("utf-8")
        self.key_len = key_len
        self.iv_len = iv_len

    def decrypt(self, b64_data: str | bytes) -> str:
        """Расшифровывает Base64 CryptoJS-шифр → JSON-строку."""
        if isinstance(b64_data, bytes):
            b64_data = b64_data.decode("ascii", errors="ignore")
        b64_data = b64_data.strip()
        try:
            raw = base64.b64decode(b64_data)
        except Exception as e:
            raise GameCryptoError(f"Не Base64: {e}")
        if not raw.startswith(SALTED_PREFIX):
            raise GameCryptoError(
                "Не похоже на CryptoJS-формат: отсутствует префикс 'Salted__'"
            )
        salt = raw[8:16]
        ciphertext = raw[16:]
        key, iv = _evp_bytes_to_key(self.password, salt, self.key_len, self.iv_len)
        try:
            plain = _aes_decrypt_cbc(key, iv, ciphertext)
        except Exception as e:
            raise GameCryptoError(
                f"Не удалось расшифровать (возможно, неверный ключ): {e}"
            )
        try:
            return plain.decode("utf-8")
        except UnicodeDecodeError as e:
            raise GameCryptoError(f"Расшифровано, но не UTF-8: {e}")

    def encrypt(self, plaintext: str) -> str:
        """Зашифровывает JSON-строку → Base64 CryptoJS-шифр."""
        salt = os.urandom(8)
        key, iv = _evp_bytes_to_key(self.password, salt, self.key_len, self.iv_len)
        ciphertext = _aes_encrypt_cbc(key, iv, plaintext.encode("utf-8"))
        raw = SALTED_PREFIX + salt + ciphertext
        return base64.b64encode(raw).decode("ascii")


# ────────────────────────────────────────────────────────────────────────────
# Поиск ключа в rmmz_managers.js (для GUI: помощь пользователю)
# ────────────────────────────────────────────────────────────────────────────

import re

_DECRYPT_PATTERN = re.compile(
    r"CryptoJS\s*\[\s*['\"]AES['\"]\s*\]\s*\[\s*['\"]decrypt['\"]\s*\]"
    r"\s*\(\s*\w+\s*,\s*['\"]([^'\"]+)['\"]"
)


def find_key_in_managers_js(text: str) -> str | None:
    """Ищет ключ в коде rmmz_managers.js.

    Сначала пробует прямые шаблоны вызова, потом — эвристику для обфусцированного
    кода (как в RMMZ Anti-decompile / Olivia / etc): находит строковые литералы,
    которые похожи на пароль-ключ, в файле, где упоминаются 'CryptoJS' и 'AES'.

    Возвращает первый найденный ключ или None.
    """
    # 1. Прямой шаблон 1: CryptoJS['AES']['decrypt'](x, 'key')
    m = _DECRYPT_PATTERN.search(text)
    if m:
        return m.group(1)
    # 2. Прямой шаблон 2: CryptoJS.AES.decrypt(x, "key")
    alt = re.search(
        r'CryptoJS\.AES\.decrypt\s*\(\s*\w+\s*,\s*[\'"]([^\'"]+)[\'"]',
        text,
    )
    if alt:
        return alt.group(1)

    # 3. Обфусцированный код: ищем CryptoJS вообще и AES — это сигнатура того,
    # что шифрование вшито. Затем выбираем кандидаты-строки.
    if "CryptoJS" not in text or "AES" not in text:
        return None

    # Ищем все строковые литералы в одинарных или двойных кавычках.
    # Ограничиваем диапазон длины: реальные пароли обычно 8-64 символа.
    candidates = []
    for m in re.finditer(r'[\'"]([^\'"\n\r\\]{6,64})[\'"]', text):
        s = m.group(1)
        # Отсеиваем заведомо не-ключи
        if _looks_like_key(s):
            candidates.append(s)

    # Берём наиболее «выделяющиеся» — те, что выглядят как намеренный
    # пароль (содержат разделители или спец-символы) и встречаются редко.
    from collections import Counter
    counts = Counter(candidates)
    # Если есть кандидаты с двоеточием/слэшем — приоритет им, это типично
    # для строк вида "showDefault:eval", "secret:key" и т.п.
    typed = [c for c in counts if (':' in c or '/' in c or '-' in c or '_' in c)]
    if typed:
        # Сортируем по частоте (редкие = более вероятный ключ),
        # потом по убыванию длины (длинные интереснее)
        typed.sort(key=lambda c: (counts[c], -len(c)))
        return typed[0]

    # Иначе берём редкий длинный кандидат
    if candidates:
        unique = sorted(set(candidates), key=lambda c: (counts[c], -len(c)))
        return unique[0]

    return None


# Распространённые подстроки-«мусор», который не может быть ключом
_NON_KEY_SUBSTRINGS = (
    ".png", ".ogg", ".m4a", ".wav", ".json", ".js", ".jpg", ".jpeg",
    "data/", "img/", "audio/", "fonts/", "se/", "bgm/", "bgs/",
    "/system/", "/pictures/", "/characters/", "/faces/",
    "rgba(", "rgb(", "#", "0x", "\\C[", "\\N[",
    "function", "prototype", "constructor", "addEventListener",
    "application/", "text/", "Error", "TypeError",
    "showDevTools",  # одно слово, не пароль (но реальный пароль 'showDefault:eval' нашли через ':')
)
# Известные «фразы пароля» — оставляем как намёк (whitelist приоритетных)
_PROBABLY_KEYS = ()


def _looks_like_key(s: str) -> bool:
    """Грубая эвристика: похожа ли строка на пароль/ключ."""
    if not s:
        return False
    # Слишком много пробелов — не ключ
    if s.count(" ") >= 2:
        return False
    # Содержит явные не-ключевые маркеры
    sl = s.lower()
    for bad in _NON_KEY_SUBSTRINGS:
        if bad in sl:
            return False
    # Только латинские буквы/цифры/типичные спецсимволы для паролей
    if not re.match(r"^[A-Za-z0-9_:./\-\+\=\!\@\#\$\%\&\*\?]+$", s):
        return False
    # Должен содержать хоть одну букву (исключаем чистые числа/хеши символов)
    if not re.search(r"[A-Za-z]", s):
        return False
    return True


# ────────────────────────────────────────────────────────────────────────────
# Авто-детект на уровне проекта
# ────────────────────────────────────────────────────────────────────────────

from pathlib import Path


def is_data_dir_encrypted(data_dir: Path) -> bool:
    """Проверяет первые попавшиеся JSON-файлы в папке: зашифрованы ли они.
    Достаточно одного зашифрованного файла, чтобы вернуть True — обычно или
    зашифровано всё, или ничего."""
    data_dir = Path(data_dir)
    if not data_dir.exists():
        return False
    # Берём первые несколько кандидатов
    candidates = []
    for name in ("System.json", "Actors.json", "Items.json", "CommonEvents.json"):
        p = data_dir / name
        if p.exists():
            candidates.append(p)
        if len(candidates) >= 2:
            break
    # Если нашли мало — добавим карту
    if len(candidates) < 1:
        for p in sorted(data_dir.glob("Map[0-9]*.json")):
            candidates.append(p)
            break
    if not candidates:
        return False
    for p in candidates:
        try:
            with open(p, "r", encoding="utf-8", errors="ignore") as f:
                head = f.read(64)
            if detect_encrypted(head):
                return True
        except Exception:
            continue
    return False


# Стандартные места, где лежит rmmz_managers.js относительно папки data/.
# Типичная структура игры RMMZ:
#   GameFolder/
#     data/Map001.json ...
#     js/rmmz_managers.js
# или (распакованный архив):
#   GameFolder/www/data/...
#   GameFolder/www/js/rmmz_managers.js
def find_managers_js(data_dir: Path) -> Path | None:
    """Ищет rmmz_managers.js рядом с папкой data."""
    data_dir = Path(data_dir).resolve()
    # Пробуем несколько уровней вверх
    for parent in [data_dir.parent, data_dir.parent.parent]:
        if parent is None:
            continue
        candidate = parent / "js" / "rmmz_managers.js"
        if candidate.exists():
            return candidate
        # MV-версия использует rpg_managers.js
        candidate_mv = parent / "js" / "rpg_managers.js"
        if candidate_mv.exists():
            return candidate_mv
    return None


def auto_find_key(data_dir: Path) -> tuple[str | None, Path | None]:
    """Пытается найти ключ шифрования рядом с папкой data.

    Стратегия:
    1. Найти rmmz_managers.js (или rpg_managers.js для MV)
    2. Попробовать прямые шаблоны вызова CryptoJS.AES.decrypt
    3. Если код обфусцирован — собрать кандидаты-строки и проверить КАЖДЫЙ
       против реального зашифрованного файла. Правильный ключ даст валидный
       UTF-8 JSON, неправильный — упадёт.

    Возвращает (ключ, путь_к_managers_js) или (None, None).
    """
    managers = find_managers_js(data_dir)
    if not managers:
        return None, None
    try:
        with open(managers, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()
    except Exception:
        return None, managers

    # Сначала прямой шаблон
    direct_key = _find_direct_key(text)
    if direct_key:
        # Всё равно проверим на реальном файле, на всякий
        if _key_works(direct_key, data_dir):
            return direct_key, managers

    # Обфусцированный код: собираем кандидатов и проверяем
    candidates = _collect_key_candidates(text)
    for candidate in candidates:
        if _key_works(candidate, data_dir):
            return candidate, managers

    return None, managers


def _find_direct_key(text: str) -> str | None:
    """Прямые шаблоны вызова без обфускации."""
    m = _DECRYPT_PATTERN.search(text)
    if m:
        return m.group(1)
    alt = re.search(
        r'CryptoJS\.AES\.decrypt\s*\(\s*\w+\s*,\s*[\'"]([^\'"]+)[\'"]',
        text,
    )
    if alt:
        return alt.group(1)
    return None


def _collect_key_candidates(text: str) -> list[str]:
    """Собирает строковые литералы — потенциальные ключи.
    Использует правильный токенизатор: проходит по тексту посимвольно
    и выделяет содержимое между парными кавычками, учитывая текущий контекст."""
    if "CryptoJS" not in text or "AES" not in text:
        return []

    found = set()

    # Простой токенизатор: проходит по тексту, отслеживает, внутри какой строки мы.
    # Поддерживает '...' и "..." и `...` (template literals). Игнорирует
    # экранированные кавычки.
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in ("'", '"', "`"):
            quote = ch
            j = i + 1
            buf = []
            while j < n and text[j] != quote:
                # Перенос строки прерывает (это не наш литерал)
                if text[j] in ("\n", "\r"):
                    buf = None
                    break
                if text[j] == "\\" and j + 1 < n:
                    # Экранированная последовательность — пропускаем 2 символа
                    buf.append(text[j])
                    buf.append(text[j + 1])
                    j += 2
                    continue
                buf.append(text[j])
                j += 1
            if buf is not None and j < n:
                s = "".join(buf)
                if 4 <= len(s) <= 64 and _looks_like_key(s):
                    found.add(s)
            i = j + 1
        else:
            i += 1

    # Приоритезация: со спец-символами (типичный пароль вида "name:value") — выше
    def score(s: str) -> tuple:
        has_separator = any(c in s for c in ":/-_=+!@#$%&*?")
        # 0 — выше приоритет
        return (0 if has_separator else 1, -len(s))

    return sorted(found, key=score)


def _key_works(key: str, data_dir: Path) -> bool:
    """Проверяет ключ против первого попавшегося зашифрованного JSON в папке."""
    data_dir = Path(data_dir)
    for name in ("System.json", "Actors.json", "Items.json"):
        p = data_dir / name
        if p.exists():
            break
    else:
        # Берём первую карту
        maps = sorted(data_dir.glob("Map[0-9]*.json"))
        if not maps:
            return False
        p = maps[0]

    try:
        with open(p, "r", encoding="utf-8", errors="ignore") as f:
            ciphertext = f.read().strip()
        if not detect_encrypted(ciphertext):
            return False  # файл не зашифрован — ключ бессмыслен
        crypto = GameCrypto(key)
        plain = crypto.decrypt(ciphertext)
        # Дополнительная проверка: результат — валидный JSON
        import json as _json
        _json.loads(plain)
        return True
    except Exception:
        return False
