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
from docapp.domain.employee import DOCTOR, EDITOR, HEAD, HEAD_NURSE, NURSE, Employee
from docapp.storage.sqlite_store import SqliteEmployeeStore

_CHARS = string.ascii_letters + string.digits


def _random_password(length: int = 10) -> str:
    """Случайный пароль из букв и цифр (без спецсимволов — проще вводить)."""
    return "".join(secrets.choice(_CHARS) for _ in range(length))


def _create_user(args: argparse.Namespace) -> int:
    """Создать врача или заведующего (с логином и паролем)."""
    if not args.login:
        print("Ошибка: для роли doctor/head/head_nurse/editor обязателен --login", file=sys.stderr)
        return 2

    password = args.password or _random_password()
    employee = Employee(
        last_name=args.last_name,
        first_name=args.first_name,
        middle_name=args.middle_name,
        role=args.role,
        login=args.login,
        password_hash=hash_password(password),
        buh_id=args.buh_id or None,
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
    """Создать медсестру. С --login она сможет входить и видеть свои анестезии."""
    if args.password and not args.login:
        print("Ошибка: --password задан, но нет --login", file=sys.stderr)
        return 2

    password = None
    password_hash = None
    if args.login:
        password = args.password or _random_password()
        password_hash = hash_password(password)

    employee = Employee(
        last_name=args.last_name,
        first_name=args.first_name,
        middle_name=args.middle_name,
        role=NURSE,
        login=args.login,
        password_hash=password_hash,
        buh_id=args.buh_id or None,
    )
    try:
        with SqliteEmployeeStore(db_path()) as store:
            saved = store.add(employee)
    except sqlite3.IntegrityError:
        print(f"Ошибка: логин «{args.login}» уже занят", file=sys.stderr)
        return 2
    print(f"Создана: {saved.full_name} (id={saved.id}, роль: nurse)")
    if args.login:
        print(f"Логин: {saved.login}")
        if args.password:
            print("Пароль: задан вами")
        else:
            print(f"Пароль: {password}  ← передайте его сотруднице")
    return 0


def _set_buh_id(args: argparse.Namespace) -> int:
    """Проставить/заменить номер в бухгалтерии у существующего сотрудника."""
    if not args.buh_id.strip():
        print("Ошибка: номер в бухгалтерии не может быть пустым", file=sys.stderr)
        return 2
    with SqliteEmployeeStore(db_path()) as store:
        try:
            store.update_buh_id(args.employee_id, args.buh_id.strip())
        except KeyError:
            print(f"Ошибка: сотрудник с id {args.employee_id} не найден", file=sys.stderr)
            return 2
        employee = store.get_by_id(args.employee_id)
        if employee is None:  # не может случиться после успешного update, но для типов
            print("Ошибка: сотрудник не найден", file=sys.stderr)
            return 2
    print(f"Обновлено: {employee.full_name} (id={employee.id})")
    print(f"Номер в бухгалтерии: {employee.buh_id}")
    return 0


def _migrate(args: argparse.Namespace) -> int:
    """Применить миграции схемы ко всем модулям приложения.

    Механизм один на всё приложение (core.db, ADR-0016), список баз — из реестра
    модулей (docapp.modules, ADR-0017): отдельных списков больше нет.
    """
    from pathlib import Path

    from docapp.core.db import migrate
    from docapp.core.registry import databases
    from docapp.modules import MODULES

    all_ok = True
    for name, path, schema in databases(MODULES, Path(db_path())):
        path.parent.mkdir(parents=True, exist_ok=True)
        created = not path.exists()
        try:
            version = migrate(path, schema)
        except Exception as exc:  # noqa: BLE001 — отчёт по всем БД важнее первой ошибки
            print(f"ОШИБКА: {name}: {exc}")
            all_ok = False
            continue
        status = "OK" if version >= schema.version else "WARN"
        created_note = " (создана)" if created else ""
        print(
            f"{status}: {name} version={version} (нужно {schema.version}){created_note}"
        )
        if version < schema.version:
            all_ok = False

    return 0 if all_ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(prog="docapp", description="Команды docapp")
    sub = parser.add_subparsers(dest="command", required=True)

    # Общие аргументы ФИО для обеих команд
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("last_name", help="Фамилия")
    common.add_argument("first_name", help="Имя")
    common.add_argument("middle_name", nargs="?", default="", help="Отчество (необязательно)")
    common.add_argument("--buh-id", help="Номер в бухгалтерии (можно добавить позже)")

    p_user = sub.add_parser(
        "user", parents=[common], help="Создать врача, заведующего, старшую сестру или редактора"
    )
    p_user.add_argument("--role", choices=[DOCTOR, HEAD, HEAD_NURSE, EDITOR], default=DOCTOR)
    p_user.add_argument("--login", help="Логин для входа")
    p_user.add_argument("--password", help="Пароль (если не задан — сгенерируется)")
    p_user.set_defaults(func=_create_user)

    p_nurse = sub.add_parser("nurse", parents=[common], help="Создать медсестру")
    p_nurse.add_argument("--login", help="Логин (если нужен доступ к просмотру)")
    p_nurse.add_argument("--password", help="Пароль (если не задан — сгенерируется)")
    p_nurse.set_defaults(func=_create_nurse)

    p_buh = sub.add_parser("buh-id", help="Проставить номер в бухгалтерии")
    p_buh.add_argument("employee_id", type=int, help="id сотрудника (виден при создании)")
    p_buh.add_argument("buh_id", help="Номер в бухгалтерии")
    p_buh.set_defaults(func=_set_buh_id)

    p_migrate = sub.add_parser(
        "migrate",
        help="Применить миграции схемы ко всем БД (docapp, wiki, needs, duty)",
    )
    p_migrate.set_defaults(func=_migrate)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
