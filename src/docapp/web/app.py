"""FastAPI-приложение docapp: сборка модулей, вход, PWA-интерфейс.

Ядро (сотрудники, анестезии) живёт здесь; модули разделов берутся из реестра
(`docapp.modules.MODULES`) — их контейнеры и HTTP-адаптеры подключает
`core.registry`.
"""

from pathlib import Path
from typing import Annotated, Sequence

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from docapp.auth.auth import Authenticator, InvalidCredentials
from docapp.config import db_path as default_db_path
from docapp.config import https_only, session_secret
from docapp.core import access
from docapp.core import registry, web
from docapp.core.registry import Module
from docapp.domain.employee import Employee
from docapp.modules import MODULES
from docapp.records.service import AnesthesiaService
from docapp.storage.sqlite_store import (
    SqliteActiveNurseStore,
    SqliteAnesthesiaStore,
    SqliteEmployeeStore,
)

BASE_DIR = Path(__file__).parent

#: Шаблоны приложения: общие (base.html, login, index, me, edit, nurse).
TEMPLATES = web.templates()


def create_app(
    db_path: str | Path | None = None,
    secret: str = "",
    modules: Sequence[Module] | None = None,
) -> FastAPI:
    """Собрать приложение: ядро, модули из реестра, HTTP.

    `modules` — подмножество реестра: тесты собирают приложение с одним модулем
    вместо всех (не нужно патчить четыре переменные окружения).
    """
    db_path = default_db_path() if db_path is None else db_path
    secret = secret or session_secret()
    modules = MODULES if modules is None else modules
    app = FastAPI(title="docapp")
    app.add_middleware(
        SessionMiddleware,
        secret_key=secret,
        max_age=60 * 60 * 24 * 30,
        https_only=https_only(),
    )

    employees = SqliteEmployeeStore(db_path)
    anesthesia = SqliteAnesthesiaStore(db_path)
    active_nurse = SqliteActiveNurseStore(db_path)
    app.state.employees = employees
    app.state.anesthesia = anesthesia
    app.state.active_nurse = active_nurse
    app.state.authenticator = Authenticator(employees)
    app.state.service = AnesthesiaService(anesthesia)

    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

    @app.exception_handler(access.AccessDenied)
    def access_denied_handler(request: Request, exc: access.AccessDenied):
        """403: модули бросают исключение шва доступа, HTTP ставит приложение."""
        return JSONResponse({"detail": str(exc)}, status_code=403)

    @app.exception_handler(access.NotAuthenticated)
    def not_authenticated_handler(request: Request, exc: access.NotAuthenticated):
        """401: то же для запросов без сессии."""
        return JSONResponse({"detail": str(exc)}, status_code=401)

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

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request, user: Annotated[Employee, Depends(current_user)]):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        state = request.app.state
        flash = request.session.pop("flash", None)

        # Медсестра и старшая сестра видят только свои анестезии (просмотр,
        # ADR-5); право ввода — разрешение records.edit (ADR-0023).
        if not access.has(user.role, access.RECORDS_EDIT):
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
            },
        )

    @app.post("/nurse")
    def choose_nurse(request: Request, user: Annotated[Employee, Depends(current_user)], nurse_id: Annotated[int, Form()]):
        if user is None:
            return JSONResponse({"error": "Требуется авторизация"}, status_code=401)
        if not access.has(user.role, access.RECORDS_EDIT):
            return JSONResponse({"error": "Выбор сестры доступен только врачам"}, status_code=403)
        request.app.state.active_nurse.set_active_nurse(user.id, nurse_id)
        return {"ok": True, "nurse_id": nurse_id}

    @app.post("/anesthesia")
    def add_anesthesia(
        request: Request,
        user: Annotated[Employee, Depends(current_user)],
        patient_name: Annotated[str, Form()],
        nurse_id: Annotated[int, Form()] = 0,
    ):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        try:
            if not access.has(user.role, access.RECORDS_EDIT):
                # Формы у медсестры нет, но адрес открыт: отвечаем как раньше —
                # сообщением, а не страницей ошибки (поведение в тестах).
                request.session["flash"] = "Ввод анестезий доступен врачу"
                return RedirectResponse("/", status_code=303)
            request.app.state.service.create(user.id, nurse_id, patient_name.strip())
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
        patient_name: Annotated[str, Form()],
        nurse_id: Annotated[int, Form()] = 0,
    ):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        try:
            request.app.state.service.update(
                user.id,
                anesthesia_id,
                patient_name.strip(),
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

    # Модули разделов: из реестра (docapp.modules) — контейнеры и роутеры.
    # Ядро приложения (сотрудники, анестезии) остаётся здесь; вынести его в
    # отдельный модуль `records` — работа шагов 4–6 (docs/ТЗ-каркас.md).
    registry.build_containers(app, modules)
    registry.include_routers(app, modules)

    return app


def current_user(request: Request) -> Employee | None:
    """Текущий вошедший сотрудник (по сессии) или None.

    Обёртка над швом доступа (core.access): здесь имя сохранено для
    `Depends(current_user)` в маршрутах этого модуля.
    """
    return access.current_user(request)
