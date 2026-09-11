"""FastAPI-приложение docapp: сборка модулей, вход, общие страницы, PWA.

Здесь осталось только то, что принадлежит приложению целиком: вход и выход,
страница «Мои данные», обработчики ошибок шва доступа, статика и подключение
модулей из реестра. Сами разделы (записи анестезий, «Компендиум», «Потребности»,
«Дежурства», «Сводка») живут в своих модулях и приходят из `docapp.modules`.
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
from docapp.people.store import SqliteEmployeeStore

BASE_DIR = Path(__file__).parent

#: Шаблоны приложения: только общие (base.html, login, me, stub).
TEMPLATES = web.templates()


def create_app(
    db_path: str | Path | None = None,
    secret: str = "",
    modules: Sequence[Module] | None = None,
) -> FastAPI:
    """Собрать приложение: ядро, модули из реестра, HTTP.

    `modules` — подмножество реестра: тесты собирают приложение с одним модулем
    вместо всех (не нужно патчить переменные окружения всех модулей).
    """
    db_path = default_db_path() if db_path is None else db_path
    secret = secret or session_secret()
    modules = MODULES if modules is None else modules
    db_file = Path(db_path)
    db_file.parent.mkdir(parents=True, exist_ok=True)

    app = FastAPI(title="docapp")
    app.add_middleware(
        SessionMiddleware,
        secret_key=secret,
        max_age=60 * 60 * 24 * 30,
        https_only=https_only(),
    )
    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

    # Шов доступа и вход нужны приложению всегда — даже когда модуль сотрудников
    # не подключён (тесты собирают подмножество модулей). Сам класс хранилища
    # принадлежит владельцу данных — модулю `people`.
    employees = SqliteEmployeeStore(db_file)
    app.state.employees = employees
    app.state.authenticator = Authenticator(employees)

    # Модули: контейнеры собираются в порядке реестра, поэтому модуль может взять
    # интерфейс соседа, объявленного раньше (ADR-0017).
    registry.build_containers(app, modules, registry.AppContext(db_path=db_file))
    registry.include_routers(app, modules)

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
    def login_submit(
        request: Request,
        login: Annotated[str, Form()],
        password: Annotated[str, Form()],
    ):
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

    return app


def current_user(request: Request) -> Employee | None:
    """Текущий вошедший сотрудник (по сессии) или None.

    Обёртка над швом доступа (core.access): имя сохранено для
    `Depends(current_user)` в маршрутах приложения.
    """
    return access.current_user(request)
