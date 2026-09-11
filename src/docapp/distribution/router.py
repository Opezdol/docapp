"""HTTP-адаптер «Распределения»: страница за период и данные для неё.

Правил здесь нет: разрез и счёт — в `distribution/service.py`, права — в
`core/access`. Роутер только переводит запрос в вызов сервиса (ADR-0017).

Раздел целиком принадлежит заведующему (`distribution.manage`, ADR-0024):
загрузка ведомости и выгрузка появятся здесь же, см. `docs/ТЗ-распределение.md`.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from docapp.core import access, web
from docapp.core.registry import container_of
from docapp.distribution.container import DistributionContainer
from docapp.distribution.service import ALLOWED_BY, BY_DOCTOR, BY_LABELS

TEMPLATES = web.templates(Path(__file__).parent / "templates")

router = APIRouter(prefix="/distribution")


def _service(request: Request):
    """Контейнер модуля: сервис распределения поверх интерфейсов соседей."""
    return container_of(request, "distribution", DistributionContainer).service


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
    """Страница распределения: период и разрез — из строки запроса."""
    user = access.current_user(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    access.ensure(user, access.DISTRIBUTION_MANAGE,
                  message="Доступ к распределению запрещён")

    from_date, to_date = _requested_period(request)
    by = request.query_params.get("by") or BY_DOCTOR
    context = {
        "user": user,
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
            "distribution.html",
            {**context, "error": str(exc), "result": None},
            status_code=400,
        )
    return TEMPLATES.TemplateResponse(
        request,
        "distribution.html",
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
