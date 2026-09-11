"""Тесты общего механизма БД (core/db.py): схема, версии, миграции.

Ключевое, что здесь проверяется, — безопасность для баз, созданных прежним
кодом: у них версия лежала только в PRAGMA user_version, и движок обязан
перенести её в таблицу schema_migrations, а не прогнать миграции заново.

Две схемы в тестах отражают договорённость движка: `SCHEMA_V1` — как выглядела
схема у прежнего кода (DDL на версию 1, миграций нет), `SCHEMA_CURRENT` — как
объявляет схему модуль сегодня: DDL на текущую версию плюс переходы со старых.
"""

import sqlite3

from docapp.core.db import (
    LEGACY_NOTE,
    SCHEMA_TABLE,
    Schema,
    applied_version,
    init_version_table,
    migrate,
    open_db,
)

_SQL_V1 = """
CREATE TABLE IF NOT EXISTS items (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL
);
"""

_SQL_CURRENT = """
CREATE TABLE IF NOT EXISTS items (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS tags (
    id      INTEGER PRIMARY KEY,
    item_id INTEGER
);
"""

#: Так схему объявлял прежний код: DDL на версию 1, никаких переходов.
SCHEMA_V1 = Schema(module="test", sql=_SQL_V1, version=1)

#: Так объявляет схему модуль сегодня: текущий DDL + переходы со старых версий.
SCHEMA_CURRENT = Schema(
    module="test",
    sql=_SQL_CURRENT,
    version=3,
    migrations=(
        (2, "add column note", ["ALTER TABLE items ADD COLUMN note TEXT NOT NULL DEFAULT ''"]),
        (3, "add table tags", ["CREATE TABLE tags (id INTEGER PRIMARY KEY, item_id INTEGER)"]),
    ),
)


def _seed_v1(db) -> None:
    """Посеять БД так, как её создавал прежний код: DDL + PRAGMA user_version."""
    conn = sqlite3.connect(db)
    conn.executescript(_SQL_V1)
    conn.execute("PRAGMA user_version = 1")
    conn.execute("INSERT INTO items (name) VALUES ('Атропин')")
    conn.commit()
    conn.close()


def _versions(conn) -> list[tuple[int, str]]:
    rows = conn.execute(
        f"SELECT version, note FROM {SCHEMA_TABLE} WHERE module = 'test' ORDER BY version"
    ).fetchall()
    return [(r["version"], r["note"]) for r in rows]


def _columns(conn, table: str) -> set[str]:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _tables(conn) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {r["name"] for r in rows}


class TestNewDatabase:
    """Новая БД получает текущую версию по DDL, миграции не выполняются."""

    def test_records_current_version(self, tmp_path):
        conn = open_db(tmp_path / "new.db", SCHEMA_CURRENT)
        try:
            assert applied_version(conn, "test") == 3
            assert _versions(conn) == [(3, "initial schema")]
        finally:
            conn.close()

    def test_mirrors_user_version(self, tmp_path):
        conn = open_db(tmp_path / "new.db", SCHEMA_CURRENT)
        try:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
        finally:
            conn.close()

    def test_creates_current_schema(self, tmp_path):
        conn = open_db(tmp_path / "new.db", SCHEMA_CURRENT)
        try:
            assert _columns(conn, "items") == {"id", "name", "note"}
            assert "tags" in _tables(conn)
        finally:
            conn.close()

    def test_schema_without_sql(self, tmp_path):
        """Модуль без своих таблиц (например «Распределение») тоже учитывается."""
        conn = open_db(tmp_path / "new.db", Schema(module="empty", sql="", version=1))
        try:
            assert applied_version(conn, "empty") == 1
        finally:
            conn.close()


