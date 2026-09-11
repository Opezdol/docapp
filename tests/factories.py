"""Общие фабрики тестов: единая БД и сотрудники с предсказуемыми id.

Появились на шаге 4, когда база стала одна (ADR-0016): модульные хранилища в
тестах тоже должны открывать общий файл, иначе внешние ключи на `employees` не
работают — «нет таблицы employees» вместо проверки правила модуля.

`init_db` открывает схемы всех модулей (как `create_app`), `seed_employees`
создаёт сотрудников с id 1…10, чтобы тесты могли ссылаться на них как на авторов
заявок, дежурных врачей и кураторов статей.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from docapp.core.db import open_db
from docapp.modules import MODULES
from docapp.people.store import SCHEMA as PEOPLE_SCHEMA

#: id сотрудников, которые есть в тестовой БД после `seed_employees`.
#: 1…10 — обычные участники (авторы заявок, дежурные врачи, кураторы статей);
#: 100 и 200 — «старшая сестра» и «заведующий» в тестах сервисов «Потребностей»:
#: в единой БД у любой ссылки должен быть настоящий сотрудник, поэтому эти id
#: тоже существуют.
EMPLOYEE_IDS: tuple[int, ...] = tuple(range(1, 11)) + (100, 200)


def init_db(db_path: str | Path) -> Path:
    """Создать единую БД со схемами всех модулей и вернуть путь к ней."""
    path = Path(db_path)
    for module in MODULES:
        if module.schema is not None:
            open_db(path, module.schema)
    return path


def seed_employees(
    db_path: str | Path, ids: Iterable[int] = EMPLOYEE_IDS
) -> None:
    """Сотрудники с заданными id — на них ссылаются строки модулей.

    Роль значения не имеет: внешний ключ проверяет только существование строки.
    """
    conn = open_db(db_path, PEOPLE_SCHEMA)
    try:
        for employee_id in ids:
            conn.execute(
                "INSERT OR IGNORE INTO employees (id, last_name, first_name, role) "
                "VALUES (?, ?, 'Тест', 'nurse')",
                (employee_id, f"Тестов{employee_id}"),
            )
        conn.commit()
    finally:
        conn.close()


def make_db(tmp_path, *, seed: bool = True) -> Path:
    """Единая БД для модульных тестов: схемы всех модулей (+ сотрудники)."""
    db = init_db(tmp_path / "app.db")
    if seed:
        seed_employees(db)
    return db


def catalog(db_path, yaml_text: str):
    """Каталог расходки из БД, наполненный YAML-текстом.

    Так же, как при первом старте приложения: пустая БД получает каталог из
    seed-файла, дальше источник правды — таблицы (ADR-0019).
    """
    from docapp.needs.catalog_store import SqliteCatalog
    from docapp.needs.catalog_yaml import parse

    cat = SqliteCatalog(db_path)
    cat.import_catalog(parse(yaml_text, source="тесты"), None, source="тесты")
    return cat
