"""Хеширование паролей: pbkdf2_sha256 из стандартной библиотеки.

Формат хранения — одна строка-контейнер:
    pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>
Соль хранится вместе с хешем, поэтому verify_password может её достать.
"""

import hashlib
import hmac
import secrets

_ALGORITHM = "pbkdf2_sha256"
_ITERATIONS = 100_000
_SALT_BYTES = 16


def hash_password(password: str) -> str:
    """Захешировать пароль. Каждый вызов даёт новую строку (случайная соль)."""
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, _ITERATIONS
    )
    return f"{_ALGORITHM}${_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    """Проверить пароль против сохранённой строки. Ничего не печатает."""
    if stored is None:
        return False
    try:
        algorithm, iterations_str, salt_hex, hash_hex = stored.split("$")
        if algorithm != _ALGORITHM:
            return False
        iterations = int(iterations_str)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except (ValueError, AttributeError):
        return False

    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iterations
    )
    return hmac.compare_digest(digest, expected)
