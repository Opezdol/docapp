"""Реализация хранилища на SQLite: один файл БД, две таблицы.

Два класса (по одному на порт), а не один: в Python нельзя определить
в одном классе два метода с одним именем (add/get_by_id для Employee
и Anesthesia) — второе определение перезапишет первое.
"""

import sqlite3
from datetime import date, datetime, timezone, timedelta
from dataclasses import replace
from pathlib import Path

from docapp.domain.anesthesia import Anesthesia
from docapp.domain.employee import Employee
from docapp.storage.store import ActiveNurseStore, AnesthesiaStore, EmployeeStore

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
CREATE TABLE IF NOT EXISTS anesthesia (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    date           TEXT NOT NULL,
    patient_name   TEXT NOT NULL,
    doctor_id      INTEGER NOT NULL REFERENCES employees(id),
    nurse_id       INTEGER NOT NULL REFERENCES employees(id),
    created_at     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS active_nurse (
    doctor_id INTEGER PRIMARY KEY REFERENCES employees(id),
    nurse_id  INTEGER NOT NULL REFERENCES employees(id)
);
"""

# Версия схемы основной БД (PRAGMA user_version). Увеличивайте на 1 при
# каждом изменении схемы и добавляйте миграцию в _MIGRATIONS ниже.
SCHEMA_VERSION = 2

# Миграции: каждая — (версия_после_применения, название, список SQL).
# Применяются по порядку, только если user_version < версии миграции.
# ВАЖНО: не редактируйте уже опубликованные миграции — добавляйте новые.
_MIGRATIONS: list[tuple[int, str, list[str]]] = [
    # (1, "initial schema", [])  # базовая схема создаётся _SCHEMA выше
    # v2: убрать номер истории болезни (минимизация данных, 152-ФЗ).
    # DROP COLUMN стирает и колонку, и старые значения.
    (
        2,
        "drop history_number from anesthesia",
        ["ALTER TABLE anesthesia DROP COLUMN history_number"],
    ),
]


def _apply_migrations(conn: sqlite3.Connection) -> None:
    """Применить миграции схемы, если user_version устарел.

    Новая БД (user_version=0) считается созданной на текущей версии схемы:
    _SCHEMA выше уже создал все таблицы. Старые БД с user_version < текущей
    проходят через миграции из _MIGRATIONS по порядку.
    """
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version == 0:
        # БД только что создана _SCHEMA — сразу помечаем текущей версией
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
        return
    for target, name, statements in _MIGRATIONS:
        if version < target:
            for stmt in statements:
                conn.execute(stmt)
            conn.execute(f"PRAGMA user_version = {target}")
            conn.commit()
            version = target


def _connect(db_path: str | Path) -> sqlite3.Connection:
    # check_same_thread=False: FastAPI обрабатывает запросы в пуле потоков,
    # соединение не может быть привязано к одному потоку.
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.executescript(_SCHEMA)
    _apply_migrations(conn)
    conn.commit()
    return conn


class SqliteEmployeeStore(EmployeeStore):
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


class SqliteAnesthesiaStore(AnesthesiaStore):
    """Анестезии в SQLite-файле."""

    def __init__(self, db_path: str | Path) -> None:
        self._conn = _connect(db_path)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "SqliteAnesthesiaStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def add(self, anesthesia: Anesthesia) -> Anesthesia:
        cur = self._conn.execute(
            "INSERT INTO anesthesia (date, patient_name, doctor_id, nurse_id, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                anesthesia.date.isoformat(),
                anesthesia.patient_name,
                anesthesia.doctor_id,
                anesthesia.nurse_id,
                anesthesia.created_at.isoformat(),
            ),
        )
        self._conn.commit()
        return replace(anesthesia, id=cur.lastrowid)

    def get_by_id(self, anesthesia_id: int) -> Anesthesia | None:
        row = self._conn.execute(
            "SELECT * FROM anesthesia WHERE id = ?", (anesthesia_id,)
        ).fetchone()
        return self._row_to_anesthesia(row) if row else None

    def list_by_doctor(self, doctor_id: int) -> list[Anesthesia]:
        rows = self._conn.execute(
            "SELECT * FROM anesthesia WHERE doctor_id = ? ORDER BY date DESC, id DESC",
            (doctor_id,),
        ).fetchall()
        return [self._row_to_anesthesia(r) for r in rows]

    def list_by_nurse(self, nurse_id: int) -> list[Anesthesia]:
        rows = self._conn.execute(
            "SELECT * FROM anesthesia WHERE nurse_id = ? ORDER BY date DESC, id DESC",
            (nurse_id,),
        ).fetchall()
        return [self._row_to_anesthesia(r) for r in rows]

    def update(self, anesthesia: Anesthesia) -> None:
        cur = self._conn.execute(
            "UPDATE anesthesia SET date = ?, patient_name = ?, "
            "doctor_id = ?, nurse_id = ?, created_at = ? WHERE id = ?",
            (
                anesthesia.date.isoformat(),
                anesthesia.patient_name,
                anesthesia.doctor_id,
                anesthesia.nurse_id,
                anesthesia.created_at.isoformat(),
                anesthesia.id,
            ),
        )
        self._conn.commit()
        if cur.rowcount == 0:
            raise KeyError(f"Запись анестезии с id {anesthesia.id} не найдена")

    def delete(self, anesthesia_id: int) -> None:
        self._conn.execute(
            "DELETE FROM anesthesia WHERE id = ?", (anesthesia_id,)
        )
        self._conn.commit()

    @staticmethod
    def _row_to_anesthesia(row: sqlite3.Row) -> Anesthesia:
        created_at = datetime.fromisoformat(row["created_at"])
        if created_at.utcoffset() == timedelta(0):
            created_at = created_at.replace(tzinfo=timezone.utc)
        return Anesthesia(
            id=row["id"],
            date=date.fromisoformat(row["date"]),
            patient_name=row["patient_name"],
            doctor_id=row["doctor_id"],
            nurse_id=row["nurse_id"],
            created_at=created_at,
        )


class SqliteActiveNurseStore(ActiveNurseStore):
    """«Активная сестра» врача в SQLite-файле (ADR-4)."""

    def __init__(self, db_path: str | Path) -> None:
        self._conn = _connect(db_path)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "SqliteActiveNurseStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def get_active_nurse(self, doctor_id: int) -> int | None:
        row = self._conn.execute(
            "SELECT nurse_id FROM active_nurse WHERE doctor_id = ?",
            (doctor_id,),
        ).fetchone()
        return row["nurse_id"] if row else None

    def set_active_nurse(self, doctor_id: int, nurse_id: int) -> None:
        self._conn.execute(
            "INSERT INTO active_nurse (doctor_id, nurse_id) VALUES (?, ?) "
            "ON CONFLICT(doctor_id) DO UPDATE SET nurse_id = excluded.nurse_id",
            (doctor_id, nurse_id),
        )
        self._conn.commit()
