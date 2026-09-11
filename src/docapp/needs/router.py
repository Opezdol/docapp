"""Маршруты подприложения «Потребности»: страница, JSON-API, отчёты (задача T6).

Доступ по ролям (ADR-11): врач — 403 везде (подприложение для медсестёр);
медсестра — каталог и заявки (создание, правка, отправка своих); старшая
сестра и заведующий — всё: доска, правка любых заявок, отчёты, аналитика,
закрытие/переоткрытие недель.

Авторизация — core.access (`current_user`, `require`, `ensure`); модуль больше
не импортирует docapp.web.app, поэтому кругового импорта нет и роутер
подключается в create_app обычным include_router. Шаблоны — свои
(needs/templates) поверх общего base.html (web/templates): Jinja2Templates
принимает список директорий.

Ошибки бизнес-слоя транслируются в HTTP: NeedsForbidden → 403,
NeedsClosed → 409, прочие ValueError → 400. Полный UI страниц — в T8;
сейчас страницы-заглушки.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from docapp.core import access, web
from docapp.core.registry import container_of
from docapp.domain.employee import Employee
from docapp.needs.analytics import summarize
from docapp.needs.catalog import CATEGORY_LABELS, CATEGORY_MEDICAMENTS, CATEGORY_SOLUTIONS
from docapp.needs.container import NeedsContainer
from docapp.needs.report import aggregate_requests, build_xlsx, html_table
from docapp.needs.service import (
    NeedsClosed,
    NeedsForbidden,
    NeedsService,
    monday_of_week,
)

#: Директории шаблонов: сначала «Потребностей», затем общие (base.html).
TEMPLATES = web.templates(Path(__file__).parent / "templates")

router = APIRouter(prefix="/needs")


def _container(request: Request) -> NeedsContainer:
    """Контейнер модуля «Потребности» из состояния приложения."""
    return container_of(request, "needs", NeedsContainer)


def _service(request: Request) -> NeedsService:
    """Сервис «Потребностей» из контейнера модуля."""
    return _container(request).service


def _valid_category(value: str | None, *, param: str = "category") -> str:
    """Проверить обязательный параметр раздела; невалидный — HTTP 400."""
    if value in (CATEGORY_SOLUTIONS, CATEGORY_MEDICAMENTS):
        return value
    raise HTTPException(
        status_code=400,
        detail=f"Параметр {param} должен быть 'solutions' или 'medicaments'",
    )


# ── проверка ролей ──────────────────────────────────────────────────

def _require_not_doctor(user: Employee) -> None:
    """Врач не имеет доступа к «Потребностям» вовсе (ADR-11, ADR-0023)."""
    access.ensure(
        user,
        access.NEEDS_VIEW_OWN,
        access.NEEDS_VIEW_ALL,
        message="Потребности доступны медсестре, старшей сестре и заведующему",
    )


def _require_full(user: Employee) -> None:
    """Полные права: доска, отчёты, закрытие недель (head_nurse/head)."""
    access.ensure(
        user, access.NEEDS_MANAGE, message="Доступно только старшей сестре или заведующему"
    )


def _api_user(request: Request) -> Employee:
    """Текущий пользователь для JSON-API; без сессии — 401."""
    user = access.api_user(request)
    assert user.id is not None  # вошедший сотрудник всегда с id из БД
    return user


# ── страница подприложения ──────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
def needs_page(request: Request):
    """Страница подприложения «Потребности» (заглушка; полный UI — в T8)."""
    user = access.current_user(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    _require_not_doctor(user)
    return TEMPLATES.TemplateResponse(
        request, "needs.html", {"user": user, "flash": None}
    )


# ── каталог ─────────────────────────────────────────────────────────

@router.get("/api/catalog")
def catalog(request: Request):
    """Каталог потребностей: базы с точками и группы с препаратами (nurse+)."""
    user = _api_user(request)
    _require_not_doctor(user)
    catalog_obj = _container(request).catalog
    return {"bases": catalog_obj.bases(), "groups": catalog_obj.groups()}


@router.get("/api/closed")
def closed_sections(request: Request):
    """Закрытые разделы недели (nurse+): список {base, category} за неделю.

    Медсестре это нужно, чтобы сразу видеть закрытые разделы (не дожидаясь
    409 при сохранении). Данные не персональные — только факт закрытия
    раздела базы, поэтому доступны всем кроме врача.
    """
    user = _api_user(request)
    _require_not_doctor(user)
    week = request.query_params.get("week") or monday_of_week()
    return {"closed": _service(request).closed_sections(week), "week": week}


@router.get("/api/points")
def points_status(request: Request):
    """Статусы точек раздела (nurse+): для цветовой индикации в сетке точек.

    Медсестра видит свой черновик/отправленную и чужие отправленные; чужой
    черновик скрыт (none). Обязателен параметр category.
    """
    user = _api_user(request)
    _require_not_doctor(user)
    category = _valid_category(request.query_params.get("category"))
    week = request.query_params.get("week") or monday_of_week()
    return {
        "points": _service(request).points_for_user(user.id, user.role, category, week),
        "category": category,
        "week": week,
    }


# ── заявки ──────────────────────────────────────────────────────────

@router.get("/api/request")
def get_request(request: Request):
    """Заявка точки раздела за неделю с учётом прав (nurse+): 404, если недоступна.

    Медсестра видит свою заявку и чужие отправленные; чужой черновик
    скрыт (404). head_nurse/head видят любую. Обязателен параметр category.
    """
    user = _api_user(request)
    _require_not_doctor(user)
    params = request.query_params
    base = params.get("base") or ""
    point = params.get("point") or ""
    category = _valid_category(params.get("category"))
    if not base or not point:
        raise HTTPException(status_code=400, detail="Параметры base и point обязательны")
    week = params.get("week") or monday_of_week()
    result = _service(request).get_for_user(user.id, user.role, base, point, category, week)
    if result is None:
        raise HTTPException(status_code=404, detail="Заявка не найдена")
    return result


@router.post("/api/request")
async def save_request(request: Request):
    """Создать или отредактировать заявку раздела (nurse+): снимки unit/grp из каталога.

    Тело: {base, point, category, week?, lines: [{item, qty}], status?}. Ответ —
    {"request": {...}} с полной заявкой (строки со снимками unit/grp).
    NeedsForbidden → 403, NeedsClosed → 409, ValueError → 400.
    """
    user = _api_user(request)
    _require_not_doctor(user)
    body = await request.json()
    base = str(body.get("base") or "")
    point = str(body.get("point") or "")
    if not base or not point:
        return JSONResponse({"error": "base и point обязательны"}, status_code=400)
    category = _valid_category(body.get("category"))
    week = body.get("week") or monday_of_week()
    lines = body.get("lines") or []
    status = body.get("status") or "draft"
    try:
        saved = _service(request).save(
            user.id, user.role, base, point, category, week, lines, status=status
        )
    except NeedsForbidden as exc:
        return JSONResponse({"error": str(exc)}, status_code=403)
    except NeedsClosed as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return {"request": saved}


@router.post("/api/request/submit")
async def submit_request(request: Request):
    """Отправить заявку раздела (nurse+): статус 'sent' + предупреждения о нулевых строках.

    Ответ — {"request": {...}, "warnings": [...]}. NeedsForbidden → 403,
    NeedsClosed → 409, ValueError → 400.
    """
    user = _api_user(request)
    _require_not_doctor(user)
    body = await request.json()
    base = str(body.get("base") or "")
    point = str(body.get("point") or "")
    if not base or not point:
        return JSONResponse({"error": "base и point обязательны"}, status_code=400)
    category = _valid_category(body.get("category"))
    week = body.get("week") or monday_of_week()
    try:
        result = _service(request).submit(user.id, user.role, base, point, category, week)
    except NeedsForbidden as exc:
        return JSONResponse({"error": str(exc)}, status_code=403)
    except NeedsClosed as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return result


# ── доска старшей и закрытие недель ─────────────────────────────────

@router.get("/api/board")
def board(request: Request):
    """Доска старшей: все точки обеих баз по разделам со статусом, автором и закрытием.

    Только head_nurse/head. К ячейкам service.board добавлены author_name
    (ФИО автора заявки из employees или None) и is_closed (закрыт ли
    раздел базы: (base, category)).
    """
    user = _api_user(request)
    _require_full(user)
    week = request.query_params.get("week") or monday_of_week()
    service = _service(request)
    cells = service.board(week)
    employees = request.app.state.employees
    for cell in cells:
        author_id = cell["author_id"]
        author = employees.get_by_id(author_id) if author_id is not None else None
        cell["author_name"] = author.full_name if author else None
        cell["is_closed"] = service.is_closed(cell["base"], cell["category"], week)
    return {"board": cells, "week": week}


@router.post("/api/close")
async def close_week(request: Request):
    """Закрыть неделю для базы и раздела (только head_nurse/head): 200 или 403."""
    user = _api_user(request)
    _require_full(user)
    body = await request.json()
    base = str(body.get("base") or "")
    if not base:
        return JSONResponse({"error": "base обязателен"}, status_code=400)
    category = _valid_category(body.get("category"))
    week = body.get("week") or monday_of_week()
    try:
        result = _service(request).close(user.id, user.role, base, category, week)
    except NeedsForbidden as exc:
        return JSONResponse({"error": str(exc)}, status_code=403)
    return result


@router.post("/api/reopen")
async def reopen_week(request: Request):
    """Переоткрыть неделю для базы и раздела (только head_nurse/head): 200 или 403."""
    user = _api_user(request)
    _require_full(user)
    body = await request.json()
    base = str(body.get("base") or "")
    if not base:
        return JSONResponse({"error": "base обязателен"}, status_code=400)
    category = _valid_category(body.get("category"))
    week = body.get("week") or monday_of_week()
    try:
        result = _service(request).reopen(user.id, user.role, base, category, week)
    except NeedsForbidden as exc:
        return JSONResponse({"error": str(exc)}, status_code=403)
    return result


# ── отчёт-форма для аптеки ──────────────────────────────────────────

def _report_agg(request: Request) -> tuple[dict, str]:
    """Агрегат отчёта по базе и разделу за неделю (только отправленные заявки)."""
    base = request.query_params.get("base") or ""
    if not base:
        raise HTTPException(status_code=400, detail="Параметр base обязателен")
    category = _valid_category(request.query_params.get("category"))
    week = request.query_params.get("week") or monday_of_week()
    container = _container(request)
    requests = container.store.list_requests(base, category, week)
    return aggregate_requests(requests, base, category, week, container.catalog), category


@router.get("/report", response_class=HTMLResponse)
def report_page(request: Request):
    """Отчёт-форма для аптеки по базе и разделу за неделю: HTML (head_nurse/head)."""
    user = access.current_user(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    _require_full(user)
    agg, category = _report_agg(request)
    return TEMPLATES.TemplateResponse(
        request,
        "report.html",
        {
            "user": user,
            "flash": None,
            "agg": agg,
            "report": html_table(agg),
            "category": category,
            "category_label": CATEGORY_LABELS[category],
        },
    )


@router.get("/report.xlsx")
def report_xlsx(request: Request):
    """Отчёт-форма для аптеки: .xlsx раздела для скачивания (head_nurse/head)."""
    user = access.current_user(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    _require_full(user)
    agg, category = _report_agg(request)
    label = CATEGORY_LABELS[category]
    filename = f"потребности-{label}-{agg['base']}-{agg['week_start']}.xlsx"
    return web.xlsx_response(build_xlsx(agg), filename)


# ── аналитика ───────────────────────────────────────────────────────

def _analytics_params(request: Request) -> tuple[str, str, str | None, str | None, str | None, str | None]:
    """Параметры аналитики: from/to обязательны, base/point/group/section — опциональны."""
    params = request.query_params
    from_week = params.get("from") or ""
    to_week = params.get("to") or ""
    if not from_week or not to_week:
        raise HTTPException(status_code=400, detail="Параметры from и to обязательны")
    section = params.get("section") or None
    if section is not None and section not in (CATEGORY_SOLUTIONS, CATEGORY_MEDICAMENTS):
        raise HTTPException(
            status_code=400,
            detail="Параметр section должен быть 'solutions' или 'medicaments'",
        )
    return (
        from_week,
        to_week,
        params.get("base") or None,
        params.get("point") or None,
        params.get("group") or None,
        section,
    )


@router.get("/analytics", response_class=HTMLResponse)
def analytics_page(request: Request):
    """Страница аналитики по истории заявок (заглушка; UI — в T8)."""
    user = access.current_user(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    _require_full(user)
    return TEMPLATES.TemplateResponse(
        request, "analytics.html", {"user": user, "flash": None}
    )


@router.get("/api/analytics")
def analytics(request: Request):
    """Свод заявок за период недель (только head_nurse/head): solutions/groups."""
    user = _api_user(request)
    _require_full(user)
    from_week, to_week, base, point, group, section = _analytics_params(request)
    container = _container(request)
    requests = container.store.list_range(from_week, to_week, base, point)
    return summarize(
        requests, container.catalog, from_week, to_week, base, group, section
    )


@router.get("/api/analytics.xlsx")
def analytics_xlsx(request: Request):
    """Экспорт свода за период в .xlsx (только head_nurse/head)."""
    user = access.current_user(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    _require_full(user)
    from_week, to_week, base, point, group, section = _analytics_params(request)
    container = _container(request)
    requests = container.store.list_range(from_week, to_week, base, point)
    summary = summarize(
        requests, container.catalog, from_week, to_week, base, group, section
    )
    filename = f"потребности-аналитика-{from_week}-{to_week}.xlsx"
    return web.xlsx_response(build_xlsx(summary), filename)
