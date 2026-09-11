"""Единый механизм SQLite: подключение, схема, миграции, версии (ADR-0016).

Один движок на всё приложение вместо четырёх копий `_connect` и
`_apply_migrations`, которые жили в `storage/sqlite_store.py`, `needs/store.py`,
`duty/store.py` и `wiki/store.py`.

Модуль объявляет о своей схеме одним объектом `Schema`:

    SCHEMA = Schema(module="needs", sql=_SCHEMA, version=2, migrations=_MIGRATIONS)

и получает соединение через `open_db(path, SCHEMA)`. Всё остальное — PRAGMA,
создание таблиц, порядок миграций, учёт версий — внутри.

Версия схемы хранится в таблице `schema_migrations` строкой на модуль:
модулей в одной БД со временем станет несколько, а `PRAGMA user_version` —
одно число на файл, поэтому источником правды таблица, а `user_version`
дублируется справочно (его читают старые скрипты и откат кода).

**Перенос версии из старой БД.** Базы, созданные прежним кодом, версию хранят
только в `PRAGMA user_version` и строки в `schema_migrations` не имеют. При
первом открытии такая версия переносится в таблицу (note «перенесено из
PRAGMA user_version») и помечается применённой — иначе движок счёл бы базу
новой и прогнал миграции заново поверх уже мигрированных данных.

Правила:
- `sql` описывает **текущую** схему (на версию `version`), а `migrations` — только
  переходы со старых версий. Новая БД создаётся по `sql`, и миграции на ней не
  выполняются: иначе переход «со старой схемы» применился бы к уже новой;
- опубликованные миграции не редактируются, только добавляются новые;
  применяются по возрастанию версии.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Sequence

#: Таблица учёта применённых миграций (по строке на модуль и версию).
SCHEMA_TABLE = "schema_migrations"

#: Отметка о версии, перенесённой из PRAGMA user_version старой БД.
LEGACY_NOTE = "перенесено из PRAGMA user_version"

_SCHEMA_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {SCHEMA_TABLE} (
    module     TEXT NOT NULL,
    version    INTEGER NOT NULL,
    applied_at TEXT NOT NULL,
    note       TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (module, version)
);
"""


@dataclass(frozen=True)
class Schema:
    """Схема модуля: имя, DDL, текущая версия и миграции.

    module — имя модуля (оно же ключ в schema_migrations);
    sql — DDL **текущей** схемы, идемпотентный (CREATE TABLE IF NOT EXISTS);
    version — текущая версия схемы (её получает новая БД);
    migrations — пары (версия_после_применения, описание, [SQL]) по возрастанию,
    только переходы со старых версий.
    """

    module: str
    sql: str
    version: int
    migrations: Sequence[tuple[int, str, Sequence[str]]] = ()


def _now() -> str:
    """Момент записи версии — ISO-строка (как таймстемпы остальных хранилищ)."""
    return datetime.now().isoformat(timespec="seconds")


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Подключиться к файлу SQLite с нужными приложению PRAGMA.

    check_same_thread=False: FastAPI обрабатывает запросы в пуле потоков,
    соединение не может быть привязано к одному потоку.
    """
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def init_version_table(conn: sqlite3.Connection) -> None:
    """Создать таблицу учёта версий, если её ещё нет."""
    conn.executescript(_SCHEMA_TABLE_SQL)


def applied_version(conn: sqlite3.Connection, module: str) -> int:
    """Версия схемы модуля из таблицы; нет строки — переносит из user_version.

    Перенос делается только для базы, которую прежний код вёл целиком (ни одной
    строки ни по одному модулю): `PRAGMA user_version` — одно число на файл, и
    подставлять его чужому модулю нельзя. Для новой БД (и для модуля, которого
    в этой БД ещё нет) возвращается 0.
    """
    row = conn.execute(
        f"SELECT MAX(version) AS v FROM {SCHEMA_TABLE} WHERE module = ?",
        (module,),
    ).fetchone()
    if row is not None and row["v"] is not None:
        return int(row["v"])

    known = conn.execute(f"SELECT COUNT(*) AS n FROM {SCHEMA_TABLE}").fetchone()["n"]
    if known:
        return 0

    legacy = int(conn.execute("PRAGMA user_version").fetchone()[0] or 0)
    if legacy > 0:
        # Старая БД: версия была только в user_version — фиксируем её в таблице,
        # иначе движок счёл бы базу новой и повторил миграции.
        _record(conn, module, legacy, LEGACY_NOTE)
    return legacy


def _record(conn: sqlite3.Connection, module: str, version: int, note: str) -> None:
    """Записать применённую версию и продублировать её в PRAGMA user_version."""
    conn.execute(
        f"INSERT OR REPLACE INTO {SCHEMA_TABLE} (module, version, applied_at, note) "
        f"VALUES (?, ?, ?, ?)",
        (module, version, _now(), note),
    )
    conn.execute(f"PRAGMA user_version = {int(version)}")
    conn.commit()


def ensure_schema(conn: sqlite3.Connection, schema: Schema) -> int:
    """Довести схему модуля до schema.version, вернуть итоговую версию.

    Новая БД (версия 0) создаётся по текущему DDL и миграции не проходит —
    переходы описывают только старые схемы. Старая БД идёт через миграции, а
    текущий DDL выполняется после них как страховка: объекты, добавленные в
    схему без миграции, всё равно появятся (`CREATE ... IF NOT EXISTS`), а
    объекты, созданные миграцией, повторно не создаются.
    """
    version = applied_version(conn, schema.module)
    if version == 0:
        conn.executescript(schema.sql)
        _record(conn, schema.module, schema.version, "initial schema")
        return schema.version

    for target, note, statements in schema.migrations:
        if target <= version:
            continue
        for statement in statements:
            conn.execute(statement)
        _record(conn, schema.module, target, note)
        version = target

    conn.executescript(schema.sql)
    return version


def open_db(db_path: str | Path, schema: Schema) -> sqlite3.Connection:
    """Открыть БД: подключение, таблица версий, схема модуля, миграции."""
    conn = connect(db_path)
    init_version_table(conn)
    ensure_schema(conn, schema)
    conn.commit()
    return conn


def migrate(db_path: str | Path, schema: Schema) -> int:
    """Применить миграции модуля к файлу и вернуть итоговую версию схемы.

    Приложение (и CLI) ждать первого запроса не обязано: соединение открывается
    и закрывается здесь же.
    """
    conn = open_db(db_path, schema)
    try:
        return applied_version(conn, schema.module)
    finally:
        conn.close()