class TestMigrations:
    """Миграции применяются по возрастанию и записываются в таблицу."""

    def test_applies_pending_in_order(self, tmp_path):
        db = tmp_path / "old.db"
        _seed_v1(db)

        conn = open_db(db, SCHEMA_CURRENT)
        try:
            assert applied_version(conn, "test") == 3
            assert _columns(conn, "items") == {"id", "name", "note"}
            assert "tags" in _tables(conn)
            assert [note for _, note in _versions(conn)] == [
                LEGACY_NOTE,
                "add column note",
                "add table tags",
            ]
            # данные прежней версии сохранены
            assert conn.execute("SELECT name FROM items").fetchone()["name"] == "Атропин"
        finally:
            conn.close()

    def test_second_open_is_idempotent(self, tmp_path):
        db = tmp_path / "old.db"
        _seed_v1(db)

        first = open_db(db, SCHEMA_CURRENT)
        first.close()
        second = open_db(db, SCHEMA_CURRENT)
        try:
            # повторное открытие ничего не дописывает и не падает
            assert _versions(second) == [
                (1, LEGACY_NOTE),
                (2, "add column note"),
                (3, "add table tags"),
            ]
            assert applied_version(second, "test") == 3
        finally:
            second.close()

    def test_applied_migration_not_rerun(self, tmp_path):
        """Опубликованная миграция не применяется второй раз."""
        db = tmp_path / "db.db"
        open_db(db, SCHEMA_CURRENT).close()

        conn = sqlite3.connect(db)
        conn.execute("INSERT INTO items (name, note) VALUES ('свой', 'x')")
        conn.commit()
        conn.close()

        conn = open_db(db, SCHEMA_CURRENT)
        try:
            row = conn.execute("SELECT name, note FROM items").fetchone()
            assert (row["name"], row["note"]) == ("свой", "x")
        finally:
            conn.close()


class TestMigrateCommand:
    """migrate(path, schema) — то, чем пользуется `docapp migrate`."""

    def test_creates_and_reports_version(self, tmp_path):
        db = tmp_path / "nested" / "dir" / "db.db"
        db.parent.mkdir(parents=True, exist_ok=True)
        assert migrate(db, SCHEMA_CURRENT) == 3

    def test_old_database_reaches_current_version(self, tmp_path):
        db = tmp_path / "old.db"
        _seed_v1(db)

        assert migrate(db, SCHEMA_CURRENT) == 3
        conn = sqlite3.connect(db)
        try:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
            names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
            assert "tags" in names
        finally:
            conn.close()


class TestBootstrapFromUserVersion:
    """Перенос версии из старой БД — самый хрупкий путь движка."""

    def test_bootstrap_records_legacy_note(self, tmp_path):
        db = tmp_path / "old.db"
        _seed_v1(db)

        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        try:
            init_version_table(conn)
            assert applied_version(conn, "test") == 1
            assert _versions(conn) == [(1, LEGACY_NOTE)]
        finally:
            conn.close()

    def test_new_database_has_no_legacy_note(self, tmp_path):
        conn = open_db(tmp_path / "new.db", SCHEMA_CURRENT)
        try:
            assert all(note != LEGACY_NOTE for _, note in _versions(conn))
        finally:
            conn.close()

    def test_unknown_module_gets_zero(self, tmp_path):
        conn = open_db(tmp_path / "new.db", SCHEMA_V1)
        try:
            assert applied_version(conn, "other") == 0
        finally:
            conn.close()

    def test_legacy_version_is_not_shared_between_modules(self, tmp_path):
        """user_version — одно число на файл, и чужому модулю оно не достаётся.

        Так база со временем и станет общей (ADR-0016): первый открытый модуль
        забирает старую версию себе, второй в том же файле стартует с нуля и
        создаёт свою схему.
        """
        db = tmp_path / "shared.db"
        _seed_v1(db)

        first = open_db(db, SCHEMA_V1)
        try:
            assert applied_version(first, "test") == 1
        finally:
            first.close()

        other = open_db(db, Schema(module="other", sql="CREATE TABLE IF NOT EXISTS b (id INTEGER)", version=1))
        try:
            assert applied_version(other, "test") == 1
            assert applied_version(other, "other") == 1
            assert "b" in _tables(other)
        finally:
            other.close()
