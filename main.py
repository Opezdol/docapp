"""Точка входа: поднимает веб-сервер docapp.

Запуск:  uv run python main.py
"""

import uvicorn

from docapp.config import db_path, session_secret
from docapp.web.app import create_app


def main() -> None:
    app = create_app(db_path=db_path(), secret=session_secret())
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
