"""Тесты шва доступа (core/access.py): таблица прав, меню, проверки.

Таблица проверяется целиком и без HTTP — это и есть смысл шага 2: права читаются
в одном месте, а не восстанавливаются по десяти сценариям.
"""

from types import SimpleNamespace

import pytest
from starlette.requests import Request

from docapp.core import access
from docapp.domain.employee import DOCTOR, EDITOR, HEAD, HEAD_NURSE, NURSE
from docapp.domain.employee import Employee


class TestPermissionsTable:
    """Таблица прав: состав и границы (ADR-0023)."""

    def test_every_role_has_permissions(self):
        for role in (DOCTOR, NURSE, HEAD_NURSE, HEAD, EDITOR):
            assert access.permissions(role), f"у роли {role} нет прав"

    def test_unknown_role_has_nothing(self):
        assert access.permissions("boss") == frozenset()

    def test_permissions_are_namespaced(self):
        """Разрешение всегда «модуль.действие» — от этого зависит меню."""
        modules = {"records", "wiki", "needs", "duty", "accrual"}
        for role, granted in access.ALLOWED.items():
            for permission in granted:
                module, _, action = permission.partition(".")
                assert module in modules, f"{role}: неизвестный модуль {permission}"
                assert action, f"{role}: разрешение без действия {permission}"

    @pytest.mark.parametrize(
        "role,permission,expected",
        [
            # врач: свои анестезии, свой отчёт дежурства, компендиум; без потребностей
            (DOCTOR, access.RECORDS_EDIT, True),
            (DOCTOR, access.WIKI_READ, True),
            (DOCTOR, access.DUTY_EDIT_OWN, True),
            (DOCTOR, access.NEEDS_VIEW_OWN, False),
            (DOCTOR, access.DUTY_MANAGE, False),
            # медсестра: свои анестезии, своя заявка; без компендиума и дежурств
            (NURSE, access.NEEDS_EDIT_OWN, True),
            (NURSE, access.RECORDS_EDIT, False),
            (NURSE, access.WIKI_READ, False),
            (NURSE, access.DUTY_VIEW_OWN, False),
            # старшая сестра: потребности полностью + компендиум на чтение
            (HEAD_NURSE, access.NEEDS_MANAGE, True),
            (HEAD_NURSE, access.NEEDS_CATALOG, True),
            (HEAD_NURSE, access.WIKI_READ, True),
            (HEAD_NURSE, access.WIKI_CURATE, False),
            (HEAD_NURSE, access.DUTY_MANAGE, False),
            # заведующий: всё, включая настройки и выгрузку
            (HEAD, access.WIKI_SETTINGS, True),
            (HEAD, access.DUTY_MANAGE, True),
            (HEAD, access.RECORDS_VIEW_ALL, True),
            (HEAD, access.NEEDS_CATALOG, True),
            (HEAD, access.ACCRUAL_VIEW_ALL, True),
            # редактор — врач с правом курирования документов
            (EDITOR, access.RECORDS_EDIT, True),
            (EDITOR, access.WIKI_CURATE, True),
            (EDITOR, access.DUTY_EDIT_OWN, True),
            (EDITOR, access.NEEDS_VIEW_OWN, False),
            (EDITOR, access.WIKI_SETTINGS, False),
        ],
    )
    def test_has(self, role, permission, expected):
        assert access.has(role, permission) is expected

    def test_has_is_or(self):
        """`has` — правило «или»: достаточно одного из разрешений."""
        assert access.has(HEAD, access.NEEDS_VIEW_OWN, access.NEEDS_VIEW_ALL) is True
        assert access.has(NURSE, access.NEEDS_VIEW_OWN, access.NEEDS_VIEW_ALL) is True
        assert access.has(NURSE, access.DUTY_MANAGE, access.WIKI_CURATE) is False

    def test_head_has_no_duty_edit(self):
        """Заведующий не вносит отчёт за врача (тест API это фиксирует)."""
        assert access.has(HEAD, access.DUTY_EDIT_OWN) is False

    def test_editor_is_doctor_plus_curator(self):
        """Редактор получает права врача — за вычетом настроек компендиума."""
        doctor = access.permissions(DOCTOR)
        editor = access.permissions(EDITOR)
        assert doctor - editor == frozenset()
        assert editor - doctor == frozenset({access.WIKI_CURATE})

    def test_head_nurse_reads_compendium(self):
        """Старшей сестре «Компендиум» доступен, медсестре — нет (ADR-11)."""
        assert access.has(HEAD_NURSE, access.WIKI_READ) is True
        assert access.has(NURSE, access.WIKI_READ) is False


