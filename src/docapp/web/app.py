"""FastAPI-приложение docapp: сборка модулей, вход, общие страницы, PWA.

Здесь осталось только то, что принадлежит приложению целиком: вход и выход,
страница «Мои данные», обработчики ошибок шва доступа, статика и подключение
модулей из реестра. Сами разделы (записи анестезий, «Чат», «Потребности»,
«Дежурства», «Распределение») живут в своих модулях и приходят из `docapp.modules`.
"""

from pathlib import Path
from typing import Annotated, Sequence

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from docapp.auth.auth import Authenticator, InvalidCredentials, PasswordChangeError
from docapp.config import db_path as default_db_path
from docapp.config import https_only, session_secret
from docapp.core import access
from docapp.core import registry, sections, web
from docapp.core.registry import Module
from docapp.core.sections import SectionsMiddleware
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
    # Выключение разделов (ADR-0030) стоит ВНУТРИ сессии: middleware, добавленная
    # раньше, оказывается глубже, поэтому к моменту проверки `request.session`
    # уже есть и сообщение «раздел выключен» доезжает до главной страницы.
    app.add_middleware(SectionsMiddleware)
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
    # Выключатель разделов: приложение берёт его из состояния, поэтому `core`
    # не импортирует модуль `admin` (обратной зависимости нет). Тесты с
    # подмножеством модулей панели не содержат — тогда выключенных нет.
    admin_container = app.state.containers.get("admin")
    app.state.section_switch = getattr(admin_container, "service", None)
    # Матрица «роль × раздел» (ADR-0033): тот же модуль, другое назначение.
    app.state.access_matrix = app.state.section_switch
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
        except InvalidCredentials as exc:
            return TEMPLATES.TemplateResponse(
                request,
                "login.html",
                {"error": str(exc)},
                status_code=401,
            )
        request.session["employee_id"] = employee.id
        return RedirectResponse("/", status_code=303)

    @app.post("/logout")
    def logout(request: Request):
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    def _me_page(
        request: Request, user: Employee, *, password_error: str | None = None
    ):
        """Страница «Мои данные»: одна сборка контекста для показа и ошибки."""
        switch = sections.switch_of(request)
        can_sign = access.has(user, access.SIGNATURES_OWN) and (
            switch is None or switch.is_enabled("signatures")
        )
        return TEMPLATES.TemplateResponse(
            request,
            "me.html",
            {
                "user": user,
                "flash": None,
                "password_error": password_error,
                "can_sign": can_sign,
            },
            status_code=400 if password_error else 200,
        )

    @app.get("/me", response_class=HTMLResponse)
    def my_data(request: Request, user: Annotated[Employee, Depends(current_user)]):
        """Мои данные: просмотр (ФИО, роль, логин, номер в бухгалтерии).

        Здесь же ссылка на свою подпись: пункта меню у модуля подписей нет, потому
        что это действие в кабинете, а не раздел (ADR-0029). Видимость ссылки —
        вопрос прав, поэтому решается таблицей, а не перечислением ролей в шаблоне.

        И смена своего пароля (ADR-0030): забытый сбрасывает заведующий из панели,
        а обычную смену человек делает сам, не дожидаясь его.
        """
        if user is None:
            return RedirectResponse("/login", status_code=303)
        return _me_page(request, user)

    @app.post("/me/password")
    def change_password(
        request: Request,
        current: Annotated[str, Form()] = "",
        new: Annotated[str, Form()] = "",
        repeat: Annotated[str, Form()] = "",
    ):
        """Сменить свой пароль: старый, новый и повтор проверяются на сервере."""
        user = access.current_user(request)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        try:
            request.app.state.authenticator.change_password(user, current, new, repeat)
        except PasswordChangeError as exc:
            return _me_page(request, user, password_error=str(exc))
        request.session["flash"] = "Пароль изменён"
        return RedirectResponse("/me", status_code=303)

    return app


def current_user(request: Request) -> Employee | None:
    """Текущий вошедший сотрудник (по сессии) или None.

    Обёртка над швом доступа (core.access): имя сохранено для
    `Depends(current_user)` в маршрутах приложения.
    """
    return access.current_user(request)
