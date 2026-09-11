"""Шов доступа: кто вошёл и что ему можно (ADR-0017, ADR-0023).

Единственный источник правды о правах — таблица `ALLOWED` ниже. Её читают:

- роутеры модулей: `access.require(request, access.NEEDS_MANAGE)` для API и
  `access.ensure(user, ...)` там, где пользователь уже получен для страницы;
- меню: `menu_for(role)` — раздел виден, если у роли есть хотя бы одно
  разрешение своего модуля;
- тесты: таблица проверяется целиком, без HTTP.

Разрешение называется `<модуль>.<действие>`. Разрешений два вида: «что видно»
(`view_own` / `view_all`) и «что можно делать» (`edit`, `manage`, `catalog`,
`curate`, `settings`).

Модули больше не импортируют `docapp.web.app`: пользователя и проверку прав даёт
этот модуль, поэтому роутеры подключаются в `create_app` обычным
`include_router` (см. ADR-0017 — он пересматривает ленивые импорты ADR-9).

Ошибки — исключения `AccessDenied` (403) и `NotAuthenticated` (401); HTTP-коды
проставляются обработчиками в `create_app`, чтобы в модулях не было HTTP.
"""

from __future__ import annotations

from dataclasses import dataclass

from starlette.requests import Request

from docapp.domain.employee import DOCTOR, EDITOR, HEAD, HEAD_NURSE, NURSE, Employee

# ── разрешения: «<модуль>.<действие>» ────────────────────────────────

#: Анестезии: свои записи (просмотр) и ввод/правка своих записей.
RECORDS_VIEW_OWN = "records.view_own"
RECORDS_EDIT = "records.edit"

#: «Распределение»: раздел заведующего — счёт по всем записям отделения и,
#: далее, разноска ведомости (ADR-0024). Раздел целиком закрыт для остальных
#: ролей, поэтому разрешение одно.
DISTRIBUTION_MANAGE = "distribution.manage"

#: «Чат»: чтение статей и ответы консультанта. Выдано заведующему и редактору —
#: раздел закрыт для остальных до доработки (12.09.2026).
WIKI_READ = "wiki.read"
#: «Чат»: источники и статьи (заведующий, редактор).
WIKI_CURATE = "wiki.curate"
#: «Чат»: настройки консультанта (только заведующий).
WIKI_SETTINGS = "wiki.settings"

#: «Потребности»: своя заявка точки пополнения.
NEEDS_VIEW_OWN = "needs.view_own"
#: «Потребности»: все точки, доска, отчёты и аналитика.
NEEDS_VIEW_ALL = "needs.view_all"
NEEDS_EDIT_OWN = "needs.edit_own"
#: «Потребности»: чужие заявки, закрытие и переоткрытие недель.
NEEDS_MANAGE = "needs.manage"
#: «Потребности»: правка каталога расходки (ADR-0019).
NEEDS_CATALOG = "needs.catalog"

#: «Дежурства»: свой отчёт за смену.
DUTY_VIEW_OWN = "duty.view_own"
DUTY_EDIT_OWN = "duty.edit_own"
#: «Дежурства»: все отчёты, закрытие смены, выгрузка разлиновки.
DUTY_VIEW_ALL = "duty.view_all"
DUTY_MANAGE = "duty.manage"

#: Таблица прав. Роль → разрешения (ADR-0023).
#:
#: `editor` — это врач с правом курирования справочных документов: права врача
#: плюс `wiki.curate`. `head` видит все анестезии в разделе «Распределение»
#: (`distribution.manage`), а на странице анестезий — свои, как все (ADR-0023, Q20).
ALLOWED: dict[str, frozenset[str]] = {
    DOCTOR: frozenset(
        {
            RECORDS_VIEW_OWN,
            RECORDS_EDIT,
            DUTY_VIEW_OWN,
            DUTY_EDIT_OWN,
        }
    ),
    NURSE: frozenset(
        {
            RECORDS_VIEW_OWN,
            NEEDS_VIEW_OWN,
            NEEDS_EDIT_OWN,
        }
    ),
    HEAD_NURSE: frozenset(
        {
            RECORDS_VIEW_OWN,
            NEEDS_VIEW_OWN,
            NEEDS_EDIT_OWN,
            NEEDS_MANAGE,
            NEEDS_CATALOG,
        }
    ),
    HEAD: frozenset(
        {
            RECORDS_VIEW_OWN,
            RECORDS_EDIT,
            DISTRIBUTION_MANAGE,
            WIKI_READ,
            WIKI_CURATE,
            WIKI_SETTINGS,
            NEEDS_VIEW_ALL,
            NEEDS_MANAGE,
            NEEDS_CATALOG,
            DUTY_VIEW_ALL,
            DUTY_MANAGE,
        }
    ),
    EDITOR: frozenset(
        {
            RECORDS_VIEW_OWN,
            RECORDS_EDIT,
            WIKI_READ,
            WIKI_CURATE,
            DUTY_VIEW_OWN,
            DUTY_EDIT_OWN,
        }
    ),
}


