"""FastAPI-приложение docapp: маршруты, сессии, PWA-интерфейс."""

import logging
import threading
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from docapp.auth.auth import Authenticator, InvalidCredentials
from docapp.config import git_revision, https_only
from docapp.core import access
from docapp.domain.employee import Employee
from docapp.records.service import AnesthesiaService
from docapp.storage.sqlite_store import (
    SqliteActiveNurseStore,
    SqliteAnesthesiaStore,
    SqliteEmployeeStore,
)

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent


def _menu_context(request: Request) -> dict:
    """Меню разделов для шаблона: из таблицы прав, второго списка не существует."""
    user = access.current_user(request)
    return {"menu": access.menu_for(user.role) if user else []}


TEMPLATES = Jinja2Templates(
    directory=str(BASE_DIR / "templates"),
    context_processors=[
        lambda request: {"git_revision": git_revision()},
        _menu_context,
    ],
)


def create_app(db_path: str | Path, secret: str) -> FastAPI:
    """Собрать приложение с хранилищами на одном SQLite-файле."""
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

    # Модуль «Компендиум»: LLM-wiki по курируемым .md-статьям поверх
    # PDF-источников. Инициализация дешёвая и без сети: LLMClient/
    # VisionClient только создают HTTP-транспорт, WikiService строит индекс
    # из пустой БД. Импорт модуля — по месту: список модулей станет реестром
    # на шаге 3 (docs/ТЗ-каркас.md), до тех пор подключение живёт здесь.
    from docapp.ai.config import load_ai_config
    from docapp.ai.llm import LLMClient
    from docapp.ai.vision import VisionClient
    from docapp.wiki.config import load_wiki_config
    from docapp.wiki.router import router as compendium_router
    from docapp.wiki.service import WikiService
    from docapp.wiki.store import SqliteWikiStore

    ai_config = load_ai_config()
    if not ai_config.api_key:
        logger.warning(
            "AI_API_KEY не задан: «Компендиум» будет возвращать ошибки "
            "до его настройки в .env"
        )
    wiki_config = load_wiki_config()
    wiki_config.db_path.parent.mkdir(parents=True, exist_ok=True)
    wiki_llm = LLMClient(ai_config)
    wiki_vision = VisionClient(ai_config)
    app.state.compendium = {
        "config": wiki_config,
        "ai_config": ai_config,
        "llm": wiki_llm,
        "vision": wiki_vision,
        "service": WikiService(SqliteWikiStore(wiki_config.db_path), wiki_llm, wiki_vision),
    }
    app.include_router(compendium_router)

    # Модуль «Потребности» (ТЗ-потребности, T6). Инициализация дешёвая и без
    # сети: чтение YAML-каталога и создание SQLite-файла заявок. Доступ — через
    # core.access, поэтому импорт модуля больше не грозит круговым импортом.
    from docapp.needs.catalog import Catalog
    from docapp.needs.config import load_needs_config
    from docapp.needs.router import router as needs_router
    from docapp.needs.service import NeedsService
    from docapp.needs.store import SqliteNeedsStore

    needs_config = load_needs_config()
    needs_config.db_path.parent.mkdir(parents=True, exist_ok=True)
    needs_store = SqliteNeedsStore(needs_config.db_path)
    needs_catalog = Catalog(needs_config.catalog_path)
    app.state.needs = {
        "config": needs_config,
        "catalog": needs_catalog,
        "store": needs_store,
        "service": NeedsService(needs_store, needs_catalog),
    }
    app.include_router(needs_router)

    # Модуль «Дежурства»: разлиновка дежурных бригад за ночные смены.
    # Инициализация дешёвая и без сети: своя SQLite-БД отчётов. Доступ — через
    # core.access, кругового импорта с web.app больше нет.
    from docapp.duty.config import load_duty_config
    from docapp.duty.router import router as duty_router
    from docapp.duty.service import DutyService
    from docapp.duty.store import SqliteDutyStore

    duty_config = load_duty_config()
    duty_config.db_path.parent.mkdir(parents=True, exist_ok=True)
    duty_store = SqliteDutyStore(duty_config.db_path)
    app.state.duty = {
        "config": duty_config,
        "store": duty_store,
        "service": DutyService(duty_store, duty_config),
    }
    app.include_router(duty_router)

    return app


def current_user(request: Request) -> Employee | None:
    """Текущий вошедший сотрудник (по сессии) или None.

    Обёртка над швом доступа (core.access): здесь имя сохранено для
    `Depends(current_user)` в маршрутах этого модуля.
    """
    return access.current_user(request)
