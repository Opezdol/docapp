"""HTTP-адаптер модуля `records`: главная страница, ввод и правка анестезий.

Своих правил здесь нет: они в `records/service.py`, права — в `core/access`,
справочник сотрудников — у модуля `people`. Роутер только переводит HTTP в вызовы
сервиса и обратно (ADR-0017).

Поведение прежних маршрутов из `web/app.py` сохранено дословно: у страниц — редирект
на вход и сообщение через «flash», у выбора сестры — JSON с кодами 401/403.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from docapp.core import access, web
from docapp.core.registry import container_of
from docapp.domain.employee import Employee
from docapp.people.container import PeopleContainer
from docapp.records.container import RecordsContainer

#: Шаблоны модуля: свои (index/nurse/edit), затем общие (base.html).
TEMPLATES = web.templates(Path(__file__).parent / "templates")

router = APIRouter()


def _container(request: Request) -> RecordsContainer:
    """Контейнер этого модуля (типизированно)."""
    return container_of(request, "records", RecordsContainer)


def _user_id(user: Employee) -> int:
    """id вошедшего сотрудника: сессия хранит именно его, None тут невозможен."""
    if user.id is None:
        raise RuntimeError("У вошедшего сотрудника нет id")
    return user.id


def _people(request: Request) -> PeopleContainer:
    """Справочник сотрудников — через владельца, а не запросом к таблице (ADR-0017)."""
    return container_of(request, "people", PeopleContainer)


@router.get("/", response_class=HTMLResponse)
def index(request: Request):
    """Главная: врач вводит и видит свои записи, медсестра — только свои (ADR-5)."""
    user = access.current_user(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)

    container = _container(request)
    employees = _people(request).employees
    flash = request.session.pop("flash", None)

    # Медсестра и старшая сестра видят только свои анестезии (просмотр,
    # ADR-5); право ввода — разрешение records.edit (ADR-0023).
    if not access.has(user.role, access.RECORDS_EDIT):
        records = container.service.list_for_nurse(_user_id(user))
        doctor_by_id = {d.id: d for d in employees.list_all()}
        return TEMPLATES.TemplateResponse(
            request,
            "nurse.html",
            {
                "user": user,
                "records": [(r, doctor_by_id.get(r.doctor_id)) for r in records],
                "flash": flash,
            },
        )

    nurses = employees.list_nurses()
    nurse_by_id = {n.id: n for n in nurses}
    return TEMPLATES.TemplateResponse(
        request,
        "index.html",
        {
            "user": user,
            "nurses": nurses,
            "active_nurse_id": container.active_nurse.get_active_nurse(_user_id(user)),
            "records": [
                (r, nurse_by_id.get(r.nurse_id))
                for r in container.service.list_mine(_user_id(user))
            ],
            "flash": flash,
        },
    )


@router.post("/nurse")
def choose_nurse(
    request: Request,
    nurse_id: Annotated[int, Form()],
):
    """Запомнить выбранную сестру («активная сестра», ADR-4)."""
    user = access.current_user(request)
    if user is None:
        return JSONResponse({"error": "Требуется авторизация"}, status_code=401)
    if not access.has(user.role, access.RECORDS_EDIT):
        return JSONResponse({"error": "Выбор сестры доступен только врачам"}, status_code=403)
    _container(request).active_nurse.set_active_nurse(_user_id(user), nurse_id)
    return {"ok": True, "nurse_id": nurse_id}


@router.post("/anesthesia")
def add_anesthesia(
    request: Request,
    patient_name: Annotated[str, Form()],
    nurse_id: Annotated[int, Form()] = 0,
):
    """Добавить запись. Сообщение об ошибке возвращается «flash»-строкой."""
    user = access.current_user(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    try:
        if not access.has(user.role, access.RECORDS_EDIT):
            # Формы у медсестры нет, но адрес открыт: отвечаем как раньше —
            # сообщением, а не страницей ошибки (поведение в тестах).
            request.session["flash"] = "Ввод анестезий доступен врачу"
            return RedirectResponse("/", status_code=303)
        _container(request).service.create(_user_id(user), nurse_id, patient_name.strip())
    except ValueError as exc:
        request.session["flash"] = str(exc)
    return RedirectResponse("/", status_code=303)


@router.get("/anesthesia/{anesthesia_id}/edit", response_class=HTMLResponse)
def edit_page(request: Request, anesthesia_id: int):
    """Форма правки записи: только своя запись (ADR-5)."""
    user = access.current_user(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    container = _container(request)
    record = container.anesthesia.get_by_id(anesthesia_id)
    if record is None or record.doctor_id != _user_id(user):
        request.session["flash"] = "Запись не найдена"
        return RedirectResponse("/", status_code=303)
    return TEMPLATES.TemplateResponse(
        request,
        "edit.html",
        {
            "user": user,
            "record": record,
            "nurses": _people(request).employees.list_nurses(),
            "flash": None,
        },
    )


@router.post("/anesthesia/{anesthesia_id}/update")
def update_anesthesia(
    request: Request,
    anesthesia_id: int,
    patient_name: Annotated[str, Form()],
    nurse_id: Annotated[int, Form()] = 0,
):
    """Перезаписать свою запись."""
    user = access.current_user(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    try:
        _container(request).service.update(
            _user_id(user), anesthesia_id, patient_name.strip(), nurse_id
        )
    except ValueError as exc:
        request.session["flash"] = str(exc)
    return RedirectResponse("/", status_code=303)


@router.post("/anesthesia/{anesthesia_id}/delete")
def delete_anesthesia(request: Request, anesthesia_id: int):
    """Удалить свою запись."""
    user = access.current_user(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    try:
        _container(request).service.delete(_user_id(user), anesthesia_id)
    except ValueError as exc:
        request.session["flash"] = str(exc)
    return RedirectResponse("/", status_code=303)
