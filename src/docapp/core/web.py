"""Общие вещи веб-слоя: шаблоны модулей и отдача файлов (ADR-0017).

Один набор шаблонов на приложение: модуль добавляет свою папку, общий `base.html`
всегда подключён последним. Меню и хэш ревизии — в контексте каждого шаблона,
поэтому модули о них не думают.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from fastapi.responses import Response
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from docapp.config import git_revision
from docapp.core import access

#: Папка общих шаблонов приложения (base.html) — рядом с этим модулем.
WEB_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "web" / "templates"

#: Media type .xlsx для выгрузок (отчёты «Потребностей», разлиновка «Дежурств»).
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _revision_context(request: Request) -> dict:
    """Хэш ревизии для бейджа в шапке."""
    return {"git_revision": git_revision()}


def _menu_context(request: Request) -> dict:
    """Меню разделов: из таблицы прав, второго списка не существует (ADR-0023)."""
    user = access.current_user(request)
    return {"menu": access.menu_for(user.role) if user else []}


def templates(*directories: Path) -> Jinja2Templates:
    """Шаблоны модуля поверх общих: свои папки первыми, `web/templates` — последней."""
    paths = [str(directory) for directory in directories] + [str(WEB_TEMPLATES_DIR)]
    return Jinja2Templates(
        directory=paths,
        context_processors=[_revision_context, _menu_context],
    )


def content_disposition(filename: str) -> str:
    """Content-Disposition: ASCII-имя в кавычках, кириллица — RFC 5987 (filename*)."""
    name = Path(filename).name
    try:
        name.encode("latin-1")
    except UnicodeEncodeError:
        return f"attachment; filename*=UTF-8''{quote(name)}"
    return f'attachment; filename="{name}"'


def xlsx_response(content: bytes, filename: str) -> Response:
    """Ответ со скачиванием .xlsx (нужные media type и имя файла)."""
    return Response(
        content=content,
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": content_disposition(filename)},
    )
