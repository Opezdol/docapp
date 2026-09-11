"""HTTP-адаптер «Распределения»: ведомость, счёт и выгрузка.

Правил здесь нет: разбор файла — в `vedomost.py`, разноска — в `spread.py`,
сборка книги — в `xlsx.py`, права — в `core/access`. Роутер только переводит
HTTP в вызовы сервиса и обратно (ADR-0017).

Раздел целиком принадлежит заведующему (`distribution.manage`, ADR-0024):

- `/distribution` — ведомость: выбрать месяц, загрузить файл, скачать результат;
- `/distribution/counts` — счёт поданных анестезий за период;
- `/distribution/api/aggregate` — тот же счёт одним JSON.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from docapp.core import access, web
from docapp.core.registry import container_of
from docapp.distribution import xlsx
from docapp.distribution.container import DistributionContainer
from docapp.distribution.service import ALLOWED_BY, BY_DOCTOR, BY_LABELS

TEMPLATES = web.templates(Path(__file__).parent / "templates")

router = APIRouter(prefix="/distribution")


def _service(request: Request):
    """Контейнер модуля: сервис распределения поверх интерфейсов соседей."""
    return container_of(request, "distribution", DistributionContainer).service


def _period_bounds() -> tuple[str, str]:
    """Период счёта по умолчанию — текущий месяц: от первого числа до сегодня."""
    today = date.today()
    return today.replace(day=1).isoformat(), today.isoformat()


def _requested_period(request: Request) -> tuple[str, str]:
    """Период из запроса; пустой — текущий месяц."""
    default_from, default_to = _period_bounds()
    params = request.query_params
    return params.get("from") or default_from, params.get("to") or default_to


def _guard_page(request: Request):
    """Пользователь для страниц раздела: нет сессии — на вход, нет прав — 403."""
    user = access.current_user(request)
    if user is None:
        return None
    access.ensure(user, access.DISTRIBUTION_MANAGE,
                  message="Доступ к распределению запрещён")
    return user


def _current_month() -> str:
    return date.today().strftime("%Y-%m")


def _vedomost_page(request: Request, user, *, error: str | None = None,
                   month: str | None = None, status_code: int = 200):
    """Страница ведомости: месяц, файл и сообщение об ошибке, если была."""
    return TEMPLATES.TemplateResponse(
        request,
        "vedomost.html",
        {"user": user, "tab": "vedomost", "error": error,
         "month": month or _current_month()},
        status_code=status_code,
    )


@router.get("", response_class=HTMLResponse)
def vedomost_page(request: Request):
    """Ведомость: выбор месяца и загрузка файла больницы."""
    user = _guard_page(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    return _vedomost_page(request, user)


@router.post("/spread")
async def spread_file(
    request: Request,
    month: Annotated[str, Form()],
    file: Annotated[UploadFile, File()],
):
    """Разнести ведомость и вернуть тот же файл с двумя нашими листами."""
    access.require(request, access.DISTRIBUTION_MANAGE)
    user = access.current_user(request)
    source = await file.read()
    try:
        result = _service(request).spread(month.strip(), source)
    except ValueError as exc:
        return _vedomost_page(request, user, error=str(exc), month=month, status_code=400)
    return web.xlsx_response(xlsx.build(source, result), xlsx.filename(month.strip()))


@router.get("/counts", response_class=HTMLResponse)
def counts_page(request: Request):
    """Счёт поданных анестезий за период: период и разрез — из строки запроса."""
    user = _guard_page(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)

    from_date, to_date = _requested_period(request)
    by = request.query_params.get("by") or BY_DOCTOR
    context = {
        "user": user,
        "tab": "counts",
        "from": from_date,
        "to": to_date,
        "by": by,
        "by_label": BY_LABELS.get(by, by),
    }
    try:
        result = _service(request).aggregate(from_date, to_date, by)
    except ValueError as exc:
        return TEMPLATES.TemplateResponse(
            request,
            "counts.html",
            {**context, "error": str(exc), "result": None},
            status_code=400,
        )
    return TEMPLATES.TemplateResponse(
        request,
        "counts.html",
        {**context, "error": None, "result": result},
    )


@router.get("/api/aggregate")
def aggregate(request: Request):
    """Счёт одним JSON: {from, to, by, total, rows: [{key, count, name?}]}."""
    access.require(request, access.DISTRIBUTION_MANAGE)
    from_date, to_date = _requested_period(request)
    by = request.query_params.get("by") or BY_DOCTOR
    if by not in ALLOWED_BY:
        raise HTTPException(
            status_code=400,
            detail="Параметр by должен быть одним из: " + ", ".join(ALLOWED_BY),
        )
    try:
        return _service(request).aggregate(from_date, to_date, by)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
