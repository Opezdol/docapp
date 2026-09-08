"""Тесты хранилища подприложения «Потребности» (SqliteNeedsStore)."""

import sqlite3

import pytest

from docapp.needs.catalog import CATEGORY_MEDICAMENTS, CATEGORY_SOLUTIONS
from docapp.needs.store import SCHEMA_VERSION, SqliteNeedsStore


@pytest.fixture
def store(tmp_path):
    s = SqliteNeedsStore(tmp_path / "needs.db")
    yield s
    s.close_conn()


def make_line(item, qty, unit="", grp=""):
    """Строка заявки с заполненными по умолчанию unit/grp."""
    return {"item": item, "unit": unit, "grp": grp, "qty": qty}


class TestSaveAndGet:
    def test_save_and_get_roundtrip(self, store):
        lines = [
            make_line("Набор А", 5, unit="шт", grp="Группа 1"),
            make_line("Набор Б", 3, unit="уп", grp="Группа 2"),
        ]
        rid = store.save_request(
            "База-1", "Точка-1", CATEGORY_MEDICAMENTS, "2026-08-10", 7, lines=lines
        )

        req = store.get_request("База-1", "Точка-1", CATEGORY_MEDICAMENTS, "2026-08-10")
        assert req is not None
        assert req["id"] == rid
        assert req["base"] == "База-1"
        assert req["point"] == "Точка-1"
        assert req["week_start"] == "2026-08-10"
        assert req["category"] == CATEGORY_MEDICAMENTS
        assert req["author_id"] == 7
        assert req["status"] == "draft"
        assert req["created_at"]
        assert req["updated_at"] == req["created_at"]
        assert req["lines"] == lines

    def test_get_missing_returns_none(self, store):
        assert (
            store.get_request("База-1", "Точка-1", CATEGORY_SOLUTIONS, "2026-08-10")
            is None
        )

    def test_empty_lines_saved(self, store):
        rid = store.save_request(
            "База-1", "Точка-1", CATEGORY_SOLUTIONS, "2026-08-10", 7, lines=[]
        )
        req = store.get_request("База-1", "Точка-1", CATEGORY_SOLUTIONS, "2026-08-10")
        assert req is not None
        assert req["id"] == rid
        assert req["lines"] == []


class TestUpsert:
    def test_second_save_updates_not_duplicates(self, store):
        rid1 = store.save_request(
            "База-1", "Точка-1", CATEGORY_MEDICAMENTS, "2026-08-10", 7,
            lines=[make_line("А", 1)],
        )
        first = store.get_request("База-1", "Точка-1", CATEGORY_MEDICAMENTS, "2026-08-10")

        rid2 = store.save_request(
            "База-1", "Точка-1", CATEGORY_MEDICAMENTS, "2026-08-10", 9,
            lines=[make_line("Б", 2), make_line("В", 4)],
            status="sent",
        )
        assert rid2 == rid1

        rows = store._conn.execute("SELECT COUNT(*) AS c FROM requests").fetchone()
        assert rows["c"] == 1

        req = store.get_request("База-1", "Точка-1", CATEGORY_MEDICAMENTS, "2026-08-10")
        assert req["author_id"] == 9
        assert req["status"] == "sent"
        assert req["lines"] == [make_line("Б", 2), make_line("В", 4)]
        assert req["created_at"] == first["created_at"]
        assert req["updated_at"] != first["updated_at"]

    def test_different_point_is_separate_request(self, store):
        rid1 = store.save_request(
            "База-1", "Точка-1", CATEGORY_MEDICAMENTS, "2026-08-10", 7,
            lines=[make_line("А", 1)],
        )
        rid2 = store.save_request(
            "База-1", "Точка-2", CATEGORY_MEDICAMENTS, "2026-08-10", 7,
            lines=[make_line("Б", 2)],
        )
        assert rid2 != rid1
        rows = store._conn.execute("SELECT COUNT(*) AS c FROM requests").fetchone()
        assert rows["c"] == 2

    def test_same_point_different_category_is_separate(self, store):
        """Одна точка за неделю держит до двух заявок — по одной на раздел."""
        rid1 = store.save_request(
            "База-1", "Точка-1", CATEGORY_SOLUTIONS, "2026-08-10", 7,
            lines=[make_line("А", 1)],
        )
        rid2 = store.save_request(
            "База-1", "Точка-1", CATEGORY_MEDICAMENTS, "2026-08-10", 7,
            lines=[make_line("Б", 2)],
        )
        assert rid2 != rid1
        rows = store._conn.execute("SELECT COUNT(*) AS c FROM requests").fetchone()
        assert rows["c"] == 2
        assert store.get_request("База-1", "Точка-1", CATEGORY_SOLUTIONS, "2026-08-10")["lines"] == [
            make_line("А", 1)
        ]
        assert store.get_request("База-1", "Точка-1", CATEGORY_MEDICAMENTS, "2026-08-10")["lines"] == [
            make_line("Б", 2)
        ]

    def test_set_status(self, store):
        rid = store.save_request(
            "База-1", "Точка-1", CATEGORY_MEDICAMENTS, "2026-08-10", 7, lines=[]
        )
        store.set_status(rid, "sent")
        req = store.get_request("База-1", "Точка-1", CATEGORY_MEDICAMENTS, "2026-08-10")
        assert req["status"] == "sent"


