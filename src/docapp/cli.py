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
from docapp.domain.employee import DOCTOR, HEAD, HEAD_NURSE, NURSE, Employee
from docapp.storage.sqlite_store import SqliteEmployeeStore

_CHARS = string.ascii_letters + string.digits


def _random_password(length: int = 10) -> str:
    """Случайный пароль из букв и цифр (без спецсимволов — проще вводить)."""
    return "".join(secrets.choice(_CHARS) for _ in range(length))


def _create_user(args: argparse.Namespace) -> int:
    """Создать врача или заведующего (с логином и паролем)."""
    if not args.login:
        print("Ошибка: для роли doctor/head/head_nurse обязателен --login", file=sys.stderr)
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
    """Применить миграции схемы ко всем БД приложения.

    Сейчас версии схем всех БД = 1 (базовая схема создаётся _SCHEMA при
    подключении). Будущие изменения схемы добавляются как миграции в
    *_store.py (список _MIGRATIONS); здесь они применяются принудительно,
    чтобы не ждать первого запроса к приложению.
    """
    import sqlite3
    from pathlib import Path

    from docapp.config import DATA_DIR, db_path
    from docapp.consult.config import load_consult_config
    from docapp.consult.store import SCHEMA_VERSION as CONSULT_VERSION
    from docapp.needs.config import load_needs_config
    from docapp.needs.store import SCHEMA_VERSION as NEEDS_VERSION
    from docapp.storage.sqlite_store import SCHEMA_VERSION as MAIN_VERSION

    dbs = [
        ("docapp", db_path(), MAIN_VERSION),
        ("consult", Path(load_consult_config().index_dir) / "consult.db", CONSULT_VERSION),
        ("needs", load_needs_config().db_path, NEEDS_VERSION),
    ]

    all_ok = True
    for name, path, want_version in dbs:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        created = not path.exists()
        # Открыть соединение как делают хранилища: применит _SCHEMA + миграции
        # (создаст файл БД, если его нет)
        try:
            if name == "docapp":
                from docapp.storage.sqlite_store import _connect as _c
            elif name == "consult":
                from docapp.consult.store import _connect as _c
            else:
                from docapp.needs.store import _connect as _c
            conn = _c(path)
            conn.close()
            new_version = sqlite3.connect(str(path)).execute(
                "PRAGMA user_version"
            ).fetchone()[0]
            status = "OK" if new_version >= want_version else "WARN"
            created_note = " (создана)" if created else ""
            print(
                f"{status}: {name} user_version={new_version}"
                f" (нужно {want_version}){created_note}"
            )
            if new_version < want_version:
                all_ok = False
        except Exception as exc:  # noqa: BLE001
            print(f"ОШИБКА: {name}: {exc}")
            all_ok = False

    return 0 if all_ok else 1
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
        "user", parents=[common], help="Создать врача, заведующего или старшую сестру"
    )
    p_user.add_argument("--role", choices=[DOCTOR, HEAD, HEAD_NURSE], default=DOCTOR)
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
        help="Применить миграции схемы ко всем БД (docapp, consult, needs)",
    )
    p_migrate.set_defaults(func=_migrate)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
