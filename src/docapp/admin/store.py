"""Состояние разделов: какие включены (ADR-0030, шаг 1.4 ТЗ панели).

Своя таблица у панели одна: `admin_sections`. Строки нет — раздел включён;
это и есть состояние по умолчанию, поэтому миграция данных не нужна.

Выключение не трогает ни схему раздела, ни его данные, ни файлы: `docapp
migrate` и бэкап ходят по `MODULES` и о тумблере не знают (ADR-0016).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from docapp.core.db import Schema, open_db

_SCHEMA = """
CREATE TABLE IF NOT EXISTS admin_sections (
    module     TEXT PRIMARY KEY,
    enabled    INTEGER NOT NULL DEFAULT 1,
    changed_at TEXT NOT NULL DEFAULT '',
    changed_by INTEGER REFERENCES employees(id)
);

CREATE TABLE IF NOT EXISTS access_sections (
    role       TEXT NOT NULL,
    module     TEXT NOT NULL,
    enabled    INTEGER NOT NULL DEFAULT 1,
    changed_at TEXT NOT NULL DEFAULT '',
    changed_by INTEGER REFERENCES employees(id),
    PRIMARY KEY (role, module)
);
"""

#: Миграции схемы панели: только переходы со старых версий.
#:
#: v2 обязана существовать как миграция, даже если DDL её таблицу и так создаёт:
#: `ensure_schema` записывает новую версию только на шаге миграции, поэтому
#: «просто добавить таблицу в DDL» оставляет `schema_migrations` на v1 —
#: `docapp check` предупреждает, `migrate` пишет WARN при каждом прогоне.
#: Нашлось 11.11.2026 сразу после появления матрицы доступа.
_MIGRATIONS: list[tuple[int, str, list[str]]] = [
    (
        2,
        "add access_sections table for the role matrix",
        [
            "CREATE TABLE IF NOT EXISTS access_sections ("
            "role TEXT NOT NULL, "
            "module TEXT NOT NULL, "
            "enabled INTEGER NOT NULL DEFAULT 1, "
            "changed_at TEXT NOT NULL DEFAULT '', "
            "changed_by INTEGER REFERENCES employees(id), "
            "PRIMARY KEY (role, module))",
        ],
    ),
]

#: Схема панели: выключатель разделов и матрица «роль × раздел» (ADR-0033).
SCHEMA = Schema(
    module="admin", sql=_SCHEMA, version=2, migrations=_MIGRATIONS
)

#: Что лежит в таблице по разделу: включён, когда и кем изменено.
SectionState = tuple[bool, str, int | None]


def _now() -> str:
    """Момент изменения — ISO-строка, как таймстемпы остальных хранилищ."""
    return datetime.now().isoformat(timespec="seconds")


class SqliteSectionStore:
    """Состояние разделов в единой БД."""

    def __init__(self, db_path: str | Path) -> None:
        self._conn = open_db(db_path, SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "SqliteSectionStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def is_enabled(self, module: str) -> bool:
        """Раздел включён? Нет строки — да (состояние по умолчанию)."""
        row = self._conn.execute(
            "SELECT enabled FROM admin_sections WHERE module = ?", (module,)
        ).fetchone()
        return True if row is None else bool(row["enabled"])

    def state(self) -> dict[str, SectionState]:
        """Все записанные состояния: раздел → (включён, когда, кто)."""
        rows = self._conn.execute(
            "SELECT module, enabled, changed_at, changed_by FROM admin_sections"
        ).fetchall()
        return {
            row["module"]: (bool(row["enabled"]), row["changed_at"], row["changed_by"])
            for row in rows
        }

    def role_state(self) -> dict[tuple[str, str], bool]:
        """Состояние матрицы: (роль, раздел) → включён или нет.

        Строки нет — раздел для роли включён, то есть права роли из кода
        (ADR-0033). Поэтому миграция данных не нужна.
        """
        rows = self._conn.execute(
            "SELECT role, module, enabled FROM access_sections"
        ).fetchall()
        return {
            (row["role"], row["module"]): bool(row["enabled"]) for row in rows
        }

    def set_role_module(
        self, role: str, module: str, enabled: bool, changed_by: int | None
    ) -> None:
        """Записать «раздел включён или нет» для роли."""
        self._conn.execute(
            "INSERT INTO access_sections (role, module, enabled, changed_at, changed_by) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(role, module) DO UPDATE SET "
            "enabled = excluded.enabled, changed_at = excluded.changed_at, "
            "changed_by = excluded.changed_by",
            (role, module, 1 if enabled else 0, _now(), changed_by),
        )
        self._conn.commit()

    def set_enabled(self, module: str, enabled: bool, changed_by: int | None) -> None:
        """Записать состояние раздела; повторное выключение обновляет запись."""
        self._conn.execute(
            "INSERT INTO admin_sections (module, enabled, changed_at, changed_by) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(module) DO UPDATE SET "
            "enabled = excluded.enabled, changed_at = excluded.changed_at, "
            "changed_by = excluded.changed_by",
            (module, 1 if enabled else 0, _now(), changed_by),
        )
        self._conn.commit()