class TestClosures:
    def test_close_reopen(self, store):
        assert store.is_closed("База-1", CATEGORY_SOLUTIONS, "2026-08-10") is False
        store.close("База-1", CATEGORY_SOLUTIONS, "2026-08-10", closed_by=7)
        assert store.is_closed("База-1", CATEGORY_SOLUTIONS, "2026-08-10") is True
        store.close("База-1", CATEGORY_SOLUTIONS, "2026-08-10", closed_by=8)  # повторный close не падает
        assert store.is_closed("База-1", CATEGORY_SOLUTIONS, "2026-08-10") is True
        store.reopen("База-1", CATEGORY_SOLUTIONS, "2026-08-10")
        assert store.is_closed("База-1", CATEGORY_SOLUTIONS, "2026-08-10") is False

    def test_close_one_category_does_not_touch_other(self, store):
        """Закрытие раздела не трогает другой раздел той же базы (ТЗ F7)."""
        store.close("База-1", CATEGORY_SOLUTIONS, "2026-08-10", closed_by=7)
        assert store.is_closed("База-1", CATEGORY_SOLUTIONS, "2026-08-10") is True
        assert store.is_closed("База-1", CATEGORY_MEDICAMENTS, "2026-08-10") is False

    def test_list_closures(self, store):
        store.close("База-1", CATEGORY_SOLUTIONS, "2026-08-10", closed_by=7)
        store.close("База-2", CATEGORY_MEDICAMENTS, "2026-08-17", closed_by=9)
        closures = store.list_closures()
        assert len(closures) == 2
        assert {c["base"] for c in closures} == {"База-1", "База-2"}
        assert {c["closed_by"] for c in closures} == {7, 9}
        assert all(c["closed_at"] for c in closures)


