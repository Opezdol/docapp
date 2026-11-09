"""Вход в систему: проверка логина и пароля."""

from docapp.domain.employee import Employee
from docapp.auth.passwords import verify_password


class InvalidCredentials(ValueError):
    """Неверный логин или пароль."""


class AccountDisabled(InvalidCredentials):
    """Учётная запись закрыта: сотрудник уволен (ADR-0030)."""


class Authenticator:
    """Проверяет учётные данные и возвращает сотрудника."""

    def __init__(self, employees) -> None:
        self._employees = employees

    def authenticate(self, login: str, password: str) -> Employee:
        """Вернуть сотрудника при верном логине и пароле.

        Обе ошибки (нет логина / неверный пароль) — одинаковые:
        не подсказываем, что логин существует. Закрытую учётную запись
        отличаем сознательно: человек уволен, а не забыл пароль, и звонок
        заведующему «у меня не входит» ничего не даёт (ADR-0030).
        """
        employee = self._employees.get_by_login(login)
        if employee is None or employee.password_hash is None:
            raise InvalidCredentials("Неверный логин или пароль")
        if not verify_password(password, employee.password_hash):
            raise InvalidCredentials("Неверный логин или пароль")
        if not employee.active:
            raise AccountDisabled("Учётная запись закрыта")
        return employee
