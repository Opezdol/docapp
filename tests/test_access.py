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
        modules = {"records", "wiki", "needs", "duty", "distribution"}
        for role, granted in access.ALLOWED.items():
            for permission in granted:
                module, _, action = permission.partition(".")
                assert module in modules, f"{role}: неизвестный модуль {permission}"
                assert action, f"{role}: разрешение без действия {permission}"

    @pytest.mark.parametrize(
        "role,permission,expected",
        [
            # врач: свои анестезии, свой отчёт дежурства; без потребностей и «Чата»
            (DOCTOR, access.RECORDS_EDIT, True),
            (DOCTOR, access.WIKI_READ, False),
            (DOCTOR, access.DUTY_EDIT_OWN, True),
            (DOCTOR, access.NEEDS_VIEW_OWN, False),
            (DOCTOR, access.DUTY_MANAGE, False),
            # медсестра: свои анестезии, своя заявка; без «Чата» и дежурств
            (NURSE, access.NEEDS_EDIT_OWN, True),
            (NURSE, access.RECORDS_EDIT, False),
            (NURSE, access.WIKI_READ, False),
            (NURSE, access.DUTY_VIEW_OWN, False),
            # старшая сестра: потребности полностью; «Чат» ей закрыт до доработки
            (HEAD_NURSE, access.NEEDS_MANAGE, True),
            (HEAD_NURSE, access.NEEDS_CATALOG, True),
            (HEAD_NURSE, access.WIKI_READ, False),
            (HEAD_NURSE, access.WIKI_CURATE, False),
            (HEAD_NURSE, access.DUTY_MANAGE, False),
            # заведующий: всё, включая «Чат»
            (HEAD, access.WIKI_READ, True),
            (HEAD, access.WIKI_SETTINGS, True),
            (HEAD, access.DUTY_MANAGE, True),
            (HEAD, access.DISTRIBUTION_MANAGE, True),
            (HEAD, access.NEEDS_CATALOG, True),
            (DOCTOR, access.DISTRIBUTION_MANAGE, False),
            (HEAD_NURSE, access.DISTRIBUTION_MANAGE, False),
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

    def test_accrual_permissions_are_gone(self):
        """Права отменённого плана «Отчёт» убраны вместе с таблицей (ADR-0024, шаг 5)."""
        removed = {"accrual.view_own", "accrual.view_all"}
        for role, granted in access.ALLOWED.items():
            assert not (granted & removed), f"{role}: {granted & removed}"

    def test_head_has_no_duty_edit(self):
        """Заведующий не вносит отчёт за врача (тест API это фиксирует)."""
        assert access.has(HEAD, access.DUTY_EDIT_OWN) is False

    def test_editor_is_doctor_plus_curator(self):
        """Редактор получает права врача и доступ к «Чату» (чтение и курирование)."""
        doctor = access.permissions(DOCTOR)
        editor = access.permissions(EDITOR)
        assert doctor - editor == frozenset()
        assert editor - doctor == frozenset({access.WIKI_READ, access.WIKI_CURATE})

    def test_chat_is_only_for_head_and_editor(self):
        """«Чат» до доработки — только у заведующего и редактора (12.09.2026)."""
        for role in (DOCTOR, NURSE, HEAD_NURSE):
            assert access.has(role, access.WIKI_READ) is False, role
        assert access.has(HEAD, access.WIKI_READ) is True
        assert access.has(EDITOR, access.WIKI_READ) is True
        assert access.has(EDITOR, access.WIKI_CURATE) is True
        assert access.has(HEAD, access.WIKI_SETTINGS) is True


class TestMenu:
    """Меню строится из той же таблицы — второго списка не существует."""

    def test_covers_all_menu_modules(self):
        modules = {item.module for item in access.MENU}
        assert modules == {"records", "wiki", "needs", "duty", "distribution"}

    def test_paths_are_unique(self):
        paths = [item.path for item in access.MENU]
        assert len(paths) == len(set(paths))

    @pytest.mark.parametrize(
        "role,expected",
        [
            # «Распределение» — раздел заведующего (ADR-0024); «Чат» до доработки
            # виден только заведующему и редактору (12.09.2026).
            (DOCTOR, ["Анестезии", "Дежурства"]),
            (NURSE, ["Анестезии", "Потребности"]),
            (HEAD_NURSE, ["Анестезии", "Потребности"]),
            (HEAD, ["Анестезии", "Чат", "Потребности", "Дежурства", "Распределение"]),
            (EDITOR, ["Анестезии", "Чат", "Дежурства"]),
        ],
    )
    def test_menu_for_role(self, role, expected):
        assert [item.label for item in access.menu_for(role)] == expected

    def test_only_head_sees_distribution(self):
        """Раздел распределения есть только у заведующего."""
        for role in (DOCTOR, NURSE, HEAD_NURSE, EDITOR):
            assert access.has_module(role, "distribution") is False
        assert access.has_module(HEAD, "distribution") is True

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