class TestMenu:
    """Меню строится из той же таблицы — второго списка не существует."""

    def test_covers_all_menu_modules(self):
        modules = {item.module for item in access.MENU}
        assert modules == {"records", "wiki", "needs", "duty"}

    def test_paths_are_unique(self):
        paths = [item.path for item in access.MENU]
        assert len(paths) == len(set(paths))

    @pytest.mark.parametrize(
        "role,expected",
        [
            (DOCTOR, ["Анестезии", "Компендиум", "Дежурства"]),
            (NURSE, ["Анестезии", "Потребности"]),
            (HEAD_NURSE, ["Анестезии", "Компендиум", "Потребности"]),
            (HEAD, ["Анестезии", "Компендиум", "Потребности", "Дежурства"]),
            (EDITOR, ["Анестезии", "Компендиум", "Дежурства"]),
        ],
    )
    def test_menu_for_role(self, role, expected):
        assert [item.label for item in access.menu_for(role)] == expected

    def test_unknown_role_gets_empty_menu(self):
        assert access.menu_for("boss") == []


def _request(employee: Employee | None = None):
    """Настоящий starlette-запрос: в scope только сессия и state.employees."""
    scope = {
        "type": "http",
        "session": {} if employee is None else {"employee_id": employee.id},
        "app": SimpleNamespace(
            state=SimpleNamespace(
                employees=SimpleNamespace(get_by_id=lambda _id: employee)
            )
        ),
    }
    return Request(scope)


class TestCurrentUser:
    def test_without_session_is_none(self):
        assert access.current_user(_request()) is None

    def test_with_session_returns_employee(self):
        employee = Employee(last_name="Иванов", first_name="Иван", role=DOCTOR, id=7)
        assert access.current_user(_request(employee)) is employee


class TestRequire:
    def test_anonymous_gets_not_authenticated(self):
        with pytest.raises(access.NotAuthenticated):
            access.require(_request(), access.NEEDS_MANAGE)

    def test_wrong_role_gets_denied(self):
        nurse = Employee(last_name="Сидорова", first_name="Анна", role=NURSE, id=2)
        with pytest.raises(access.AccessDenied):
            access.require(_request(nurse), access.NEEDS_MANAGE)

    def test_right_role_passes(self):
        head_nurse = Employee(last_name="Орлова", first_name="Елена", role=HEAD_NURSE, id=3)
        assert access.require(_request(head_nurse), access.NEEDS_MANAGE) is head_nurse

    def test_require_message_is_neutral(self):
        """Сообщение `require` не подсказывает, кто именно нужен."""
        nurse = Employee(last_name="Сидорова", first_name="Анна", role=NURSE, id=2)
        with pytest.raises(access.AccessDenied, match="запрещён"):
            access.require(_request(nurse), access.NEEDS_MANAGE)

    def test_ensure_uses_given_message(self):
        nurse = Employee(last_name="Сидорова", first_name="Анна", role=NURSE, id=2)
        with pytest.raises(access.AccessDenied, match="только старшей сестре"):
            access.ensure(
                nurse, access.NEEDS_MANAGE, message="Доступно только старшей сестре"
            )

    def test_ensure_returns_user(self):
        doctor = Employee(last_name="Иванов", first_name="Иван", role=DOCTOR, id=1)
        assert access.ensure(doctor, access.RECORDS_EDIT) is doctor

    def test_ensure_raises(self):
        doctor = Employee(last_name="Иванов", first_name="Иван", role=DOCTOR, id=1)
        with pytest.raises(access.AccessDenied):
            access.ensure(doctor, access.NEEDS_MANAGE)