class TestLists:
    def test_list_requests_filters_by_base_week_category(self, store):
        store.save_request(
            "База-1", "Точка-1", CATEGORY_MEDICAMENTS, "2026-08-10", 7,
            lines=[make_line("А", 1)],
        )
        store.save_request(
            "База-1", "Точка-2", CATEGORY_MEDICAMENTS, "2026-08-10", 7,
            lines=[make_line("Б", 2)],
        )
        store.save_request(
            "База-1", "Точка-1", CATEGORY_SOLUTIONS, "2026-08-10", 7,
            lines=[make_line("С", 3)],
        )
        store.save_request(
            "База-2", "Точка-1", CATEGORY_MEDICAMENTS, "2026-08-10", 7,
            lines=[make_line("В", 3)],
        )
        store.save_request(
            "База-1", "Точка-1", CATEGORY_MEDICAMENTS, "2026-08-17", 7,
            lines=[make_line("Г", 4)],
        )

        reqs = store.list_requests("База-1", CATEGORY_MEDICAMENTS, "2026-08-10")
        assert [r["point"] for r in reqs] == ["Точка-1", "Точка-2"]
        assert all(r["base"] == "База-1" for r in reqs)
        assert all(r["category"] == CATEGORY_MEDICAMENTS for r in reqs)
        assert all(r["week_start"] == "2026-08-10" for r in reqs)
        assert reqs[0]["lines"] == [make_line("А", 1)]

        # раздел растворов той же базы/недели — только своя заявка
        sol = store.list_requests("База-1", CATEGORY_SOLUTIONS, "2026-08-10")
        assert [r["point"] for r in sol] == ["Точка-1"]

    def test_list_week_returns_all_bases(self, store):
        store.save_request(
            "База-1", "Точка-1", CATEGORY_MEDICAMENTS, "2026-08-10", 7,
            lines=[make_line("А", 1)],
        )
        store.save_request(
            "База-2", "Точка-1", CATEGORY_MEDICAMENTS, "2026-08-10", 7,
            lines=[make_line("Б", 2)],
        )
        store.save_request(
            "База-1", "Точка-1", CATEGORY_MEDICAMENTS, "2026-08-17", 7,
            lines=[make_line("В", 3)],
        )

        week = store.list_week("2026-08-10")
        assert {r["base"] for r in week} == {"База-1", "База-2"}
        assert len(week) == 2
        assert all(r["week_start"] == "2026-08-10" for r in week)
        assert all(r["lines"] for r in week)
        assert all("category" in r for r in week)

    def test_list_range_with_filters(self, store):
        store.save_request(
            "База-1", "Точка-1", CATEGORY_MEDICAMENTS, "2026-08-03", 7,
            lines=[make_line("А", 1)],
        )
        store.save_request(
            "База-1", "Точка-1", CATEGORY_MEDICAMENTS, "2026-08-10", 7,
            lines=[make_line("Б", 2)],
        )
        store.save_request(
            "База-1", "Точка-1", CATEGORY_MEDICAMENTS, "2026-08-17", 7,
            lines=[make_line("В", 3)],
        )
        store.save_request(
            "База-2", "Точка-1", CATEGORY_MEDICAMENTS, "2026-08-10", 7,
            lines=[make_line("Г", 4)],
        )
        store.save_request(
            "База-1", "Точка-2", CATEGORY_MEDICAMENTS, "2026-08-10", 7,
            lines=[make_line("Д", 5)],
        )

        # весь диапазон
        all_in_range = store.list_range("2026-08-03", "2026-08-17")
        assert len(all_in_range) == 5

        # одна неделя — только она
        mid = store.list_range("2026-08-10", "2026-08-10")
        assert [r["week_start"] for r in mid] == ["2026-08-10"] * 3
        assert all(r["lines"] for r in mid)  # lines подгружены

        # фильтр по базе
        by_base = store.list_range("2026-08-03", "2026-08-17", base="База-2")
        assert len(by_base) == 1
        assert by_base[0]["base"] == "База-2"

        # фильтр по точке
        by_point = store.list_range("2026-08-03", "2026-08-17", point="Точка-2")
        assert len(by_point) == 1
        assert by_point[0]["point"] == "Точка-2"

        # оба фильтра сразу
        both = store.list_range(
            "2026-08-03", "2026-08-17", base="База-1", point="Точка-1"
        )
        assert len(both) == 3


class TestValidationAndCascade:
    def test_negative_qty_raises(self, store):
        with pytest.raises(ValueError, match="отрицательн"):
            store.save_request(
                "База-1", "Точка-1", CATEGORY_MEDICAMENTS, "2026-08-10", 7,
                lines=[make_line("А", -1)],
            )
        # при ошибке валидации ничего не сохраняется
        assert store.get_request("База-1", "Точка-1", CATEGORY_MEDICAMENTS, "2026-08-10") is None

    def test_delete_request_cascades_lines(self, store):
        rid = store.save_request(
            "База-1", "Точка-1", CATEGORY_MEDICAMENTS, "2026-08-10", 7,
            lines=[make_line("А", 1), make_line("Б", 2)],
        )
        store._conn.execute("DELETE FROM requests WHERE id = ?", (rid,))
        store._conn.commit()
        rows = store._conn.execute(
            "SELECT COUNT(*) AS c FROM request_lines"
        ).fetchone()
        assert rows["c"] == 0


