"""HTTP-адаптер «Сводки»: страница за период и данные для неё.

Правил здесь нет: разрез и подсчёт — в `summary/service.py`, права — в
`core/access`. Роутер только переводит запрос в вызов сервиса (ADR-0017).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from docapp.core import access, web
from docapp.core.registry import container_of
from docapp.summary.container import SummaryContainer
from docapp.summary.service import ALLOWED_BY, BY_DOCTOR, BY_LABELS

TEMPLATES = web.templates(Path(__file__).parent / "templates")

router = APIRouter(prefix="/summary")


def _service(request: Request):
    """Контейнер модуля: сервис сводки поверх интерфейсов соседей."""
    return container_of(request, "summary", SummaryContainer).service


def _scope(request: Request):
    """Пользователь с правом смотреть записи (свои или все).

    Свои — у врача и медсестры (`records.view_own`), все — у заведующего и
    старшей сестры (`records.view_all`). Фильтр «только свои» применяет сервис.
    """
    return access.require(request, access.RECORDS_VIEW_ALL, access.RECORDS_VIEW_OWN)


def _month_bounds() -> tuple[str, str]:
    """Период по умолчанию — текущий месяц: от первого числа до сегодня."""
    today = date.today()
    return today.replace(day=1).isoformat(), today.isoformat()


def _requested_period(request: Request) -> tuple[str, str]:
    """Период из запроса; пустой — текущий месяц."""
    default_from, default_to = _month_bounds()
    params = request.query_params
    return params.get("from") or default_from, params.get("to") or default_to


@router.get("", response_class=HTMLResponse)
def page(request: Request):
    """Страница сводки: период и разрез — из строки запроса, по умолчанию месяц."""
    user = access.current_user(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    access.ensure(user, access.RECORDS_VIEW_ALL, access.RECORDS_VIEW_OWN,
                  message="Доступ к сводке запрещён")

    from_date, to_date = _requested_period(request)
    by = request.query_params.get("by") or BY_DOCTOR
    try:
        summary = _service(request).aggregate(user, from_date, to_date, by)
    except ValueError as exc:
        return TEMPLATES.TemplateResponse(
            request,
            "summary.html",
            {
                "user": user,
                "error": str(exc),
                "summary": None,
                "from": from_date,
                "to": to_date,
                "by": by,
                "by_label": BY_LABELS.get(by, by),
            },
            status_code=400,
        )
    return TEMPLATES.TemplateResponse(
        request,
        "summary.html",
        {
            "user": user,
            "error": None,
            "summary": summary,
            "from": from_date,
            "to": to_date,
            "by": by,
            "by_label": BY_LABELS.get(by, by),
        },
    )


@router.get("/api/aggregate")
def aggregate(request: Request):
    """Сводка одним JSON: {from, to, by, scope, total, rows: [{key, count, name?}]}."""
    user = _scope(request)
    from_date, to_date = _requested_period(request)
    by = request.query_params.get("by") or BY_DOCTOR
    if by not in ALLOWED_BY:
        raise HTTPException(
            status_code=400,
            detail="Параметр by должен быть одним из: " + ", ".join(ALLOWED_BY),
        )
    try:
        return _service(request).aggregate(user, from_date, to_date, by)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
