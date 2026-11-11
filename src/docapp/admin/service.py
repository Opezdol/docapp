"""Правила панели заведующего: сотрудники (ADR-0030).

Здесь проверки, которые нельзя размазывать по роутеру и шаблону: «нельзя
уволить себя», «логин занят», «нельзя стереть учётную запись пустым полем».
Их видно в тестах без HTTP.

Отдельной проверки «не уволить последнего заведующего» нет: панель доступна
только заведующему, и уволить он может лишь другого, то есть сам остаётся
в системе. Инвариант держится запретом на себя — и это проверено тестом.

Сотрудники принадлежат модулю `people` (ADR-0017): панель объявляет интерфейс,
который ей нужен, и правит их через него, а не запросом к чужой таблице.
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from typing import Protocol

from docapp.auth.passwords import MIN_PASSWORD, generate_password, hash_password
from docapp.core import access
from docapp.domain.employee import ROLES, Employee

#: Подписи ролей для интерфейса — одно место, чтобы не разъезжались.
ROLE_LABELS: dict[str, str] = {
    "doctor": "Врач",
    "nurse": "Медсестра",
    "head_nurse": "Старшая медсестра",
    "head": "Заведующий",
    "editor": "Редактор",
}

#: Модули, которые обеспечивают работу приложения и выключаться не могут:
#: сама панель, учётки (вход и внешние ключи) и владелец главной страницы.
SERVICE_MODULES = frozenset({"admin", "people", "records"})

#: Раздел без пункта меню: у подписей вход из личного кабинета (ADR-0029),
#: но выключить их нужно — ссылка в кабинете тоже должна исчезнуть.
EXTRA_SECTIONS: dict[str, tuple[str, str]] = {
    "signatures": ("/signatures", "Подписи"),
}


def _switchable() -> tuple[tuple[str, str, str], ...]:
    """(модуль, адрес, подпись) каждого выключаемого раздела.

    Считается из `MENU`: второй список разделов разошёлся бы с меню, как
    когда-то разошёлся ручной список таблиц в `docapp check`.
    """
    items: list[tuple[str, str, str]] = [
        (item.module, item.path, item.label)
        for item in access.MENU
        if item.module not in SERVICE_MODULES
    ]
    known = {module for module, _, _ in items}
    for module, (path, label) in EXTRA_SECTIONS.items():
        if module not in SERVICE_MODULES and module not in known:
            items.append((module, path, label))
    return tuple(items)


#: Выключаемые разделы в порядке меню, затем те, у кого пункта меню нет.
SWITCHABLE = _switchable()


class PanelError(ValueError):
    """Ошибка панели: человеку показывается текстом, а не 500."""


class SectionsInterface(Protocol):
    """Что панели нужно от хранилища состояния разделов."""

    def is_enabled(self, module: str) -> bool: ...

    def state(self) -> dict[str, tuple[bool, str, int | None]]: ...

    def set_enabled(self, module: str, enabled: bool, changed_by: int | None) -> None: ...


class EmployeesInterface(Protocol):
    """Что панели нужно от владельца сотрудников — и ничего сверх этого."""

    def get_by_id(self, employee_id: int) -> Employee | None: ...

    def get_by_login(self, login: str) -> Employee | None: ...

    def list_all(self) -> list[Employee]: ...

    def add(self, employee: Employee) -> Employee: ...

    def update(self, employee: Employee) -> None: ...

    def set_active(self, employee_id: int, active: bool) -> None: ...

    def set_password(self, employee_id: int, password_hash: str) -> None: ...


class AdminService:
    """Сотрудники в панели заведующего."""

    def __init__(
        self, employees: EmployeesInterface, sections: SectionsInterface
    ) -> None:
        self._employees = employees
        self._sections = sections

    # ── чтение ──────────────────────────────────────────────────────

    def staff(self, *, show_dismissed: bool = False) -> list[Employee]:
        """Сотрудники для списка: по умолчанию только работающие."""
        people = self._employees.list_all()
        if show_dismissed:
            return people
        return [person for person in people if person.active]

    def get(self, employee_id: int) -> Employee:
        employee = self._employees.get_by_id(employee_id)
        if employee is None:
            raise PanelError("Сотрудник не найден")
        return employee

    # ── создание и правка ───────────────────────────────────────────

    def create(
        self,
        *,
        last_name: str,
        first_name: str,
        middle_name: str,
        role: str,
        login: str,
        password: str,
    ) -> tuple[Employee, str | None]:
        """Завести сотрудника; вернуть его и сгенерированный пароль, если был."""
        last_name = last_name.strip()
        first_name = first_name.strip()
        login = login.strip()
        password = password.strip()
        if not last_name or not first_name:
            raise PanelError("Фамилия и имя обязательны")
        if role not in ROLES:
            raise PanelError("Неизвестная роль")
        if not login and password:
            raise PanelError(
                "Пароль без логина не нужен: у сотрудника не будет учётной записи"
            )
        generated: str | None = None
        if login:
            self._ensure_login_free(login, employee_id=None)
            if not password:
                password = generate_password()
                generated = password
            elif len(password) < MIN_PASSWORD:
                raise PanelError(f"Пароль короче {MIN_PASSWORD} символов")
        try:
            saved = self._employees.add(
                Employee(
                    last_name=last_name,
                    first_name=first_name,
                    middle_name=middle_name.strip(),
                    role=role,
                    login=login or None,
                    password_hash=hash_password(password) if login else None,
                )
            )
        except sqlite3.IntegrityError as exc:
            raise PanelError("Логин уже занят") from exc
        return saved, generated

    def update(
        self,
        *,
        actor: Employee,
        employee_id: int,
        last_name: str,
        first_name: str,
        middle_name: str,
        role: str,
        login: str,
        buh_id: str,
    ) -> tuple[Employee, str | None]:
        """Переписать карточку; вернуть её и новый пароль, если учётку выдали сейчас.

        Логин существующей учётной записи нельзя стереть: для «человек больше
        не работает» есть увольнение, молчаливая потеря доступа — не то, что
        должно происходить от пустого поля.
        """
        target = self.get(employee_id)
        last_name = last_name.strip()
        first_name = first_name.strip()
        login = login.strip()
        buh_id = buh_id.strip()
        if not last_name or not first_name:
            raise PanelError("Фамилия и имя обязательны")
        if role not in ROLES:
            raise PanelError("Неизвестная роль")
        if actor.id == employee_id and role != target.role:
            raise PanelError("Нельзя менять роль самому себе: панель станет недоступна")
        if target.login and not login:
            raise PanelError(
                "У сотрудника есть учётная запись — логин нельзя оставить пустым"
            )
        if login and login != target.login:
            self._ensure_login_free(login, employee_id=employee_id)

        password_hash = target.password_hash
        generated: str | None = None
        if login and not password_hash:
            # Впервые выдаём учётку существующему сотруднику: логин без пароля
            # — это «половина учётной записи», в которую нельзя войти.
            generated = generate_password()
            password_hash = hash_password(generated)

        updated = replace(
            target,
            last_name=last_name,
            first_name=first_name,
            middle_name=middle_name.strip(),
            role=role,
            login=login or None,
            password_hash=password_hash if login else None,
            buh_id=buh_id or None,
        )
        try:
            self._employees.update(updated)
        except sqlite3.IntegrityError as exc:
            raise PanelError("Логин уже занят") from exc
        return self.get(employee_id), generated

    # ── увольнение и пароли ─────────────────────────────────────────

    def dismiss(self, *, actor: Employee, employee_id: int) -> Employee:
        """Уволить сотрудника: строка остаётся, доступ закрывается."""
        target = self.get(employee_id)
        if actor.id == employee_id:
            raise PanelError("Нельзя уволить самого себя")
        self._employees.set_active(employee_id, False)
        return target

    def restore(self, employee_id: int) -> Employee:
        """Вернуть уволенного: вход снова работает с прежним паролем."""
        target = self.get(employee_id)
        self._employees.set_active(employee_id, True)
        return target

    def reset_password(self, employee_id: int) -> str:
        """Выдать новый пароль; старый перестаёт работать сразу."""
        employee = self.get(employee_id)
        if not employee.login:
            raise PanelError("У сотрудника нет учётной записи — сначала задайте логин")
        password = generate_password()
        self._employees.set_password(employee_id, hash_password(password))
        return password

    # ── разделы ─────────────────────────────────────────────────────

    def sections(self) -> list[dict]:
        """Разделы с их состоянием — для вкладки «Разделы»."""
        state = self._sections.state()
        result: list[dict] = []
        for module, path, label in SWITCHABLE:
            enabled, changed_at, _ = state.get(module, (True, "", None))
            result.append(
                {
                    "module": module,
                    "path": path,
                    "label": label,
                    "enabled": enabled,
                    "changed_at": changed_at,
                }
            )
        return result

    def is_enabled(self, module: str) -> bool:
        """Раздел включён? Сервисные и невыключаемые — всегда да."""
        if module not in {name for name, _, _ in SWITCHABLE}:
            return True
        return self._sections.is_enabled(module)

    def module_for_path(self, path: str) -> str | None:
        """Модуль, которому принадлежит адрес: точный путь или его подпапка.

        Сравнение по границе `/`, а не по сырому префиксу: `/needsomething`
        не должен закрываться разделом «Потребности».
        """
        best: tuple[str, str] | None = None
        for module, prefix, _ in SWITCHABLE:
            if path == prefix or path.startswith(prefix + "/"):
                if best is None or len(prefix) > len(best[1]):
                    best = (module, prefix)
        return None if best is None else best[0]

    def section_label(self, module: str) -> str:
        for name, _, label in SWITCHABLE:
            if name == module:
                return label
        return module

    def set_section(self, *, actor: Employee, module: str, enabled: bool) -> str:
        """Включить или выключить раздел; вернуть его подпись для сообщения."""
        if module not in {name for name, _, _ in SWITCHABLE}:
            raise PanelError(
                "Этот раздел выключать нельзя: он обеспечивает работу приложения"
            )
        self._sections.set_enabled(module, enabled, actor.id)
        return self.section_label(module)

    # ── вспомогательные проверки ────────────────────────────────────

    def _ensure_login_free(self, login: str, *, employee_id: int | None) -> None:
        other = self._employees.get_by_login(login)
        if other is not None and other.id != employee_id:
            raise PanelError("Логин уже занят")
