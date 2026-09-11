"""Тесты «Сводки» — модуля, который доказывает, что каркас сработал (ADR-0017).

Проверяется не только арифметика, но и главное обещание модуля: он читает записи
**через интерфейс** модуля `records`, а таблицу `anesthesia` не знает вовсе.
Поэтому здесь есть и подстановка чужого сервиса-заглушки, и проверка исходников
модуля на прямое обращение к таблице.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from docapp.core import access
from docapp.domain.anesthesia import Anesthesia
from docapp.domain.employee import DOCTOR, EDITOR, HEAD, HEAD_NURSE, NURSE, Employee
from docapp.people.store import SqliteEmployeeStore
from docapp.records.service import AnesthesiaService
from docapp.records.store import SqliteAnesthesiaStore
from docapp.summary.service import BY_DAY, BY_MONTH, BY_NURSE, SummaryService
from factories import make_db

SUMMARY_DIR = Path(__file__).resolve().parents[1] / "src" / "docapp" / "summary"


def make_anesthesia(**overrides) -> Anesthesia:
    base: dict = dict(
        date=date(2026, 9, 1),
        patient_name="Пациент А.",
        doctor_id=1,
        nurse_id=2,
        created_at=datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc),
    )
    base.update(overrides)
    return Anesthesia(**base)


@pytest.fixture
def env(tmp_path):
    """Записи, справочник сотрудников и сводка поверх них.

    Роли: 1 — заведующий (видит всех), 2 — врач (видит свои), 3 — медсестра.
    """
    db = make_db(tmp_path, seed=False)
    employees = SqliteEmployeeStore(db)
    # id берём из хранилища: автоинкремент — не часть контракта теста
    head = employees.add(Employee(last_name="Иванов", first_name="Иван", role=HEAD))
    doctor = employees.add(Employee(last_name="Петров", first_name="Пётр", role=DOCTOR))
    nurse = employees.add(Employee(last_name="Сидорова", first_name="Анна", role=NURSE))
    other_nurse = employees.add(Employee(last_name="Кузнецова", first_name="Ольга", role=NURSE))

    store = SqliteAnesthesiaStore(db)
    # сентябрь и август у одного врача, две разные сестры — чтобы разрезы
    # «по врачам» и «по сёстрам» отличались
    for record in (
        make_anesthesia(date=date(2026, 9, 2), doctor_id=doctor.id, nurse_id=nurse.id),
        make_anesthesia(date=date(2026, 8, 20), doctor_id=doctor.id, nurse_id=nurse.id),
        make_anesthesia(date=date(2026, 9, 5), doctor_id=doctor.id, nurse_id=other_nurse.id),
    ):
        store.add(record)

    service = SummaryService(AnesthesiaService(store), employees)
    yield service, store, employees, head, doctor, nurse
    store.close()
    employees.close()


def user(employee_id: int, role: str) -> Employee:
    return Employee(id=employee_id, last_name="Кто", first_name="То", role=role)


# ── подсчёт ───────────────────────────────────────────────────────────


class TestAggregate:
    def test_by_doctor(self, env):
        service, _, _, head, doctor, _ = env
        result = service.aggregate(user(head.id, HEAD), "2026-01-01", "2026-12-31")
        assert result["by"] == "doctor" and result["total"] == 3
        assert result["rows"] == [
            {"key": str(doctor.id), "count": 3, "name": "Петров Пётр"}
        ]

    def test_by_nurse_with_names(self, env):
        """Разрез «по медсёстрам»: имена подставляет справочник, не сводка."""
        service, _, _, head, _, nurse = env
        result = service.aggregate(user(head.id, HEAD), "2026-01-01", "2026-12-31", BY_NURSE)
        assert result["rows"] == [
            {"key": str(nurse.id), "count": 2, "name": "Сидорова Анна"},
            {"key": str(nurse.id + 1), "count": 1, "name": "Кузнецова Ольга"},
        ]

    def test_by_month_is_chronological(self, env):
        """Динамика идёт по времени, а не по количеству."""
        service, _, _, head, _, _ = env
        result = service.aggregate(user(head.id, HEAD), "2026-01-01", "2026-12-31", BY_MONTH)
        assert result["rows"] == [
            {"key": "2026-08", "count": 1},
            {"key": "2026-09", "count": 2},
        ]

    def test_by_day(self, env):
        service, _, _, head, _, _ = env
        result = service.aggregate(user(head.id, HEAD), "2026-09-01", "2026-09-30", BY_DAY)
        assert [row["key"] for row in result["rows"]] == ["2026-09-02", "2026-09-05"]

    def test_period_bounds_are_inclusive(self, env):
        service, _, _, head, _, _ = env
        who = user(head.id, HEAD)
        assert service.aggregate(who, "2026-09-02", "2026-09-02")["total"] == 1
        assert service.aggregate(who, "2026-09-03", "2026-09-04")["total"] == 0

    def test_unknown_period_gives_empty_rows(self, env):
        service, _, _, head, _, _ = env
        result = service.aggregate(user(head.id, HEAD), "2020-01-01", "2020-12-31")
        assert result["total"] == 0 and result["rows"] == []

    def test_unknown_split_raises(self, env):
        service, _, _, head, _, _ = env
        with pytest.raises(ValueError, match="разрез"):
            service.aggregate(user(head.id, HEAD), "2026-01-01", "2026-12-31", "base")


# ── права: «все» и «только свои» ──────────────────────────────────────


class TestScope:
    def test_head_sees_everyone(self, env):
        service, _, _, head, _, _ = env
        result = service.aggregate(user(head.id, HEAD), "2026-01-01", "2026-12-31")
        assert result["scope"] == "all" and result["total"] == 3

    def test_head_nurse_sees_only_hers(self, env):
        """Старшая сестра в анестезиях — как медсестра: полный обзор у заведующего.

        Так решено в таблице прав (ADR-0023): `records.view_all` есть только у
        `head`. Если старшей сестре нужен обзор отделения — это правка одной
        строки в `core/access.ALLOWED`, а не отдельная логика сводки.
        """
        service, _, _, head, _, _ = env
        assert not access.has(HEAD_NURSE, access.RECORDS_VIEW_ALL)
        assert service.aggregate(user(head.id, HEAD_NURSE), "2026-01-01", "2026-12-31")["scope"] == "own"

    def test_doctor_sees_only_own_records(self, env):
        """Врач видит свои записи: чужие в его сводку не попадают."""
        service, store, _, head, doctor, nurse = env
        store.add(make_anesthesia(date=date(2026, 9, 6), doctor_id=head.id, nurse_id=nurse.id))

        result = service.aggregate(user(doctor.id, DOCTOR), "2026-01-01", "2026-12-31")
        assert result["scope"] == "own"
        assert result["total"] == 3                      # записи врача, а не заведующего
        assert result["rows"] == [
            {"key": str(doctor.id), "count": 3, "name": "Петров Пётр"}
        ]

    def test_editor_is_also_doctor(self, env):
        """Редактор — врач с правом курирования: свои записи, как у врача."""
        service, _, _, head, doctor, _ = env
        result = service.aggregate(user(doctor.id, EDITOR), "2026-01-01", "2026-12-31")
        assert result["scope"] == "own"

    def test_nurse_sees_records_where_she_worked(self, env):
        """Сестра видит записи, в которых участвовала, — по колонке nurse_id."""
        service, _, _, head, _, nurse = env
        result = service.aggregate(user(nurse.id, NURSE), "2026-01-01", "2026-12-31", BY_NURSE)
        assert result["scope"] == "own" and result["total"] == 2
        assert result["rows"] == [
            {"key": str(nurse.id), "count": 2, "name": "Сидорова Анна"}
        ]

    def test_permissions_come_from_access_table(self):
        """Права сверяются с таблицей core/access, а не со своим списком ролей."""
        assert access.has(HEAD, access.RECORDS_VIEW_ALL)
        assert not access.has(DOCTOR, access.RECORDS_VIEW_ALL)
        assert access.has(DOCTOR, access.RECORDS_VIEW_OWN)
        assert access.has(NURSE, access.RECORDS_VIEW_OWN)


# ── каркас: сводка не знает про таблицу анестезий ─────────────────────


class _StubRecords:
    """Чужой интерфейс записи: сводка обязана работать и с ним (шов, не таблица)."""

    def __init__(self, key: str = "2") -> None:
        self.key = key
        self.calls: list[tuple] = []

    def aggregate(self, from_date, to_date, by="doctor", *, doctor_id=None, nurse_id=None):
        self.calls.append((from_date, to_date, by, doctor_id, nurse_id))
        return {
            "from": from_date, "to": to_date, "by": by, "total": 1,
            "rows": [{"key": self.key, "count": 1}],
        }


class TestCarcass:
    def test_works_with_foreign_interface(self, tmp_path):
        """Сводка принимает любой объект с методом aggregate — не хранилище."""
        db = make_db(tmp_path, seed=False)
        employees = SqliteEmployeeStore(db)
        doctor = employees.add(Employee(last_name="Петров", first_name="Пётр", role=DOCTOR))
        stub = _StubRecords(key=str(doctor.id))

        service = SummaryService(stub, employees)
        result = service.aggregate(user(doctor.id + 1, HEAD), "2026-09-01", "2026-09-30")

        assert result["total"] == 1
        assert result["rows"] == [
            {"key": str(doctor.id), "count": 1, "name": "Петров Пётр"}
        ]
        assert stub.calls == [("2026-09-01", "2026-09-30", "doctor", None, None)]
        employees.close()

    def test_own_scope_is_passed_as_filter(self, tmp_path):
        """«Только свои» передаётся фильтром в интерфейс записей, а не фильтром сводки."""
        db = make_db(tmp_path, seed=False)
        employees = SqliteEmployeeStore(db)
        stub = _StubRecords()

        SummaryService(stub, employees).aggregate(user(2, DOCTOR), "2026-09-01", "2026-09-30")
        assert stub.calls == [("2026-09-01", "2026-09-30", "doctor", 2, None)]
        employees.close()

    def test_module_has_no_own_tables(self, env):
        """`sources()` показывает, из чего собрана сводка, и что таблиц у неё нет."""
        service, *_ = env
        sources = service.sources()
        assert sources["own_tables"] == ()
        assert sources["records"] == "AnesthesiaService"
        assert sources["employees"] == "SqliteEmployeeStore"

    @pytest.mark.parametrize("path", sorted(SUMMARY_DIR.rglob("*.py")), ids=lambda p: p.name)
    def test_module_never_touches_anesthesia_table(self, path):
        """Ни одного SQL-обращения к `anesthesia` в исходниках модуля.

        Это и есть проверка каркаса из ТЗ §10.3: если однажды сводке понадобится
        читать таблицу чужого модуля — правим каркас, а не обходим его.
        """
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"FROM\s+anesthesia\b", text, re.IGNORECASE), path
        assert not re.search(r"JOIN\s+anesthesia\b", text, re.IGNORECASE), path
        assert "SqliteAnesthesiaStore" not in text, path