# ── меню ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MenuItem:
    """Пункт меню: путь, подпись и модуль, который за ним стоит."""

    path: str
    label: str
    module: str


#: Меню разделов. Пункт виден, если у роли есть разрешение его модуля.
#: Новый модуль добавляет здесь одну строку (и объявляет разрешения выше).
MENU: tuple[MenuItem, ...] = (
    MenuItem("/", "Анестезии", "records"),
    # Раздел «Чат» (модуль `wiki`, адрес `/wiki`): до доработки виден
    # только заведующему и редактору — у врача и старшей сестры права
    # `wiki.read` нет вовсе, поэтому и пункта меню у них нет.
    MenuItem("/wiki", "Чат", "wiki"),
    MenuItem("/needs", "Потребности", "needs"),
    MenuItem("/duty", "Дежурства", "duty"),
    # «Распределение» — раздел заведующего: у него своё разрешение
    # (`distribution.manage`, ADR-0024), права на записи тут ни при чём.
    MenuItem("/distribution", "Распределение", "distribution"),
)


# ── ошибки ───────────────────────────────────────────────────────────


class AccessDenied(ValueError):
    """Недостаточно прав (роутер отвечает 403)."""


class NotAuthenticated(ValueError):
    """Сессии нет (роутер отвечает 401)."""


# ── проверка прав ────────────────────────────────────────────────────


def permissions(role: str) -> frozenset[str]:
    """Разрешения роли; неизвестная роль — пустое множество."""
    return ALLOWED.get(role, frozenset())


def has(role: str, *permissions_: str) -> bool:
    """Есть ли у роли хотя бы одно из разрешений (правило «или»)."""
    granted = permissions(role)
    return any(permission in granted for permission in permissions_)


def has_module(role: str, module: str) -> bool:
    """Есть ли у роли хоть какое-то разрешение модуля (для меню)."""
    prefix = f"{module}."
    return any(permission.startswith(prefix) for permission in permissions(role))


def menu_for(role: str) -> list[MenuItem]:
    """Пункты меню, доступные роли."""
    return [item for item in MENU if has_module(role, item.module)]


# ── пользователь и проверки в роутерах ───────────────────────────────


def current_user(request: Request) -> Employee | None:
    """Текущий вошедший сотрудник (по сессии) или None."""
    employee_id = request.session.get("employee_id")
    if employee_id is None:
        return None
    return request.app.state.employees.get_by_id(employee_id)


def ensure(user: Employee, *permissions_: str, message: str = "Недостаточно прав") -> Employee:
    """Проверить права уже полученного пользователя; иначе AccessDenied."""
    if not has(user.role, *permissions_):
        raise AccessDenied(message)
    return user


def require(request: Request, *permissions_: str) -> Employee:
    """Пользователь с нужными правами: 401 без сессии, 403 без прав.

    Для страниц, которым нужен редирект на вход, используйте `current_user` —
    у страниц поведение «нет сессии → /login» сохраняется.
    """
    user = current_user(request)
    if user is None:
        raise NotAuthenticated("Требуется авторизация")
    return ensure(user, *permissions_, message="Доступ к этому разделу запрещён")


def api_user(request: Request) -> Employee:
    """Пользователь без проверки прав (для общих обработчиков /logout, /me)."""
    user = current_user(request)
    if user is None:
        raise NotAuthenticated("Требуется авторизация")
    return user
