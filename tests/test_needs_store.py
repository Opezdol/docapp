"""Тесты хранилища подприложения «Потребности» (SqliteNeedsStore)."""

import pytest

from docapp.needs.store import SqliteNeedsStore


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
        rid = store.save_request("База-1", "Точка-1", "2026-08-10", 7, lines=lines)

        req = store.get_request("База-1", "Точка-1", "2026-08-10")
        assert req is not None
        assert req["id"] == rid
        assert req["base"] == "База-1"
        assert req["point"] == "Точка-1"
        assert req["week_start"] == "2026-08-10"
        assert req["author_id"] == 7
        assert req["status"] == "draft"
        assert req["created_at"]
        assert req["updated_at"] == req["created_at"]
        assert req["lines"] == lines

    def test_get_missing_returns_none(self, store):
        assert store.get_request("База-1", "Точка-1", "2026-08-10") is None

    def test_empty_lines_saved(self, store):
        rid = store.save_request("База-1", "Точка-1", "2026-08-10", 7, lines=[])
        req = store.get_request("База-1", "Точка-1", "2026-08-10")
        assert req is not None
        assert req["id"] == rid
        assert req["lines"] == []


class TestUpsert:
    def test_second_save_updates_not_duplicates(self, store):
        rid1 = store.save_request(
            "База-1", "Точка-1", "2026-08-10", 7,
            lines=[make_line("А", 1)],
        )
        first = store.get_request("База-1", "Точка-1", "2026-08-10")

        rid2 = store.save_request(
            "База-1", "Точка-1", "2026-08-10", 9,
            lines=[make_line("Б", 2), make_line("В", 4)],
            status="sent",
        )
        assert rid2 == rid1

        rows = store._conn.execute("SELECT COUNT(*) AS c FROM requests").fetchone()
        assert rows["c"] == 1

        req = store.get_request("База-1", "Точка-1", "2026-08-10")
        assert req["author_id"] == 9
        assert req["status"] == "sent"
        assert req["lines"] == [make_line("Б", 2), make_line("В", 4)]
        assert req["created_at"] == first["created_at"]
        assert req["updated_at"] != first["updated_at"]

    def test_different_point_is_separate_request(self, store):
        rid1 = store.save_request("База-1", "Точка-1", "2026-08-10", 7,
                                  lines=[make_line("А", 1)])
        rid2 = store.save_request("База-1", "Точка-2", "2026-08-10", 7,
                                  lines=[make_line("Б", 2)])
        assert rid2 != rid1
        rows = store._conn.execute("SELECT COUNT(*) AS c FROM requests").fetchone()
        assert rows["c"] == 2

    def test_set_status(self, store):
        rid = store.save_request("База-1", "Точка-1", "2026-08-10", 7, lines=[])
        store.set_status(rid, "sent")
        req = store.get_request("База-1", "Точка-1", "2026-08-10")
        assert req["status"] == "sent"


class TestClosures:
    def test_close_reopen(self, store):
        assert store.is_closed("База-1", "2026-08-10") is False
        store.close("База-1", "2026-08-10", closed_by=7)
        assert store.is_closed("База-1", "2026-08-10") is True
        store.close("База-1", "2026-08-10", closed_by=8)  # повторный close не падает
        assert store.is_closed("База-1", "2026-08-10") is True
        store.reopen("База-1", "2026-08-10")
        assert store.is_closed("База-1", "2026-08-10") is False

    def test_list_closures(self, store):
        store.close("База-1", "2026-08-10", closed_by=7)
        store.close("База-2", "2026-08-17", closed_by=9)
        closures = store.list_closures()
        assert len(closures) == 2
        assert {c["base"] for c in closures} == {"База-1", "База-2"}
        assert {c["closed_by"] for c in closures} == {7, 9}
        assert all(c["closed_at"] for c in closures)


class TestLists:
    def test_list_requests_filters_by_base_and_week(self, store):
        store.save_request("База-1", "Точка-1", "2026-08-10", 7,
                           lines=[make_line("А", 1)])
        store.save_request("База-1", "Точка-2", "2026-08-10", 7,
                           lines=[make_line("Б", 2)])
        store.save_request("База-2", "Точка-1", "2026-08-10", 7,
                           lines=[make_line("В", 3)])
        store.save_request("База-1", "Точка-1", "2026-08-17", 7,
                           lines=[make_line("Г", 4)])

        reqs = store.list_requests("База-1", "2026-08-10")
        assert [r["point"] for r in reqs] == ["Точка-1", "Точка-2"]
        assert all(r["base"] == "База-1" for r in reqs)
        assert all(r["week_start"] == "2026-08-10" for r in reqs)
        assert reqs[0]["lines"] == [make_line("А", 1)]

    def test_list_week_returns_all_bases(self, store):
        store.save_request("База-1", "Точка-1", "2026-08-10", 7,
                           lines=[make_line("А", 1)])
        store.save_request("База-2", "Точка-1", "2026-08-10", 7,
                           lines=[make_line("Б", 2)])
        store.save_request("База-1", "Точка-1", "2026-08-17", 7,
                           lines=[make_line("В", 3)])

        week = store.list_week("2026-08-10")
        assert {r["base"] for r in week} == {"База-1", "База-2"}
        assert len(week) == 2
        assert all(r["week_start"] == "2026-08-10" for r in week)
        assert all(r["lines"] for r in week)

    def test_list_range_with_filters(self, store):
        store.save_request("База-1", "Точка-1", "2026-08-03", 7,
                           lines=[make_line("А", 1)])
        store.save_request("База-1", "Точка-1", "2026-08-10", 7,
                           lines=[make_line("Б", 2)])
        store.save_request("База-1", "Точка-1", "2026-08-17", 7,
                           lines=[make_line("В", 3)])
        store.save_request("База-2", "Точка-1", "2026-08-10", 7,
                           lines=[make_line("Г", 4)])
        store.save_request("База-1", "Точка-2", "2026-08-10", 7,
                           lines=[make_line("Д", 5)])

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
                "База-1", "Точка-1", "2026-08-10", 7,
                lines=[make_line("А", -1)],
            )
        # при ошибке валидации ничего не сохраняется
        assert store.get_request("База-1", "Точка-1", "2026-08-10") is None

    def test_delete_request_cascades_lines(self, store):
        rid = store.save_request(
            "База-1", "Точка-1", "2026-08-10", 7,
            lines=[make_line("А", 1), make_line("Б", 2)],
        )
        store._conn.execute("DELETE FROM requests WHERE id = ?", (rid,))
        store._conn.commit()
        rows = store._conn.execute(
            "SELECT COUNT(*) AS c FROM request_lines"
        ).fetchone()
        assert rows["c"] == 0
