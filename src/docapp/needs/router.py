"""Маршруты подприложения «Потребности»: страница, JSON-API, отчёты (задача T6).

Доступ по ролям (ADR-11): врач — 403 везде (подприложение для медсестёр);
медсестра — каталог и заявки (создание, правка, отправка своих); старшая
сестра и заведующий — всё: доска, правка любых заявок, отчёты, аналитика,
закрытие/переоткрытие недель.

Авторизация — current_user из docapp.web.app (импорт на уровне модуля,
как в wiki.router); из-за этого роутер подключается в create_app
лениво, внутри функции. Шаблоны — свои (needs/templates) поверх общего
base.html (web/templates): Jinja2Templates принимает список директорий.

Ошибки бизнес-слоя транслируются в HTTP: NeedsForbidden → 403,
NeedsClosed → 409, прочие ValueError → 400. Полный UI страниц — в T8;
сейчас страницы-заглушки.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

import docapp.web.app
from docapp.config import git_revision
from docapp.domain.employee import DOCTOR, Employee
from docapp.needs.analytics import summarize
from docapp.needs.report import aggregate_requests, build_xlsx, html_table
from docapp.needs.service import (
    ALLOWED_FULL,
    NeedsClosed,
    NeedsForbidden,
    NeedsService,
    monday_of_week,
)

#: Media type .xlsx для отчётов (report.xlsx и analytics.xlsx).
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

#: Директории шаблонов: сначала «Потребностей», затем общие (чтобы needs.html
#: мог наследовать base.html). Starlette принимает список директорий.
needs_templates_dir = Path(__file__).parent / "templates"
web_templates_dir = Path(docapp.web.app.__file__).parent / "templates"
TEMPLATES = Jinja2Templates(
    directory=[needs_templates_dir, web_templates_dir],
    context_processors=[lambda request: {"git_revision": git_revision()}],
)

router = APIRouter(prefix="/needs")


def _service(request: Request) -> NeedsService:
    """Сервис «Потребностей» из state приложения."""
    return request.app.state.needs["service"]


def _content_disposition(filename: str) -> str:
    """Content-Disposition: ASCII-имя в кавычках, кириллица — RFC 5987 (filename*)."""
    name = Path(filename).name
    try:
        name.encode("latin-1")
    except UnicodeEncodeError:
        return f"attachment; filename*=UTF-8''{quote(name)}"
    return f'attachment; filename="{name}"'


# ── проверка ролей ──────────────────────────────────────────────────

def _require_not_doctor(user: Employee) -> None:
    """Врач не имеет доступа к «Потребностям» вовсе (ADR-11): 403 везде."""
    if user.role == DOCTOR:
        raise HTTPException(
            status_code=403,
            detail="Потребности доступны медсестре, старшей сестре и заведующему",
        )


def _require_full(user: Employee) -> None:
    """Полные права (доска, отчёты, закрытие недель): head_nurse/head, иначе 403."""
    _require_not_doctor(user)
    if user.role not in ALLOWED_FULL:
        raise HTTPException(
            status_code=403,
            detail="Доступно только старшей сестре или заведующему",
        )


def _api_user(request: Request) -> Employee:
    """Текущий пользователь для JSON-API; без сессии — 401."""
    user = docapp.web.app.current_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Требуется авторизация")
    assert user.id is not None  # вошедший сотрудник всегда с id из БД
    return user


# ── страница подприложения ──────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
def needs_page(request: Request):
    """Страница подприложения «Потребности» (заглушка; полный UI — в T8)."""
    user = docapp.web.app.current_user(request)
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
    catalog_obj = request.app.state.needs["catalog"]
    return {"bases": catalog_obj.bases(), "groups": catalog_obj.groups()}


# ── заявки ──────────────────────────────────────────────────────────

@router.get("/api/request")
def get_request(request: Request):
    """Заявка точки за неделю с учётом прав (nurse+): 404, если недоступна.

    Медсестра видит свою заявку и чужие отправленные; чужой черновик
    скрыт (404). head_nurse/head видят любую.
    """
    user = _api_user(request)
    _require_not_doctor(user)
    params = request.query_params
    base = params.get("base") or ""
    point = params.get("point") or ""
    if not base or not point:
        raise HTTPException(status_code=400, detail="Параметры base и point обязательны")
    week = params.get("week") or monday_of_week()
    result = _service(request).get_for_user(user.id, user.role, base, point, week)
    if result is None:
        raise HTTPException(status_code=404, detail="Заявка не найдена")
    return result


@router.post("/api/request")
async def save_request(request: Request):
    """Создать или отредактировать заявку (nurse+): снимки unit/grp из каталога.

    Тело: {base, point, week?, lines: [{item, qty}], status?}. Ответ —
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
    week = body.get("week") or monday_of_week()
    lines = body.get("lines") or []
    status = body.get("status") or "draft"
    try:
        saved = _service(request).save(
            user.id, user.role, base, point, week, lines, status=status
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
    """Отправить заявку (nurse+): статус 'sent' + предупреждения о нулевых строках.

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
    week = body.get("week") or monday_of_week()
    try:
        result = _service(request).submit(user.id, user.role, base, point, week)
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
    """Доска старшей: все точки обеих баз со статусом, автором и закрытием недели.

    Только head_nurse/head. К ячейкам service.board добавлены author_name
    (ФИО автора заявки из employees или None) и is_closed (закрыта ли
    неделя для базы).
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
        cell["is_closed"] = service.is_closed(cell["base"], week)
    return {"board": cells, "week": week}


@router.post("/api/close")
async def close_week(request: Request):
    """Закрыть неделю для базы (только head_nurse/head): 200 или 403."""
    user = _api_user(request)
    _require_full(user)
    body = await request.json()
    base = str(body.get("base") or "")
    if not base:
        return JSONResponse({"error": "base обязателен"}, status_code=400)
    week = body.get("week") or monday_of_week()
    try:
        result = _service(request).close(user.id, user.role, base, week)
    except NeedsForbidden as exc:
        return JSONResponse({"error": str(exc)}, status_code=403)
    return result


@router.post("/api/reopen")
async def reopen_week(request: Request):
    """Переоткрыть неделю для базы (только head_nurse/head): 200 или 403."""
    user = _api_user(request)
    _require_full(user)
    body = await request.json()
    base = str(body.get("base") or "")
    if not base:
        return JSONResponse({"error": "base обязателен"}, status_code=400)
    week = body.get("week") or monday_of_week()
    try:
        result = _service(request).reopen(user.id, user.role, base, week)
    except NeedsForbidden as exc:
        return JSONResponse({"error": str(exc)}, status_code=403)
    return result


# ── отчёт-форма для аптеки ──────────────────────────────────────────

def _report_agg(request: Request) -> dict:
    """Агрегат отчёта по базе за неделю (только отправленные заявки)."""
    base = request.query_params.get("base") or ""
    if not base:
        raise HTTPException(status_code=400, detail="Параметр base обязателен")
    week = request.query_params.get("week") or monday_of_week()
    state = request.app.state.needs
    requests = state["store"].list_requests(base, week)
    return aggregate_requests(requests, base, week, state["catalog"])


@router.get("/report", response_class=HTMLResponse)
def report_page(request: Request):
    """Отчёт-форма для аптеки по базе за неделю: HTML-таблица (head_nurse/head)."""
    user = docapp.web.app.current_user(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    _require_full(user)
    agg = _report_agg(request)
    return TEMPLATES.TemplateResponse(
        request,
        "report.html",
        {"user": user, "flash": None, "agg": agg, "report": html_table(agg)},
    )


@router.get("/report.xlsx")
def report_xlsx(request: Request):
    """Отчёт-форма для аптеки: .xlsx (два листа) для скачивания (head_nurse/head)."""
    user = docapp.web.app.current_user(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    _require_full(user)
    agg = _report_agg(request)
    filename = f"потребности-{agg['base']}-{agg['week_start']}.xlsx"
    return Response(
        content=build_xlsx(agg),
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": _content_disposition(filename)},
    )


# ── аналитика ───────────────────────────────────────────────────────

def _analytics_params(request: Request) -> tuple[str, str, str | None, str | None, str | None]:
    """Параметры аналитики: from/to обязательны, base/point/group — опциональны."""
    params = request.query_params
    from_week = params.get("from") or ""
    to_week = params.get("to") or ""
    if not from_week or not to_week:
        raise HTTPException(status_code=400, detail="Параметры from и to обязательны")
    return (
        from_week,
        to_week,
        params.get("base") or None,
        params.get("point") or None,
        params.get("group") or None,
    )


@router.get("/analytics", response_class=HTMLResponse)
def analytics_page(request: Request):
    """Страница аналитики по истории заявок (заглушка; UI — в T8)."""
    user = docapp.web.app.current_user(request)
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
    from_week, to_week, base, point, group = _analytics_params(request)
    state = request.app.state.needs
    requests = state["store"].list_range(from_week, to_week, base, point)
    return summarize(requests, state["catalog"], from_week, to_week, base, group)


@router.get("/api/analytics.xlsx")
def analytics_xlsx(request: Request):
    """Экспорт свода за период в .xlsx (только head_nurse/head)."""
    user = docapp.web.app.current_user(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    _require_full(user)
    from_week, to_week, base, point, group = _analytics_params(request)
    state = request.app.state.needs
    requests = state["store"].list_range(from_week, to_week, base, point)
    summary = summarize(requests, state["catalog"], from_week, to_week, base, group)
    filename = f"потребности-аналитика-{from_week}-{to_week}.xlsx"
    return Response(
        content=build_xlsx(summary),
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": _content_disposition(filename)},
    )
