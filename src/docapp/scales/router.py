"""HTTP-адаптер «Шкал»: страница-калькулятор (ТЗ-шкалы, ADR-0028).

Правил здесь нет: пункты, баллы и пороги — в `calc.py`, права — в
`core/access`. Роутер только читает форму и отдаёт шаблон.

Раздел состояния не имеет: в БД не пишется и из БД не читается ничего. Поэтому
`POST /scales` — не сохранение, а пересчёт: ответы возвращаются в форму, чтобы
врач не вводил их заново (ТЗ §4).
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from docapp.core import access, web
from docapp.core.registry import container_of
from docapp.domain.employee import Employee
from docapp.scales import calc
from docapp.scales.container import ScalesContainer

TEMPLATES = web.templates(Path(__file__).parent / "templates")

router = APIRouter(prefix="/scales")


def _page_user(request: Request) -> Employee | None:
    """Пользователь страницы: нет сессии — None (уводим на вход), нет прав — 403."""
    user = access.current_user(request)
    if user is not None:
        access.ensure(
            user,
            access.SCALES_USE,
            message="Раздел «Шкалы» доступен врачу, редактору и заведующему",
        )
    return user


def _scales(request: Request) -> tuple[calc.Scale, ...]:
    """Каталог шкал из контейнера модуля (шов реестра, ADR-0017)."""
    return container_of(request, "scales", ScalesContainer).scales


def _render(
    request: Request,
    user: Employee,
    selected: tuple[str, ...],
    answers: dict[str, str],
) -> HTMLResponse:
    """Отрисовать страницу: галочки, пункты выбранных шкал и результат."""
    return TEMPLATES.TemplateResponse(
        request,
        "scales.html",
        {
            "user": user,
            "scales": _scales(request),
            "selected": list(selected),
            "results": calc.plan(selected, answers),
        },
    )


@router.get("", response_class=HTMLResponse)
def scales_page(request: Request):
    """Пустая форма: четыре галочки и справочный блок."""
    user = _page_user(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    return _render(request, user, selected=(), answers={})


@router.post("", response_class=HTMLResponse)
async def scales_count(request: Request):
    """Пересчёт: пункты выбранных шкал остаются заполненными, рядом — результат."""
    user = _page_user(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    form = await request.form()
    answers = {key: value for key, value in form.items() if isinstance(value, str)}
    selected = calc.selected_keys(
        [value for value in form.getlist("scales") if isinstance(value, str)]
    )
    return _render(request, user, selected, answers)
