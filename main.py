"""Точка входа: поднимает веб-сервер docapp.

Запуск:  uv run python main.py
"""

import os
import secrets
from pathlib import Path

import uvicorn

from docapp.web.app import create_app

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)


def db_path() -> Path:
    """Путь к файлу БД: из окружения DOCAPP_DB или data/docapp.db."""
    override = os.environ.get("DOCAPP_DB")
    return Path(override) if override else DATA_DIR / "docapp.db"


def session_secret() -> str:
    """Секрет подписи сессий. Хранится в data/secret.key, чтобы
    перезапуски сервера не убивали сессии врачей."""
    key_file = DATA_DIR / "secret.key"
    if key_file.exists():
        return key_file.read_text().strip()
    secret = secrets.token_hex(32)
    key_file.write_text(secret)
    return secret


def main() -> None:
    app = create_app(db_path=db_path(), secret=session_secret())
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
