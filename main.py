"""Точка входа: поднимает веб-сервер docapp.

Запуск:  uv run python main.py

По умолчанию слушает 127.0.0.1:8000 — за reverse-proxy Caddy/nginx (ADR-7).
Для доступа по локальной сети / при разработке: DOCAPP_HOST=0.0.0.0.
Адрес и порт переопределяются переменными DOCAPP_HOST и DOCAPP_PORT.
"""

import os

import uvicorn

from docapp.config import db_path, session_secret
from docapp.web.app import create_app


def main() -> None:
    app = create_app(db_path=db_path(), secret=session_secret())
    host = os.environ.get("DOCAPP_HOST", "127.0.0.1")
    port = int(os.environ.get("DOCAPP_PORT", "8000"))
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
