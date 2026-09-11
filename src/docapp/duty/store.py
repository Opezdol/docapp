"""Хранилище подприложения «Дежурства» на SQLite.

Собственная БД (data/duty/duty.db), основную docapp.db не трогаем.
Соединение и стиль — как в needs/store.py и storage/sqlite_store.py:
row_factory = sqlite3.Row, check_same_thread=False (FastAPI работает в пуле
потоков), PRAGMA foreign_keys = ON для каскадного удаления операций,
таймстемпы — ISO-строки datetime.now().isoformat().
"""

import sqlite3
from datetime import datetime
from pathlib import Path

from docapp.core.db import Schema, open_db

_SCHEMA = """
CREATE TABLE IF NOT EXISTS duty_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    base TEXT NOT NULL,
    shift_date TEXT NOT NULL,
    doctor_id INTEGER NOT NULL REFERENCES employees(id),
    status TEXT NOT NULL DEFAULT 'draft',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (base, shift_date, doctor_id)
);
CREATE TABLE IF NOT EXISTS duty_operations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER NOT NULL REFERENCES duty_reports(id) ON DELETE CASCADE,
    operation TEXT NOT NULL,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_duty_op_report ON duty_operations(report_id);
"""

SCHEMA_VERSION = 1

# Миграции: (версия_после_применения, название, [SQL...]). Базовая схема
# создаётся _SCHEMA выше; будущие изменения добавляются сюда.
_MIGRATIONS: list[tuple[int, str, list[str]]] = []


def _now() -> str:
    """Текущее время как ISO-строка (как в sqlite_store.py: isoformat())."""
    return datetime.now().isoformat()


def _connect(db_path: str | Path) -> sqlite3.Connection:
    """Открыть БД отчётов: подключение, PRAGMA, схема, миграции — внутри core.db."""
    return open_db(db_path, SCHEMA)


#: Схема модуля «Дежурства» для общего механизма БД (core.db).
SCHEMA = Schema(
    module="duty",
    sql=_SCHEMA,
    version=SCHEMA_VERSION,
    migrations=_MIGRATIONS,
)


class SqliteDutyStore:
    """Отчёты о дежурствах и их операции в SQLite-файле."""

    def __init__(self, db_path: str | Path) -> None:
        self._conn = _connect(db_path)

    def close_conn(self) -> None:
        """Закрыть соединение (имя не `close` — см. замечание в needs/store.py)."""
        self._conn.close()

    def __enter__(self) -> "SqliteDutyStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close_conn()

    # ── внутренние помощники ──────────────────────────────────────────

    def _load_operations(self, report_id: int) -> list[dict]:
        rows = self._conn.execute(
            "SELECT operation, start_time, end_time FROM duty_operations "
            "WHERE report_id = ? ORDER BY position, id",
            (report_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def _row_to_report(self, row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "base": row["base"],
            "shift_date": row["shift_date"],
            "doctor_id": row["doctor_id"],
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "operations": self._load_operations(row["id"]),
        }

    # ── отчёты ────────────────────────────────────────────────────────

    def get_report(self, base: str, shift_date: str, doctor_id: int) -> dict | None:
        """Отчёт по (base, shift_date, doctor_id) или None."""
        row = self._conn.execute(
            "SELECT * FROM duty_reports WHERE base = ? AND shift_date = ? AND doctor_id = ?",
            (base, shift_date, doctor_id),
        ).fetchone()
        return self._row_to_report(row) if row else None

    def save_report(
        self,
        base: str,
        shift_date: str,
        doctor_id: int,
        operations: list[dict],
        status: str = "draft",
    ) -> int:
        """Создать или обновить отчёт (upsert по UNIQUE(base, shift_date, doctor_id)).

        Существующий отчёт обновляется (status, updated_at), его операции
        ЗАМЕНЯЮТСЯ (DELETE + INSERT с порядком). Возвращает id отчёта.
        """
        now = _now()
        existing = self._conn.execute(
            "SELECT id FROM duty_reports WHERE base = ? AND shift_date = ? AND doctor_id = ?",
            (base, shift_date, doctor_id),
        ).fetchone()
        if existing:
            report_id = existing["id"]
            self._conn.execute(
                "UPDATE duty_reports SET status = ?, updated_at = ? WHERE id = ?",
                (status, now, report_id),
            )
            self._conn.execute(
                "DELETE FROM duty_operations WHERE report_id = ?", (report_id,)
            )
        else:
            cur = self._conn.execute(
                "INSERT INTO duty_reports "
                "(base, shift_date, doctor_id, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (base, shift_date, doctor_id, status, now, now),
            )
            report_id = cur.lastrowid
            assert report_id is not None  # INSERT только что прошёл
        self._conn.executemany(
            "INSERT INTO duty_operations (report_id, operation, start_time, end_time, position) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                (
                    report_id,
                    op["operation"],
                    op["start_time"],
                    op["end_time"],
                    i,
                )
                for i, op in enumerate(operations)
            ],
        )
        self._conn.commit()
        return report_id

    def set_status(self, report_id: int, status: str) -> None:
        """Сменить статус отчёта (обновляет updated_at)."""
        self._conn.execute(
            "UPDATE duty_reports SET status = ?, updated_at = ? WHERE id = ?",
            (status, _now(), report_id),
        )
        self._conn.commit()

    def list_reports(self, from_date: str, to_date: str, base: str | None = None) -> list[dict]:
        """Отчёты за диапазон дат смены (с операциями), свежие сверху.

        shift_date сравнивается лексикографически: формат ISO 'YYYY-MM-DD'.
        """
        sql = "SELECT * FROM duty_reports WHERE shift_date >= ? AND shift_date <= ?"
        params: list = [from_date, to_date]
        if base is not None:
            sql += " AND base = ?"
            params.append(base)
        sql += " ORDER BY shift_date, base, doctor_id"
        rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_report(r) for r in rows]

    def list_open(self) -> list[dict]:
        """Все незакрытые отчёты (draft/sent) — для ленивого авто-закрытия."""
        rows = self._conn.execute(
            "SELECT * FROM duty_reports WHERE status IN ('draft', 'sent') "
            "ORDER BY shift_date, base"
        ).fetchall()
        return [self._row_to_report(r) for r in rows]

    def close_shift(self, shift_date: str) -> None:
        """Закрыть все отчёты за смену (status → 'closed')."""
        self._conn.execute(
            "UPDATE duty_reports SET status = 'closed', updated_at = ? WHERE shift_date = ?",
            (_now(), shift_date),
        )
        self._conn.commit()