def _create_v1_db(db):
    """Создать БД схемы v1 (без category) с данными — для теста миграции 1→2."""
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            base TEXT NOT NULL,
            point TEXT NOT NULL,
            week_start TEXT NOT NULL,
            author_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'draft',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (base, point, week_start)
        );
        CREATE TABLE request_lines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id INTEGER NOT NULL REFERENCES requests(id) ON DELETE CASCADE,
            item TEXT NOT NULL,
            unit TEXT NOT NULL DEFAULT '',
            grp TEXT NOT NULL DEFAULT '',
            qty INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX idx_lines_req ON request_lines(request_id);
        CREATE TABLE closures (
            base TEXT NOT NULL,
            week_start TEXT NOT NULL,
            closed_at TEXT NOT NULL,
            closed_by INTEGER NOT NULL,
            PRIMARY KEY (base, week_start)
        );
        """
    )
    conn.execute("PRAGMA user_version = 1")
    # Заявка с раствор-строкой (смешанная) → solutions.
    conn.execute(
        "INSERT INTO requests (base, point, week_start, author_id, status, created_at, updated_at) "
        "VALUES ('База-1', 'Точка-1', '2026-08-10', 7, 'sent', 't', 't')"
    )
    rid = conn.execute("SELECT id FROM requests WHERE point = 'Точка-1'").fetchone()[0]
    conn.execute(
        "INSERT INTO request_lines (request_id, item, unit, grp, qty) VALUES (?, 'Физ 200/250', 'фл', 'Растворы', 5)",
        (rid,),
    )
    conn.execute(
        "INSERT INTO request_lines (request_id, item, unit, grp, qty) VALUES (?, 'Атропин', 'амп', 'Неспецифика', 3)",
        (rid,),
    )
    # Заявка без раствор-строк → medicaments.
    conn.execute(
        "INSERT INTO requests (base, point, week_start, author_id, status, created_at, updated_at) "
        "VALUES ('База-1', 'Точка-2', '2026-08-10', 8, 'draft', 't', 't')"
    )
    rid2 = conn.execute("SELECT id FROM requests WHERE point = 'Точка-2'").fetchone()[0]
    conn.execute(
        "INSERT INTO request_lines (request_id, item, unit, grp, qty) VALUES (?, 'Атропин', 'амп', 'Неспецифика', 7)",
        (rid2,),
    )
    # Закрытие базы — должно продублироваться на оба раздела.
    conn.execute(
        "INSERT INTO closures (base, week_start, closed_at, closed_by) VALUES ('База-1', '2026-08-10', 't', 9)"
    )
    conn.commit()
    conn.close()


class TestMigration:
    def test_migration_v1_to_v2_preserves_data(self, tmp_path):
        """Миграция 1→2: категория выводится по строкам, закрытия дублируются."""
        db = tmp_path / "needs.db"
        _create_v1_db(db)

        s = SqliteNeedsStore(db)
        try:
            assert s._conn.execute("PRAGMA user_version").fetchone()[0] == 2

            # раствор-заявка → solutions, строки сохранены
            req = s.get_request("База-1", "Точка-1", CATEGORY_SOLUTIONS, "2026-08-10")
            assert req is not None
            assert req["category"] == CATEGORY_SOLUTIONS
            assert [line["item"] for line in req["lines"]] == ["Физ 200/250", "Атропин"]
            # в раздел medicaments эта заявка не попала
            assert s.get_request("База-1", "Точка-1", CATEGORY_MEDICAMENTS, "2026-08-10") is None

            # медикамент-заявка → medicaments
            med = s.get_request("База-1", "Точка-2", CATEGORY_MEDICAMENTS, "2026-08-10")
            assert med is not None
            assert med["category"] == CATEGORY_MEDICAMENTS
            assert [line["item"] for line in med["lines"]] == ["Атропин"]

            # закрытие продублировано на оба раздела
            assert s.is_closed("База-1", CATEGORY_SOLUTIONS, "2026-08-10") is True
            assert s.is_closed("База-1", CATEGORY_MEDICAMENTS, "2026-08-10") is True

            # после миграции CRUD работает с новой схемой
            new_id = s.save_request(
                "База-1", "Точка-1", CATEGORY_MEDICAMENTS, "2026-08-10", 7,
                lines=[make_line("Новое", 1)],
            )
            assert new_id is not None
            new_req = s.get_request("База-1", "Точка-1", CATEGORY_MEDICAMENTS, "2026-08-10")
            assert new_req is not None
            assert new_req["lines"] == [make_line("Новое", 1)]
        finally:
            s.close_conn()


class TestSchema:
    def test_schema_version_is_2(self):
        assert SCHEMA_VERSION == 2

    def test_requests_have_category_column(self, store):
        store.save_request(
            "База-1", "Точка-1", CATEGORY_SOLUTIONS, "2026-08-10", 7,
            lines=[make_line("А", 1)],
        )
        cols = {r["name"] for r in store._conn.execute("PRAGMA table_info(requests)").fetchall()}
        assert "category" in cols
        ccols = {r["name"] for r in store._conn.execute("PRAGMA table_info(closures)").fetchall()}
        assert "category" in ccols
