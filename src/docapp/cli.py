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
from docapp.people.store import SqliteEmployeeStore

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


def _import_legacy(args: argparse.Namespace) -> int:
    """Перенести данные прежних баз в единую БД (шаг 4b, ADR-0016).

    Запускать на копиях прежних баз; целевая БД — та же, что у приложения.
    """
    from pathlib import Path

    from docapp.legacy import LegacyImportError, import_legacy

    target = Path(args.target) if args.target else Path(db_path())
    sources = {
        "core": Path(args.core) if args.core else None,
        "needs": Path(args.needs) if args.needs else None,
        "wiki": Path(args.wiki) if args.wiki else None,
        "duty": Path(args.duty) if args.duty else None,
        "catalog": Path(args.catalog) if args.catalog else None,
    }
    if not any(sources.values()):
        print(
            "Ошибка: не указан ни один источник (--core, --needs, --wiki, --duty)",
            file=sys.stderr,
        )
        return 2

    print(f"Целевая БД: {target}")
    for name, path in sources.items():
        print(f"  источник {name}: {path if path else '—'}")
    print()

    try:
        report = import_legacy(
            target,
            core_db=sources["core"],
            needs_db=sources["needs"],
            wiki_db=sources["wiki"],
            duty_db=sources["duty"],
            catalog_path=sources["catalog"],
            sources_dir=args.sources_dir,
            skip_orphans=args.skip_orphans,
        )
    except LegacyImportError as exc:
        print(f"ОСТАНОВЛЕНО: {exc}", file=sys.stderr)
        return 1

    print(report.summary())
    if report.orphans:
        print()
        print("Перенос выполнен, но в целевой БД остались сироты — см. выше.")
        return 1
    print()
    print("Готово. Проверить результат: docapp check")
    return 0


def _repair_schema(args: argparse.Namespace) -> int:
    """Привести форму таблиц к объявленным схемам (остатки прежних баз).

    `CREATE TABLE IF NOT EXISTS` принимает таблицу другой формы как есть, и
    такая таблица падает на первой записи. Команда пересобирает её по
    объявлению, строки при этом сохраняются.
    """
    from pathlib import Path

    from docapp.core.db import repair
    from docapp.core.registry import databases
    from docapp.modules import MODULES

    target = Path(args.db) if args.db else Path(db_path())
    if not target.exists():
        print(f"файла нет: {target}")
        return 1
    actions = repair(target, [schema for _, _, schema in databases(MODULES, target)])
    if not actions:
        print(f"Форма таблиц совпадает с объявленной схемой: {target}")
        return 0
    print(f"Исправлено в {target}:")
    for action in actions:
        print("  - " + action)
    print("Проверить: docapp check")
    return 0


def _check(args: argparse.Namespace) -> int:
    """Счётчики, версии схем и целостность единой БД (шаг 4b)."""
    from pathlib import Path

    from docapp.legacy import check

    target = Path(args.db) if args.db else Path(db_path())
    report = check(target)
    print(report.summary())
    return 0 if report.ok else 1


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
        help="Применить миграции схемы к единой БД (docapp, wiki, needs, duty)",
    )
    p_migrate.set_defaults(func=_migrate)

    p_import = sub.add_parser(
        "import-legacy",
        help="Перенести данные прежних баз в единую БД (одноразово, на копиях)",
    )
    p_import.add_argument("--core", help="прежняя основная БД (data/docapp.db)")
    p_import.add_argument("--needs", help="прежняя БД «Потребностей» (data/needs/needs.db)")
    p_import.add_argument("--wiki", help="прежняя БД «Компендиума» (data/wiki/wiki.db)")
    p_import.add_argument("--duty", help="прежняя БД «Дежурств» (data/duty/duty.db)")
    p_import.add_argument(
        "--catalog",
        help="файл каталога расходки (data/needs/catalog.yaml) — до ADR-0019 каталог жил файлом",
    )
    p_import.add_argument("--target", help="целевая единая БД (по умолчанию DOCAPP_DB)")
    p_import.add_argument(
        "--sources-dir",
        help="папка PDF-источников «Компендиума» (по умолчанию WIKI_SOURCES_DIR)",
    )
    p_import.add_argument(
        "--skip-orphans",
        action="store_true",
        help="перенести всё, кроме строк со ссылкой на несуществующего сотрудника",
    )
    p_import.set_defaults(func=_import_legacy)

    p_check = sub.add_parser("check", help="Счётчики и целостность единой БД")
    p_check.add_argument("--db", help="файл БД (по умолчанию DOCAPP_DB)")
    p_check.set_defaults(func=_check)

    p_repair = sub.add_parser(
        "repair-schema",
        help="Пересобрать таблицы, форма которых осталась от прежней схемы",
    )
    p_repair.add_argument("--db", help="файл БД (по умолчанию DOCAPP_DB)")
    p_repair.set_defaults(func=_repair_schema)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
