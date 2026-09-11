"""Сотрудники: схема таблицы и хранилище на SQLite.

Модуль `people` владеет сотрудниками: их таблица и их хранилище (ADR-0017).
Остальные модули берут справочник сотрудников через контейнер этого модуля, а не
запросом к его таблице.

Схема появилась делением прежнего `storage/sqlite_store.py`, где в одном файле
жили сотрудники, анестезии и «активная сестра»: у таблиц разные владельцы, значит
и схемы разные — версия схемы ведётся на модуль (ADR-0016).

Абстрактных портов (`EmployeeStore` и соседей) здесь нет: адаптер один, а порт
без второй реализации — гипотетический шов (ADR-0017). Интерфейс, который нужен
соседям, объявляет сам потребитель — см. `summary/service.RecordsInterface`.
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

from docapp.core.db import Schema, open_db
from docapp.domain.employee import Employee

_SCHEMA = """
CREATE TABLE IF NOT EXISTS employees (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    last_name     TEXT NOT NULL,
    first_name    TEXT NOT NULL,
    middle_name   TEXT NOT NULL DEFAULT '',
    role          TEXT NOT NULL,
    login         TEXT UNIQUE,
    password_hash TEXT,
    buh_id        TEXT
);
"""

#: Схема сотрудников: одна таблица, отдельной версии хватает.
SCHEMA = Schema(module="people", sql=_SCHEMA, version=1)


def _connect(db_path: str | Path) -> sqlite3.Connection:
    """Открыть БД: подключение, PRAGMA, схема модуля — внутри core.db."""
    return open_db(db_path, SCHEMA)


class SqliteEmployeeStore:
    """Сотрудники в SQLite-файле."""

    def __init__(self, db_path: str | Path) -> None:
        self._conn = _connect(db_path)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "SqliteEmployeeStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def add(self, employee: Employee) -> Employee:
        cur = self._conn.execute(
            "INSERT INTO employees (last_name, first_name, middle_name, role, login, password_hash, buh_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                employee.last_name,
                employee.first_name,
                employee.middle_name,
                employee.role,
                employee.login,
                employee.password_hash,
                employee.buh_id,
            ),
        )
        self._conn.commit()
        return replace(employee, id=cur.lastrowid)

    def get_by_id(self, employee_id: int) -> Employee | None:
        row = self._conn.execute(
            "SELECT * FROM employees WHERE id = ?", (employee_id,)
        ).fetchone()
        return self._row_to_employee(row) if row else None

    def get_by_login(self, login: str) -> Employee | None:
        row = self._conn.execute(
            "SELECT * FROM employees WHERE login = ?", (login,)
        ).fetchone()
        return self._row_to_employee(row) if row else None

    def list_all(self) -> list[Employee]:
        rows = self._conn.execute(
            "SELECT * FROM employees ORDER BY last_name, first_name"
        ).fetchall()
        return [self._row_to_employee(r) for r in rows]

    def list_nurses(self) -> list[Employee]:
        rows = self._conn.execute(
            "SELECT * FROM employees WHERE role = 'nurse' ORDER BY last_name, first_name"
        ).fetchall()
        return [self._row_to_employee(r) for r in rows]

    def update_buh_id(self, employee_id: int, buh_id: str) -> None:
        cur = self._conn.execute(
            "UPDATE employees SET buh_id = ? WHERE id = ?", (buh_id, employee_id)
        )
        self._conn.commit()
        if cur.rowcount == 0:
            raise KeyError(f"Сотрудник с id {employee_id} не найден")

    @staticmethod
    def _row_to_employee(row: sqlite3.Row) -> Employee:
        return Employee(
            id=row["id"],
            last_name=row["last_name"],
            first_name=row["first_name"],
            middle_name=row["middle_name"],
            role=row["role"],
            login=row["login"],
            password_hash=row["password_hash"],
            buh_id=row["buh_id"],
        )
