"""Анестезии и «активная сестра»: схема и хранилища на SQLite.

Модуль `records` владеет записями об анестезиях и выбором сестры на смену
(ADR-4). Таблица сотрудников принадлежит модулю `people` — здесь на неё только
ссылки внешним ключом.

Схема появилась делением прежнего `storage/sqlite_store.py`. Номера версий и
миграции прежней основной схемы (`docapp`) сохранены под именем этого модуля,
чтобы история изменений не потерялась.

Абстрактных портов здесь нет: адаптер один, а порт без второй реализации —
гипотетический шов (ADR-0017). Интерфейс для соседей объявляет потребитель
(`distribution/service.RecordsInterface`), а не владелец данных.
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence

from docapp.core.db import Schema, open_db
from docapp.domain.anesthesia import Anesthesia

_SCHEMA = """
CREATE TABLE IF NOT EXISTS anesthesia (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    date           TEXT NOT NULL,
    patient_name   TEXT NOT NULL,
    doctor_id      INTEGER NOT NULL REFERENCES employees(id),
    nurse_id       INTEGER NOT NULL REFERENCES employees(id),
    created_at     TEXT NOT NULL,
    accrued_at     TEXT
);
CREATE TABLE IF NOT EXISTS active_nurse (
    doctor_id INTEGER PRIMARY KEY REFERENCES employees(id),
    nurse_id  INTEGER NOT NULL REFERENCES employees(id)
);
-- Итоги начислений за месяц — задел прежнего плана «Отчёт» (docs/ТЗ-отчёт.md,
-- файла больше нет). По ADR-0024 план отменён: таблица удаляется шагом 5
-- ТЗ `docs/ТЗ-распределение.md`.
CREATE TABLE IF NOT EXISTS accrual (
    employee_id INTEGER NOT NULL REFERENCES employees(id),
    month       TEXT NOT NULL,
    amount      TEXT NOT NULL,
    PRIMARY KEY (employee_id, month)
);
"""

#: Версия схемы записей: прежняя основная схема шла v1 → v2 → v3.
SCHEMA_VERSION = 3

_MIGRATIONS: list[tuple[int, str, list[str]]] = [
    # (1, "initial schema", [])  # базовая схема создаётся _SCHEMA выше
    # v2: убрать номер истории болезни (минимизация данных, 152-ФЗ).
    # Пересборка таблицы (CREATE/INSERT/DROP/RENAME) вместо
    # ALTER TABLE … DROP COLUMN: последний требует SQLite ≥ 3.35,
    # а на shared-хостинге reg.ru стоит более старый. Пересборка
    # стирает и колонку, и старые значения.
    (
        2,
        "drop history_number from anesthesia",
        [
            "CREATE TABLE anesthesia_new ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "date TEXT NOT NULL, "
            "patient_name TEXT NOT NULL, "
            "doctor_id INTEGER NOT NULL REFERENCES employees(id), "
            "nurse_id INTEGER NOT NULL REFERENCES employees(id), "
            "created_at TEXT NOT NULL"
            ")",
            "INSERT INTO anesthesia_new "
            "(id, date, patient_name, doctor_id, nurse_id, created_at) "
            "SELECT id, date, patient_name, doctor_id, nurse_id, created_at "
            "FROM anesthesia",
            "DROP TABLE anesthesia",
            "ALTER TABLE anesthesia_new RENAME TO anesthesia",
        ],
    ),
    # v3: задел под прежний план «Отчёт» (начисления): флаг на записи и итоги за
    # месяц. План отменён ADR-0024 — флаг `accrued_at` меняет смысл на «учтена в
    # распределении», таблица `accrual` удаляется отдельной миграцией
    # (`docs/ТЗ-распределение.md`, шаг 5). Миграция оставлена как есть: она уже
    # применена на живой базе, переписывать применённые миграции нельзя.
    (
        3,
        "add accrual table and accrued_at column",
        [
            "ALTER TABLE anesthesia ADD COLUMN accrued_at TEXT",
            "CREATE TABLE IF NOT EXISTS accrual ("
            "employee_id INTEGER NOT NULL REFERENCES employees(id), "
            "month TEXT NOT NULL, "
            "amount TEXT NOT NULL, "
            "PRIMARY KEY (employee_id, month))",
        ],
    ),
]

SCHEMA = Schema(module="records", sql=_SCHEMA, version=SCHEMA_VERSION, migrations=_MIGRATIONS)


def _connect(db_path: str | Path) -> sqlite3.Connection:
    """Открыть БД: подключение, PRAGMA, схема модуля, миграции — внутри core.db."""
    return open_db(db_path, SCHEMA)


def _moment(value: str | None) -> datetime | None:
    """Момент времени из БД: пусто — None, иначе UTC (как пишет `created_at`)."""
    if not value:
        return None
    moment = datetime.fromisoformat(value)
    if moment.utcoffset() == timedelta(0):
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


class SqliteAnesthesiaStore:
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

    def list_range(
        self,
        from_date: date,
        to_date: date,
        *,
        doctor_id: int | None = None,
        nurse_id: int | None = None,
    ) -> list[Anesthesia]:
        sql = "SELECT * FROM anesthesia WHERE date >= ? AND date <= ?"
        params: list = [from_date.isoformat(), to_date.isoformat()]
        if doctor_id is not None:
            sql += " AND doctor_id = ?"
            params.append(doctor_id)
        if nurse_id is not None:
            sql += " AND nurse_id = ?"
            params.append(nurse_id)
        sql += " ORDER BY date DESC, id DESC"
        rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_anesthesia(r) for r in rows]

    def remark_accrued(
        self,
        from_date: date,
        to_date: date,
        anesthesia_ids: Sequence[int],
        moment: datetime,
    ) -> tuple[int, int]:
        """Пересчитать метки «учтена» за период: снять прежние и поставить новые.

        Одна транзакция: либо пересчёт применился целиком, либо период остался как
        был. Метка принадлежит прогону месяца, поэтому `anesthesia_ids` вне границ
        периода игнорируются. Возвращает (снято, поставлено).
        """
        first, last = from_date.isoformat(), to_date.isoformat()
        cleared = self._conn.execute(
            "UPDATE anesthesia SET accrued_at = NULL "
            "WHERE date >= ? AND date <= ? AND accrued_at IS NOT NULL",
            (first, last),
        ).rowcount

        marked = 0
        ids = [int(anesthesia_id) for anesthesia_id in anesthesia_ids]
        if ids:
            placeholders = ", ".join("?" * len(ids))
            marked = self._conn.execute(
                f"UPDATE anesthesia SET accrued_at = ? "
                f"WHERE id IN ({placeholders}) AND date >= ? AND date <= ?",
                (moment.isoformat(), *ids, first, last),
            ).rowcount
        self._conn.commit()
        return cleared, marked

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
        created_at = _moment(row["created_at"])
        if created_at is None:      # NOT NULL в схеме: None тут — испорченная строка
            raise ValueError(f"У записи {row['id']} нет created_at")
        return Anesthesia(
            id=row["id"],
            date=date.fromisoformat(row["date"]),
            patient_name=row["patient_name"],
            doctor_id=row["doctor_id"],
            nurse_id=row["nurse_id"],
            created_at=created_at,
            accrued_at=_moment(row["accrued_at"]),
        )


class SqliteActiveNurseStore:
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
