"""Консольные команды docapp.

Сейчас одна команда — создание сотрудника (врача, заведующего, медсестры).

Примеры:
  uv run python -m docapp.cli user Иванов Иван Иванович --role doctor --login ivanov
  uv run python -m docapp.cli user Петров Пётр --role head --login petrov --password secret
  uv run python -m docapp.cli nurse Сидорова Анна
"""

import argparse
import secrets
import sqlite3
import string
import sys

from docapp.auth.passwords import hash_password
from docapp.config import db_path
from docapp.domain.employee import DOCTOR, HEAD, NURSE, Employee
from docapp.storage.sqlite_store import SqliteEmployeeStore

_CHARS = string.ascii_letters + string.digits


def _random_password(length: int = 10) -> str:
    """Случайный пароль из букв и цифр (без спецсимволов — проще вводить)."""
    return "".join(secrets.choice(_CHARS) for _ in range(length))


def _create_user(args: argparse.Namespace) -> int:
    """Создать врача или заведующего (с логином и паролем)."""
    if not args.login:
        print("Ошибка: для роли doctor/head обязателен --login", file=sys.stderr)
        return 2

    password = args.password or _random_password()
    employee = Employee(
        last_name=args.last_name,
        first_name=args.first_name,
        middle_name=args.middle_name,
        role=args.role,
        login=args.login,
        password_hash=hash_password(password),
    )

    try:
        with SqliteEmployeeStore(db_path()) as store:
            saved = store.add(employee)
    except sqlite3.IntegrityError:
        print(f"Ошибка: логин «{args.login}» уже занят", file=sys.stderr)
        return 2

    print(f"Создан: {saved.full_name} (id={saved.id}, роль: {saved.role})")
    print(f"Логин: {saved.login}")
    if args.password:
        print("Пароль: задан вами")
    else:
        # Печатаем пароль один раз — его больше не увидит никто,
        # в БД хранится только хеш.
        print(f"Пароль: {password}  ← передайте его сотруднику")
    return 0


def _create_nurse(args: argparse.Namespace) -> int:
    """Создать медсестру (без логина — она не входит в систему)."""
    employee = Employee(
        last_name=args.last_name,
        first_name=args.first_name,
        middle_name=args.middle_name,
        role=NURSE,
    )
    with SqliteEmployeeStore(db_path()) as store:
        saved = store.add(employee)
    print(f"Создана: {saved.full_name} (id={saved.id}, роль: nurse)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="docapp", description="Команды docapp")
    sub = parser.add_subparsers(dest="command", required=True)

    # Общие аргументы ФИО для обеих команд
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("last_name", help="Фамилия")
    common.add_argument("first_name", help="Имя")
    common.add_argument("middle_name", nargs="?", default="", help="Отчество (необязательно)")

    p_user = sub.add_parser("user", parents=[common], help="Создать врача или заведующего")
    p_user.add_argument("--role", choices=[DOCTOR, HEAD], default=DOCTOR)
    p_user.add_argument("--login", help="Логин для входа")
    p_user.add_argument("--password", help="Пароль (если не задан — сгенерируется)")
    p_user.set_defaults(func=_create_user)

    p_nurse = sub.add_parser("nurse", parents=[common], help="Создать медсестру")
    p_nurse.set_defaults(func=_create_nurse)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
