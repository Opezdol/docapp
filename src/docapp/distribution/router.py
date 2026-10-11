"""HTTP-адаптер «Распределения»: ведомость, прогон, публикация и счёт.

Правил здесь нет: разбор файла — в `vedomost.py`, разноска — в `spread.py`,
сборка книги — в `xlsx.py`, прогон на диске — в `run.py`, права — в
`core/access`. Роутер только переводит HTTP в вызовы сервиса и обратно
(ADR-0017).

Раздел целиком принадлежит заведующему (`distribution.manage`, ADR-0024):

- `/distribution` — ведомость: месяц, файл и результат последнего прогона;
- `/distribution/spread` — прогон: разнести и показать результат, без меток;
- `/distribution/download` — файл результата последнего прогона;
- `/distribution/publish` — поставить метки «учтена» (ADR-0027);
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
from docapp.distribution.container import DistributionContainer
from docapp.distribution.run import Run, human
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


def _run_context(run: Run | None) -> dict:
    """Прогон и его даты для страницы; пусто, если прогона ещё не было."""
    if run is None:
        return {"run": None, "created_label": "", "published_label": ""}
    return {
        "run": run,
        "created_label": human(run.created_at),
        "published_label": human(run.published_at),
    }


def _vedomost_page(request: Request, user, *, error: str | None = None,
                   note: str | None = None, month: str | None = None,
                   run: Run | None = None, status_code: int = 200):
    """Страница ведомости: форма, сообщение и результат последнего прогона.

    Прогон передаётся явно: свежий — после прогона и публикации, прежний —
    когда последнее действие закончилось ошибкой (прежний результат при этом
    на месте, и прятать его незачем).
    """
    return TEMPLATES.TemplateResponse(
        request,
        "vedomost.html",
        {"user": user, "tab": "vedomost", "error": error, "note": note,
         "month": month or _current_month(), **_run_context(run)},
        status_code=status_code,
    )


@router.get("", response_class=HTMLResponse)
def vedomost_page(request: Request):
    """Ведомость: месяц, файл и результат последнего прогона."""
    user = _guard_page(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    return _vedomost_page(request, user, run=_service(request).current_run())


@router.post("/spread")
async def spread_file(
    request: Request,
    month: Annotated[str, Form()],
    file: Annotated[UploadFile, File()],
):
    """Прогон: разнести ведомость, показать результат и сохранить его.

    Метки «учтена» здесь не ставятся, и файл пока не отдаётся: сначала
    заведующий смотрит, что получилось, и только потом закрывает месяц
    (ADR-0027).
    """
    access.require(request, access.DISTRIBUTION_MANAGE)
    user = access.current_user(request)
    source = await file.read()
    service = _service(request)
    try:
        run = service.check(month.strip(), source, source_name=file.filename or "")
    except ValueError as exc:
        return _vedomost_page(request, user, error=str(exc), month=month,
                              run=service.current_run(), status_code=400)
    return _vedomost_page(
        request,
        user,
        run=run,
        note="Проверка сделана: результат ниже. Метки «учтено» ещё не поставлены — "
             "месяц закрывает кнопка «Опубликовать».",
    )


@router.get("/download")
def download_file(request: Request):
    """Файл результата последнего прогона — тот же, что был разнесён."""
    access.require(request, access.DISTRIBUTION_MANAGE)
    user = access.current_user(request)
    service = _service(request)
    try:
        payload, filename = service.download()
    except ValueError as exc:
        return _vedomost_page(request, user, error=str(exc),
                              run=service.current_run(), status_code=400)
    return web.xlsx_response(payload, filename)


@router.post("/publish")
def publish_run(request: Request):
    """Опубликовать прогон: поставить метки «учтена» (ADR-0027).

    Только это действие закрывает месяц: до него бейджа «учтено» нет ни у
    врача, ни у сестры.
    """
    access.require(request, access.DISTRIBUTION_MANAGE)
    user = access.current_user(request)
    service = _service(request)
    try:
        marks, run = service.publish()
    except ValueError as exc:
        return _vedomost_page(request, user, error=str(exc),
                              run=service.current_run(), status_code=400)
    return _vedomost_page(
        request,
        user,
        run=run,
        note=f"Опубликовано за {run.month}: помечено записей — {marks['marked']}, "
             f"снято прежних — {marks['cleared']}. Врач и медсестра видят «учтено».",
    )


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
