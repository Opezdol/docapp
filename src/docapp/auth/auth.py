"""Вход в систему: проверка логина и пароля."""

from docapp.domain.employee import Employee
from docapp.auth.passwords import MIN_PASSWORD, hash_password, verify_password


class InvalidCredentials(ValueError):
    """Неверный логин или пароль."""


class AccountDisabled(InvalidCredentials):
    """Учётная запись закрыта: сотрудник уволен (ADR-0030)."""


class PasswordChangeError(ValueError):
    """Пароль не изменён: причина показывается человеку текстом."""


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

    def change_password(
        self, employee: Employee, current: str, new: str, repeat: str
    ) -> None:
        """Сменить свой пароль: старый проверяется, новый хешируется сразу.

        Правила простые и все проверяются на сервере: старый пароль должен
        сходиться, новый — не короче MIN_PASSWORD и совпадать с повтором.
        Своё имя и пароль в текст ошибки не попадают.
        """
        if employee.password_hash is None or not verify_password(
            current, employee.password_hash
        ):
            raise PasswordChangeError("Текущий пароль неверен")
        if len(new) < MIN_PASSWORD:
            raise PasswordChangeError(f"Новый пароль короче {MIN_PASSWORD} символов")
        if new != repeat:
            raise PasswordChangeError("Новый пароль и повтор не совпадают")
        self._employees.set_password(employee.id, hash_password(new))
