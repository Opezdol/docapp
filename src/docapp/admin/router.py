"""HTTP-адаптер панели заведующего: сотрудники (ADR-0030).

Правил здесь нет: они в `service.py`, права — в `core/access`, шаблоны — рядом.
Роутер читает форму, зовёт службу и отдаёт страницу.

Пароль, сгенерированный панелью, показывается ОДИН раз: он попадает только
в ответ на создание или сброс. В сессию (подписанная кука) и в логи он не
пишется — кука читается клиентом, пароль там хранить нельзя.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from docapp.admin.container import AdminContainer
from docapp.admin.service import ROLE_LABELS, AdminService, PanelError
from docapp.core import access, web
from docapp.core.registry import container_of
from docapp.domain.employee import ROLES, Employee

router = APIRouter(prefix="/admin")
TEMPLATES = web.templates(Path(__file__).parent / "templates")


def _service(request: Request) -> AdminService:
    """Служба панели из контейнера модуля (шов реестра, ADR-0017)."""
    return container_of(request, "admin", AdminContainer).service


def _page_user(request: Request) -> Employee | None:
    """Пользователь страницы: нет сессии — None (редирект), нет права — 403."""
    user = access.current_user(request)
    if user is None:
        return None
    access.ensure(user, access.ADMIN_MANAGE)
    return user


def _common_context(request: Request, user: Employee) -> dict:
    """То, что нужно обеим страницам панели."""
    return {
        "user": user,
        "roles": [(role, ROLE_LABELS[role]) for role in ROLES],
        "role_labels": ROLE_LABELS,
        "flash": request.session.pop("flash", None),
    }


def _render_staff(
    request: Request,
    user: Employee,
    *,
    error: str | None = None,
    new_login: str | None = None,
    new_password: str | None = None,
    form: dict | None = None,
    status_code: int = 200,
):
    """Страница панели: вкладка «Сотрудники» или «Разделы»."""
    show_dismissed = request.query_params.get("all") == "1"
    tab = request.query_params.get("tab", "staff")
    tab = tab if tab in {"staff", "sections"} else "staff"
    service = _service(request)
    context = _common_context(request, user)
    context.update(
        {
            "tab": tab,
            "staff": service.staff(show_dismissed=show_dismissed),
            "sections": service.sections(),
            "show_dismissed": show_dismissed,
            "error": error,
            "new_login": new_login,
            "new_password": new_password,
            "form": form or {},
        }
    )
    return TEMPLATES.TemplateResponse(
        request, "admin.html", context, status_code=status_code
    )


def _render_employee(
    request: Request,
    user: Employee,
    employee: Employee,
    *,
    error: str | None = None,
    new_password: str | None = None,
    form: dict | None = None,
    status_code: int = 200,
):
    """Карточка сотрудника: правка, сброс пароля, увольнение."""
    context = _common_context(request, user)
    context.update(
        {
            "employee": employee,
            "error": error,
            "new_password": new_password,
            "form": form or {},
        }
    )
    return TEMPLATES.TemplateResponse(
        request, "employee.html", context, status_code=status_code
    )


# ── список и создание ────────────────────────────────────────────────


@router.get("", response_class=HTMLResponse)
def page(request: Request):
    """Панель: список сотрудников и форма добавления."""
    user = _page_user(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    return _render_staff(request, user)


@router.post("/employee")
def create_employee(
    request: Request,
    last_name: Annotated[str, Form()] = "",
    first_name: Annotated[str, Form()] = "",
    middle_name: Annotated[str, Form()] = "",
    role: Annotated[str, Form()] = "",
    login: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
):
    """Завести сотрудника; сгенерированный пароль показывается один раз."""
    user = _page_user(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    form = {
        "last_name": last_name,
        "first_name": first_name,
        "middle_name": middle_name,
        "role": role,
        "login": login,
    }
    try:
        saved, generated = _service(request).create(
            last_name=last_name,
            first_name=first_name,
            middle_name=middle_name,
            role=role,
            login=login,
            password=password,
        )
    except PanelError as exc:
        return _render_staff(request, user, error=str(exc), form=form, status_code=400)

    request.session["flash"] = f"Сотрудник добавлен: {saved.full_name}"
    if generated:
        # Пароль нельзя отдать редиректом: в сессии ему не место.
        return _render_staff(
            request, user, new_login=saved.login, new_password=generated
        )
    return RedirectResponse("/admin", status_code=303)


# ── карточка сотрудника ──────────────────────────────────────────────


@router.get("/employee/{employee_id}", response_class=HTMLResponse)
def employee_page(request: Request, employee_id: int):
    """Карточка сотрудника."""
    user = _page_user(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    try:
        employee = _service(request).get(employee_id)
    except PanelError as exc:
        request.session["flash"] = str(exc)
        return RedirectResponse("/admin", status_code=303)
    return _render_employee(request, user, employee)


@router.post("/employee/{employee_id}")
def update_employee(
    request: Request,
    employee_id: int,
    last_name: Annotated[str, Form()] = "",
    first_name: Annotated[str, Form()] = "",
    middle_name: Annotated[str, Form()] = "",
    role: Annotated[str, Form()] = "",
    login: Annotated[str, Form()] = "",
    buh_id: Annotated[str, Form()] = "",
):
    """Сохранить карточку; выданная сейчас учётка даёт пароль один раз."""
    user = _page_user(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    form = {
        "last_name": last_name,
        "first_name": first_name,
        "middle_name": middle_name,
        "role": role,
        "login": login,
        "buh_id": buh_id,
    }
    service = _service(request)
    try:
        saved, generated = service.update(
            actor=user,
            employee_id=employee_id,
            last_name=last_name,
            first_name=first_name,
            middle_name=middle_name,
            role=role,
            login=login,
            buh_id=buh_id,
        )
    except PanelError as exc:
        try:
            employee = service.get(employee_id)
        except PanelError:
            request.session["flash"] = str(exc)
            return RedirectResponse("/admin", status_code=303)
        return _render_employee(
            request, user, employee, error=str(exc), form=form, status_code=400
        )

    if generated:
        return _render_employee(request, user, saved, new_password=generated)
    request.session["flash"] = "Изменения сохранены"
    return RedirectResponse("/admin", status_code=303)


@router.post("/employee/{employee_id}/password")
def reset_password(request: Request, employee_id: int):
    """Выдать новый пароль: старый перестаёт работать сразу."""
    user = _page_user(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    service = _service(request)
    try:
        password = service.reset_password(employee_id)
    except PanelError as exc:
        try:
            employee = service.get(employee_id)
        except PanelError:
            request.session["flash"] = str(exc)
            return RedirectResponse("/admin", status_code=303)
        return _render_employee(request, user, employee, error=str(exc), status_code=400)
    return _render_employee(
        request, user, service.get(employee_id), new_password=password
    )


@router.post("/section/{module}")
def switch_section(
    request: Request,
    module: str,
    enabled: Annotated[int, Form()] = 1,
):
    """Включить или выключить раздел (ADR-0030).

    Сервисные разделы (панель, учётки, анестезии) выключать нельзя: служба
    откажет текстом, а не переключит их молча.
    """
    user = _page_user(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    try:
        label = _service(request).set_section(
            actor=user, module=module, enabled=bool(enabled)
        )
    except PanelError as exc:
        request.session["flash"] = str(exc)
        return RedirectResponse("/admin?tab=sections", status_code=303)
    request.session["flash"] = (
        f"Раздел «{label}» включён" if enabled else f"Раздел «{label}» выключен"
    )
    return RedirectResponse("/admin?tab=sections", status_code=303)


@router.post("/employee/{employee_id}/active")
def set_active(
    request: Request,
    employee_id: int,
    active: Annotated[int, Form()] = 1,
):
    """Уволить (active=0) или вернуть (active=1) сотрудника."""
    user = _page_user(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    service = _service(request)
    try:
        if active:
            target = service.restore(employee_id)
            request.session["flash"] = f"Сотрудник возвращён: {target.full_name}"
        else:
            target = service.dismiss(actor=user, employee_id=employee_id)
            request.session["flash"] = f"Сотрудник уволен: {target.full_name}"
    except PanelError as exc:
        request.session["flash"] = str(exc)
    return RedirectResponse("/admin", status_code=303)
