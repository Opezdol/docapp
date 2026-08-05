"""FastAPI-приложение docapp: маршруты, сессии, PWA-интерфейс."""

from datetime import date
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from docapp.auth.auth import Authenticator, InvalidCredentials
from docapp.domain.employee import NURSE, Employee
from docapp.records.service import AnesthesiaService
from docapp.storage.sqlite_store import (
    SqliteActiveNurseStore,
    SqliteAnesthesiaStore,
    SqliteEmployeeStore,
)

BASE_DIR = Path(__file__).parent
TEMPLATES = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def create_app(db_path: str | Path, secret: str) -> FastAPI:
    """Собрать приложение с хранилищами на одном SQLite-файле."""
    app = FastAPI(title="docapp")
    app.add_middleware(SessionMiddleware, secret_key=secret, max_age=60 * 60 * 24 * 30)

    employees = SqliteEmployeeStore(db_path)
    anesthesia = SqliteAnesthesiaStore(db_path)
    active_nurse = SqliteActiveNurseStore(db_path)
    app.state.employees = employees
    app.state.anesthesia = anesthesia
    app.state.active_nurse = active_nurse
    app.state.authenticator = Authenticator(employees)
    app.state.service = AnesthesiaService(anesthesia, active_nurse)

    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request):
        if request.session.get("employee_id"):
            return RedirectResponse("/", status_code=303)
        return TEMPLATES.TemplateResponse(request, "login.html", {"error": None})

    @app.post("/login")
    def login_submit(request: Request, login: Annotated[str, Form()], password: Annotated[str, Form()]):
        try:
            employee = request.app.state.authenticator.authenticate(login, password)
        except InvalidCredentials:
            return TEMPLATES.TemplateResponse(
                request,
                "login.html",
                {"error": "Неверный логин или пароль"},
                status_code=401,
            )
        request.session["employee_id"] = employee.id
        return RedirectResponse("/", status_code=303)

    @app.post("/logout")
    def logout(request: Request):
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    @app.get("/me", response_class=HTMLResponse)
    def my_data(request: Request, user: Annotated[Employee, Depends(current_user)]):
        """Мои данные: просмотр (ФИО, роль, логин, номер в бухгалтерии)."""
        if user is None:
            return RedirectResponse("/login", status_code=303)
        return TEMPLATES.TemplateResponse(
            request, "me.html", {"user": user, "flash": None}
        )

    @app.get("/orders", response_class=HTMLResponse)
    def orders_stub(request: Request, user: Annotated[Employee, Depends(current_user)]):
        """Заглушка будущего подприложения «Приказы» (ADR-9)."""
        if user is None:
            return RedirectResponse("/login", status_code=303)
        return TEMPLATES.TemplateResponse(
            request,
            "stub.html",
            {"user": user, "flash": None, "stub_title": "Приказы"},
        )

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request, user: Annotated[Employee, Depends(current_user)]):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        state = request.app.state
        flash = request.session.pop("flash", None)

        # Медсестра видит только свои анестезии (только просмотр, ADR-5)
        if user.role == NURSE:
            records = state.service.list_for_nurse(user.id)
            doctor_by_id = {d.id: d for d in state.employees.list_all()}
            records_with_doctors = [(r, doctor_by_id.get(r.doctor_id)) for r in records]
            return TEMPLATES.TemplateResponse(
                request,
                "nurse.html",
                {
                    "user": user,
                    "records": records_with_doctors,
                    "flash": flash,
                },
            )

        nurses = state.employees.list_nurses()
        nurse_by_id = {n.id: n for n in nurses}
        active_nurse_id = state.active_nurse.get_active_nurse(user.id)
        records = state.service.list_mine(user.id)
        records_with_nurses = [
            (r, nurse_by_id.get(r.nurse_id)) for r in records
        ]
        return TEMPLATES.TemplateResponse(
            request,
            "index.html",
            {
                "user": user,
                "nurses": nurses,
                "active_nurse_id": active_nurse_id,
                "records": records_with_nurses,
                "flash": flash,
                "today": date.today(),
            },
        )

    @app.post("/nurse")
    def choose_nurse(request: Request, user: Annotated[Employee, Depends(current_user)], nurse_id: Annotated[int, Form()]):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        if user.role == NURSE:
            request.session["flash"] = "Выбор сестры доступен только врачам"
            return RedirectResponse("/", status_code=303)
        request.app.state.active_nurse.set_active_nurse(user.id, nurse_id)
        return RedirectResponse("/", status_code=303)

    @app.post("/anesthesia")
    def add_anesthesia(
        request: Request,
        user: Annotated[Employee, Depends(current_user)],
        procedure_date: Annotated[date, Form()],
        patient_name: Annotated[str, Form()],
        history_number: Annotated[str, Form()],
    ):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        try:
            request.app.state.service.create(
                user.id, procedure_date, patient_name.strip(), history_number.strip()
            )
        except ValueError as exc:
            request.session["flash"] = str(exc)
        return RedirectResponse("/", status_code=303)

    @app.get("/anesthesia/{anesthesia_id}/edit", response_class=HTMLResponse)
    def edit_page(request: Request, anesthesia_id: int, user: Annotated[Employee, Depends(current_user)]):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        state = request.app.state
        record = state.anesthesia.get_by_id(anesthesia_id)
        if record is None or record.doctor_id != user.id:
            request.session["flash"] = "Запись не найдена"
            return RedirectResponse("/", status_code=303)
        nurses = state.employees.list_nurses()
        return TEMPLATES.TemplateResponse(
            request,
            "edit.html",
            {"user": user, "record": record, "nurses": nurses, "flash": None},
        )

    @app.post("/anesthesia/{anesthesia_id}/update")
    def update_anesthesia(
        request: Request,
        anesthesia_id: int,
        user: Annotated[Employee, Depends(current_user)],
        procedure_date: Annotated[date, Form()],
        patient_name: Annotated[str, Form()],
        history_number: Annotated[str, Form()],
        nurse_id: Annotated[int, Form()],
    ):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        try:
            request.app.state.service.update(
                user.id,
                anesthesia_id,
                procedure_date,
                patient_name.strip(),
                history_number.strip(),
                nurse_id,
            )
        except ValueError as exc:
            request.session["flash"] = str(exc)
        return RedirectResponse("/", status_code=303)

    @app.post("/anesthesia/{anesthesia_id}/delete")
    def delete_anesthesia(request: Request, anesthesia_id: int, user: Annotated[Employee, Depends(current_user)]):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        try:
            request.app.state.service.delete(user.id, anesthesia_id)
        except ValueError as exc:
            request.session["flash"] = str(exc)
        return RedirectResponse("/", status_code=303)

    return app


def current_user(request: Request) -> Employee | None:
    """Текущий вошедший сотрудник (по сессии) или None."""
    employee_id = request.session.get("employee_id")
    if employee_id is None:
        return None
    return request.app.state.employees.get_by_id(employee_id)
