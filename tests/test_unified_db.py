"""Тесты единой БД (ADR-0016) — критерий шага 4.

Проверяется не «код запустился», а свойства единой базы: файл один, таблицы
модулей живут в нём с префиксами владельца, ссылки на сотрудников — настоящие
внешние ключи (и они включены), а версии схем учтены по каждому модулю.
"""

import sqlite3

import pytest

from docapp.core.db import SCHEMA_TABLE, Schema, open_db
from docapp.modules import MODULES


def _open_all(db) -> sqlite3.Connection:
    """Открыть единую БД так, как это делает приложение: по схеме каждого модуля."""
    conn = None
    for module in MODULES:
        if module.schema is None:
            continue
        if conn is not None:
            conn.close()
        conn = open_db(db, module.schema)
    assert conn is not None
    return conn


def _tables(conn) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {r["name"] for r in rows}


class TestOneFile:
    """Все модули живут в одном файле."""

    def test_all_module_tables_in_one_file(self, tmp_path):
        db = tmp_path / "docapp.db"
        conn = _open_all(db)
        try:
            tables = _tables(conn)
        finally:
            conn.close()

        # ядро — без префикса
        assert {"employees", "anesthesia", "active_nurse", "accrual"} <= tables
        # модули — с префиксом владельца
        assert {"needs_requests", "needs_request_lines", "needs_closures"} <= tables
        assert {"duty_reports", "duty_operations"} <= tables
        assert {"wiki_sources", "wiki_articles", "wiki_revisions"} <= tables

    def test_old_table_names_are_gone(self, tmp_path):
        """Прежние имена (requests/closures/duty_report) в новой БД не создаются."""
        conn = _open_all(tmp_path / "docapp.db")
        try:
            tables = _tables(conn)
        finally:
            conn.close()
        assert "requests" not in tables
        assert "closures" not in tables
        assert "duty_report" not in tables
        assert "articles" not in tables

    def test_version_row_per_module(self, tmp_path):
        conn = _open_all(tmp_path / "docapp.db")
        try:
            rows = conn.execute(
                f"SELECT module, version FROM {SCHEMA_TABLE} ORDER BY module"
            ).fetchall()
        finally:
            conn.close()
        assert [(r["module"], r["version"]) for r in rows] == [
            ("duty", 1),
            ("needs", 2),
            ("people", 1),
            ("records", 3),
            ("wiki", 2),
        ]

    def test_schema_of_one_module_does_not_touch_another(self, tmp_path):
        """Открытие схемы модуля не создаёт чужие таблицы."""
        from docapp.duty.store import SCHEMA as DUTY_SCHEMA

        conn = open_db(tmp_path / "docapp.db", DUTY_SCHEMA)
        try:
            tables = _tables(conn)
        finally:
            conn.close()
        assert {"duty_reports", "duty_operations"} <= tables
        assert "employees" not in tables


class TestForeignKeys:
    """Ссылки на сотрудников — настоящие внешние ключи, и они работают."""

    @pytest.mark.parametrize(
        "table,column",
        [
            ("needs_requests", "author_id"),
            ("needs_closures", "closed_by"),
            ("duty_reports", "doctor_id"),
            ("wiki_sources", "uploaded_by"),
            ("wiki_articles", "created_by"),
            ("wiki_revisions", "edited_by"),
            ("wiki_messages", "employee_id"),
            ("anesthesia", "doctor_id"),
            ("anesthesia", "nurse_id"),
            ("active_nurse", "nurse_id"),
            ("accrual", "employee_id"),
        ],
    )
    def test_declared(self, tmp_path, table, column):
        conn = _open_all(tmp_path / "docapp.db")
        try:
            keys = conn.execute(f"PRAGMA foreign_key_list({table})").fetchall()
        finally:
            conn.close()
        pairs = {(k["from"], k["table"], k["to"]) for k in keys}
        assert (column, "employees", "id") in pairs, f"{table}.{column}: {pairs}"

    def test_enforced_on_open(self, tmp_path):
        """PRAGMA foreign_keys включён: ссылка на несуществующего сотрудника — отказ."""
        conn = _open_all(tmp_path / "docapp.db")
        try:
            assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO needs_requests "
                    "(base, point, week_start, category, author_id, status, "
                    " created_at, updated_at) "
                    "VALUES ('База', 'Точка', '2026-08-10', 'solutions', 999, "
                    "'draft', 't', 't')"
                )
        finally:
            conn.close()

    def test_cascade_from_requests_to_lines(self, tmp_path):
        """Строки заявки уходят вместе с заявкой (ON DELETE CASCADE)."""
        conn = _open_all(tmp_path / "docapp.db")
        try:
            conn.execute(
                "INSERT INTO employees (last_name, first_name, role) VALUES ('И', 'И', 'nurse')"
            )
            employee_id = conn.execute("SELECT id FROM employees").fetchone()["id"]
            conn.execute(
                "INSERT INTO needs_requests "
                "(base, point, week_start, category, author_id, status, created_at, updated_at) "
                "VALUES ('База', 'Точка', '2026-08-10', 'solutions', ?, 'draft', 't', 't')",
                (employee_id,),
            )
            request_id = conn.execute("SELECT id FROM needs_requests").fetchone()["id"]
            conn.execute(
                "INSERT INTO needs_request_lines (request_id, item, unit, grp, qty, position) "
                "VALUES (?, 'Рингер', 'фл', 'Растворы', 1, 0)",
                (request_id,),
            )
            conn.commit()
            conn.execute("DELETE FROM needs_requests WHERE id = ?", (request_id,))
            conn.commit()
            left = conn.execute("SELECT COUNT(*) AS c FROM needs_request_lines").fetchone()["c"]
        finally:
            conn.close()
        assert left == 0


class TestSchemaObjects:
    """Схемы модулей — обычные объекты core.db.Schema: их видно и можно мигрировать."""

    def test_every_schema_has_name_and_sql(self):
        for module in MODULES:
            schema = module.schema
            if schema is None:
                continue
            assert isinstance(schema, Schema)
            assert schema.module == module.name
            assert schema.sql.strip()
