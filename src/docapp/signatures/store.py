"""Хранилище подписей сотрудников (ТЗ-кровь §6, ADR-0029).

Подпись одна на сотрудника: повторная загрузка заменяет прежнюю строку и тот же
файл (`<id>.png`), истории версий нет — она никому не нужна и хранила бы чужие
подписи зря.

Файл лежит на диске, в базе только имя и размер (как у PDF-источников «Чата»,
ADR-0020): подпись — картинка, а не строка. Имя файла — идентификатор сотрудника,
поэтому «моя подпись» и «подпись заведующего» — это две строки и два файла,
и перепутать их нельзя.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from docapp.core.db import Schema, open_db

_SCHEMA = """
CREATE TABLE IF NOT EXISTS signatures (
    employee_id INTEGER PRIMARY KEY REFERENCES employees(id),
    file_name TEXT NOT NULL,
    size INTEGER NOT NULL,
    uploaded_at TEXT NOT NULL
);
"""

SCHEMA_VERSION = 1

#: Миграции: (версия после применения, название, [SQL…]). Базовая схема — выше.
_MIGRATIONS: list[tuple[int, str, list[str]]] = []

#: Схема модуля для общего механизма БД (core.db).
SCHEMA = Schema(
    module="signatures",
    sql=_SCHEMA,
    version=SCHEMA_VERSION,
    migrations=_MIGRATIONS,
)


@dataclass(frozen=True)
class Signature:
    """Загруженная подпись: чья, какой файл, сколько весит и когда загружена."""

    employee_id: int
    file_name: str
    size: int
    uploaded_at: str


def _now() -> str:
    """Текущее время ISO-строкой — как таймстемпы остальных хранилищ."""
    return datetime.now().isoformat()


class SqliteSignatureStore:
    """Подписи сотрудников в единой БД приложения."""

    def __init__(self, db_path: str | Path) -> None:
        self._conn: sqlite3.Connection = open_db(db_path, SCHEMA)

    def get(self, employee_id: int) -> Signature | None:
        """Подпись сотрудника или None, если он её ещё не загрузил."""
        row = self._conn.execute(
            "SELECT employee_id, file_name, size, uploaded_at "
            "FROM signatures WHERE employee_id = ?",
            (employee_id,),
        ).fetchone()
        if row is None:
            return None
        return Signature(
            employee_id=row["employee_id"],
            file_name=row["file_name"],
            size=row["size"],
            uploaded_at=row["uploaded_at"],
        )

    def save(self, employee_id: int, file_name: str, size: int) -> Signature:
        """Записать подпись сотрудника, заменив прежнюю (одна на человека)."""
        uploaded_at = _now()
        self._conn.execute(
            "INSERT INTO signatures (employee_id, file_name, size, uploaded_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(employee_id) DO UPDATE SET "
            "file_name = excluded.file_name, size = excluded.size, "
            "uploaded_at = excluded.uploaded_at",
            (employee_id, file_name, size, uploaded_at),
        )
        self._conn.commit()
        return Signature(
            employee_id=employee_id,
            file_name=file_name,
            size=size,
            uploaded_at=uploaded_at,
        )

    def delete(self, employee_id: int) -> None:
        """Убрать строку подписи (файл удаляет сервис)."""
        self._conn.execute("DELETE FROM signatures WHERE employee_id = ?", (employee_id,))
        self._conn.commit()

    def close(self) -> None:
        """Закрыть соединение (тесты и завершение работы)."""
        self._conn.close()
