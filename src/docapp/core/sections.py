"""Выключение разделов на входе в приложение (ADR-0030).

Проверка стоит в middleware, а не в каждом роутере: раздел состоит из
страницы, формы и JSON-API, и забыть одну из них — это открытая дверь.
Карта «адрес → модуль» строится из объявлений разделов, а не из префикса
всего приложения: `records` владеет `/`, и проверка по общему префиксу
закрыла бы всё (это и есть причина, по которой `records` не выключается).

Отдельного протокола ради одного потребителя не заводим (ADR-0017):
`SectionSwitch` описывает, что middleware нужно от панели, а реализует его
`admin.AdminService`.
"""

from __future__ import annotations

from typing import Protocol

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse


class SectionSwitch(Protocol):
    """Что нужно middleware от владельца состояния разделов."""

    def module_for_path(self, path: str) -> str | None: ...

    def is_enabled(self, module: str) -> bool: ...

    def section_label(self, module: str) -> str: ...


def switch_of(request: Request) -> SectionSwitch | None:
    """Выключатель разделов приложения; None — модуль `admin` не подключён.

    В тестах собирается подмножество модулей (без панели): тогда выключенных
    разделов нет по определению, как и фолбэк на `ALLOWED` в правах.
    """
    return getattr(request.app.state, "section_switch", None)


class SectionsMiddleware(BaseHTTPMiddleware):
    """Выключенный раздел: страница — редирект с сообщением, API — 403."""

    async def dispatch(self, request: Request, call_next):
        switch = switch_of(request)
        if switch is not None:
            module = switch.module_for_path(request.url.path)
            if module is not None and not switch.is_enabled(module):
                if "text/html" in request.headers.get("accept", ""):
                    request.session["flash"] = (
                        f"Раздел «{switch.section_label(module)}» выключен"
                    )
                    return RedirectResponse("/", status_code=303)
                return JSONResponse({"detail": "Раздел выключен"}, status_code=403)
        return await call_next(request)
