"""Общие настройки приложения: путь к БД и секрет сессий."""

import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)


def db_path() -> Path:
    """Путь к файлу БД: из окружения DOCAPP_DB или data/docapp.db."""
    override = os.environ.get("DOCAPP_DB")
    return Path(override) if override else DATA_DIR / "docapp.db"


def https_only() -> bool:
    """Требовать HTTPS-куки сессий: env DOCAPP_HTTPS_ONLY=1 (продакшн за TLS)."""
    return os.environ.get("DOCAPP_HTTPS_ONLY", "") == "1"


def session_secret() -> str:
    """Секрет подписи сессий. Хранится в data/secret.key, чтобы
    перезапуски сервера не убивали сессии врачей."""
    key_file = DATA_DIR / "secret.key"
    if key_file.exists():
        return key_file.read_text().strip()
    secret = secrets.token_hex(32)
    key_file.write_text(secret)
    return secret
