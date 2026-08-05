"""Вход в систему: проверка логина и пароля."""

from docapp.domain.employee import Employee
from docapp.auth.passwords import verify_password


class InvalidCredentials(ValueError):
    """Неверный логин или пароль."""


class Authenticator:
    """Проверяет учётные данные и возвращает сотрудника."""

    def __init__(self, employees) -> None:
        self._employees = employees

    def authenticate(self, login: str, password: str) -> Employee:
        """Вернуть сотрудника при верном логине и пароле.

        Обе ошибки (нет логина / неверный пароль) — одинаковые:
        не подсказываем, что логин существует.
        """
        employee = self._employees.get_by_login(login)
        if employee is None or employee.password_hash is None:
            raise InvalidCredentials("Неверный логин или пароль")
        if not verify_password(password, employee.password_hash):
            raise InvalidCredentials("Неверный логин или пароль")
        return employee
