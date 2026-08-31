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
from docapp.domain.employee import HEAD_NURSE, NURSE, Employee
from docapp.records.service import AnesthesiaService
from docapp.storage.sqlite_store import (
    SqliteActiveNurseStore,
    SqliteAnesthesiaStore,
    SqliteEmployeeStore,
)

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
TEMPLATES = Jinja2Templates(
    directory=str(BASE_DIR / "templates"),
    context_processors=[lambda request: {"git_revision": git_revision()}],
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

        # Медсестра и старшая сестра видят только свои анестезии
        # (только просмотр, ADR-5; старшая сестра — тоже медсестра, ADR-11)
        if user.role in (NURSE, HEAD_NURSE):
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
        if user.role in (NURSE, HEAD_NURSE):
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

    # Подприложение «Компендиум»: LLM-wiki по курируемым .md-статьям
    # поверх PDF-источников. Инициализация дешёвая и без сети: LLMClient/
    # VisionClient только создают HTTP-транспорт, WikiService строит индекс
    # из пустой БД. Импорты локальные: router импортирует docapp.web.app
    # (current_user), поэтому на уровне модуля был бы круговой импорт.
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

    # Подприложение «Потребности» (задача T6 ТЗ-потребностей).
    # Инициализация дешёвая и без сети: чтение YAML-каталога и создание
    # SQLite-файла заявок (как «Компендиум» создаёт свою wiki.db).
    # Импорты локальные: needs.router импортирует docapp.web.app (current_user),
    # поэтому на уровне модуля был бы круговой импорт.
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

    return app


def current_user(request: Request) -> Employee | None:
    """Текущий вошедший сотрудник (по сессии) или None."""
    employee_id = request.session.get("employee_id")
    if employee_id is None:
        return None
    return request.app.state.employees.get_by_id(employee_id)
