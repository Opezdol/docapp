"""Маршруты подприложения «Дежурства»: страница, JSON-API, выгрузка .xlsx.

Доступ по ролям: врач — вносит/отправляет свои отчёты за смену; заведующий —
видит все отчёты и выгружает разлиновку за период. Остальным ролям — 403,
пункт меню скрыт.

Авторизация — core.access (`current_user`, `require`, `ensure`); модуль больше не
импортирует docapp.web.app, поэтому роутер подключается в create_app обычным
include_router. Шаблон — свой (duty/templates) поверх общего base.html
(web/templates).
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from docapp.core import access, web
from docapp.core.registry import container_of
from docapp.domain.employee import Employee
from docapp.duty.config import BASES
from docapp.duty.container import DutyContainer
from docapp.duty.report import build_xlsx
from docapp.duty.service import DutyClosed, DutyService
from docapp.duty import statuses

#: Шаблоны «Дежурств» поверх общих (base.html).
TEMPLATES = web.templates(Path(__file__).parent / "templates")

router = APIRouter(prefix="/duty")


def _container(request: Request) -> DutyContainer:
    """Контейнер модуля «Дежурства» из состояния приложения."""
    return container_of(request, "duty", DutyContainer)


def _service(request: Request) -> DutyService:
    """Сервис «Дежурств» из контейнера модуля."""
    return _container(request).service


# ── проверка ролей ──────────────────────────────────────────────────

def _require_access(user: Employee) -> None:
    """К «Дежурствам» допускаются врач (свой отчёт) и заведующий (все отчёты)."""
    access.ensure(
        user,
        access.DUTY_VIEW_OWN,
        access.DUTY_VIEW_ALL,
        message="Дежурства доступны только врачам и заведующему",
    )


def _require_doctor(user: Employee) -> None:
    """Вносить отчёт за смену может только врач (заведующий — не вносит)."""
    access.ensure(
        user, access.DUTY_EDIT_OWN, message="Ввод отчёта доступен только врачу"
    )


def _require_head(user: Employee) -> None:
    """Доска, закрытие смены и выгрузка разлиновки — только заведующий."""
    access.ensure(user, access.DUTY_MANAGE, message="Доступно только заведующему")


def _api_user(request: Request) -> Employee:
    """Текущий пользователь для JSON-API; без сессии — 401."""
    user = access.api_user(request)
    assert user.id is not None  # вошедший сотрудник всегда с id из БД
    return user


# ── страница подприложения ──────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
def duty_page(request: Request):
    user = access.current_user(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    _require_access(user)
    return TEMPLATES.TemplateResponse(
        request, "duty.html", {"user": user, "bases": list(BASES)}
    )


# ── врач: отчёт за смену ─────────────────────────────────────────────

@router.get("/api/report")
def get_report(request: Request):
    """Состояние текущей смены врача: {shift_date, is_open, report}."""
    user = _api_user(request)
    _require_doctor(user)
    base = request.query_params.get("base") or ""
    if not base:
        raise HTTPException(status_code=400, detail="Параметр base обязателен")
    user_id = user.id
    assert user_id is not None  # вошедший сотрудник всегда с id из БД
    return _service(request).get_for_doctor(user_id, base)


@router.post("/api/report")
async def save_report(request: Request):
    """Сохранить черновик текущей смены. Тело: {base, operations: [...]}.

    operations: [{operation, start_time, end_time}]. DutyClosed → 409,
    ValueError → 400. Ответ — {"report": {...}}.
    """
    user = _api_user(request)
    _require_doctor(user)
    body = await request.json()
    base = str(body.get("base") or "")
    if not base:
        return JSONResponse({"error": "base обязателен"}, status_code=400)
    operations = body.get("operations") or []
    user_id = user.id
    assert user_id is not None  # вошедший сотрудник всегда с id из БД
    try:
        saved = _service(request).save(user_id, base, operations)
    except DutyClosed as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return {"report": saved}


@router.post("/api/send")
async def send_report(request: Request):
    """Отправить отчёт текущей смены. Тело: {base}. Ответ — {"report": {...}}."""
    user = _api_user(request)
    _require_doctor(user)
    body = await request.json()
    base = str(body.get("base") or "")
    if not base:
        return JSONResponse({"error": "base обязателен"}, status_code=400)
    user_id = user.id
    assert user_id is not None  # вошедший сотрудник всегда с id из БД
    try:
        sent = _service(request).send(user_id, base)
    except DutyClosed as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return {"report": sent}


# ── заведующий: доска и выгрузка ─────────────────────────────────────

@router.get("/api/board")
def board(request: Request):
    """Отчёты за диапазон дат (с именами врачей). Параметры from/to обязательны."""
    user = _api_user(request)
    _require_head(user)
    params = request.query_params
    from_date = params.get("from") or ""
    to_date = params.get("to") or ""
    if not from_date or not to_date:
        raise HTTPException(status_code=400, detail="Параметры from и to обязательны")
    reports = _service(request).board(from_date, to_date)
    employees = request.app.state.employees
    for report in reports:
        doctor = employees.get_by_id(report["doctor_id"])
        report["doctor_name"] = doctor.full_name if doctor else f"# {report['doctor_id']}"
    return {"reports": reports}


def _doctor_label(emp: Employee) -> str:
    """«Фамилия И.О.» — как имена врачей в шаблоне разлиновки."""
    initials = ""
    if emp.first_name:
        initials += emp.first_name[0] + "."
    if emp.middle_name:
        initials += emp.middle_name[0] + "."
    parts = [emp.last_name]
    if initials:
        parts.append(initials)
    return " ".join(parts)


def _group_for_export(reports: list[dict], employees) -> list[dict]:
    """Сгруппировать отчёты для build_xlsx: дата → база → врач → операции."""
    by_date: dict[str, dict] = {}
    for report in reports:
        day = by_date.setdefault(report["shift_date"], {"date": report["shift_date"], "bases": {}})
        base_entry = day["bases"].setdefault(report["base"], {"base": report["base"], "doctors": {}})
        emp = employees.get_by_id(report["doctor_id"])
        name = _doctor_label(emp) if emp else f"# {report['doctor_id']}"
        base_entry["doctors"][name] = {"name": name, "operations": report["operations"]}

    days: list[dict] = []
    for date in sorted(by_date):
        day = by_date[date]
        bases = []
        for base_name in BASES:
            entry = day["bases"].get(base_name)
            if entry is not None:
                entry["doctors"] = sorted(entry["doctors"].values(), key=lambda d: d["name"])
                bases.append(entry)
        day["bases"] = bases
        days.append(day)
    return days


@router.get("/report.xlsx")
def report_xlsx(request: Request):
    """Выгрузить разлиновку за диапазон дат (.xlsx, только заведующий).

    В отчёт попадают только закрытые (status == 'closed') отчёты.
    """
    user = access.current_user(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    _require_head(user)
    params = request.query_params
    from_date = params.get("from") or ""
    to_date = params.get("to") or ""
    if not from_date or not to_date:
        raise HTTPException(status_code=400, detail="Параметры from и to обязательны")
    reports = _service(request).board(from_date, to_date)
    reports = [r for r in reports if r["status"] == statuses.CLOSED]
    days = _group_for_export(reports, request.app.state.employees)
    filename = f"разлиновка-{from_date}-{to_date}.xlsx"
    return web.xlsx_response(build_xlsx(days), filename)


# ── заведующий: закрытие/переоткрытие ────────────────────────────────

@router.post("/api/close")
async def close_report(request: Request):
    """Закрыть один отчёт (только заведующий). Тело: {report_id}."""
    user = _api_user(request)
    _require_head(user)
    body = await request.json()
    try:
        report_id = int(body.get("report_id"))
    except (TypeError, ValueError):
        return JSONResponse({"error": "report_id обязателен"}, status_code=400)
    _service(request).close_report(report_id)
    return {"ok": True}


@router.post("/api/close-shift")
async def close_shift(request: Request):
    """Закрыть все отчёты за смену (только заведующий). Тело: {shift_date}."""
    user = _api_user(request)
    _require_head(user)
    body = await request.json()
    shift_date = str(body.get("shift_date") or "")
    if not shift_date:
        return JSONResponse({"error": "shift_date обязателен"}, status_code=400)
    _service(request).close_shift(shift_date)
    return {"ok": True}


@router.post("/api/reopen")
async def reopen_report(request: Request):
    """Переоткрыть закрытый отчёт (только заведующий). Тело: {report_id}."""
    user = _api_user(request)
    _require_head(user)
    body = await request.json()
    try:
        report_id = int(body.get("report_id"))
    except (TypeError, ValueError):
        return JSONResponse({"error": "report_id обязателен"}, status_code=400)
    _service(request).reopen_report(report_id)
    return {"ok": True}
