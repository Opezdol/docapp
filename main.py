"""Точка входа: поднимает веб-сервер docapp.

Запуск:  uv run python main.py

По умолчанию слушает 127.0.0.1:8000 — за reverse-proxy Caddy/nginx (ADR-7).
Для доступа по локальной сети / при разработке: DOCAPP_HOST=0.0.0.0.
Адрес и порт переопределяются переменными DOCAPP_HOST и DOCAPP_PORT.

На shared-хостинге (ISPmanager) приложение запускается панелью напрямую:
`.venv/bin/python main.py` — панель не передаёт окружение из .env, поэтому
main.py сам загружает .env (если он есть рядом), а только потом читает
переменные DOCAPP_*. Существующие переменные окружения имеют приоритет.
"""

import os
from pathlib import Path

import uvicorn

from docapp.config import db_path, session_secret
from docapp.web.app import create_app


def _load_dotenv() -> None:
    """Загрузить .env из каталога приложения, не перезаписывая окружение."""
    env_file = Path(__file__).resolve().parent / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def main() -> None:
    _load_dotenv()
    app = create_app(db_path=db_path(), secret=session_secret())
    host = os.environ.get("DOCAPP_HOST", "127.0.0.1")
    port = int(os.environ.get("DOCAPP_PORT", "8000"))
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
