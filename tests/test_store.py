"""Тесты хранилищ SQLite: сотрудники (модуль people) и анестезии (records)."""

import sqlite3
from datetime import date, datetime, timezone

import pytest

from docapp.domain.anesthesia import Anesthesia
from docapp.domain.employee import DOCTOR, HEAD, HEAD_NURSE, NURSE, Employee
from docapp.people.store import SqliteEmployeeStore
from docapp.records.store import SqliteAnesthesiaStore
from factories import make_db


@pytest.fixture
def emp_store(tmp_path):
    """БД со всеми схемами, но без готовых сотрудников: их заводят сами тесты."""
    s = SqliteEmployeeStore(make_db(tmp_path, seed=False))
    yield s
    s.close()


@pytest.fixture
def an_store(tmp_path):
    s = SqliteAnesthesiaStore(make_db(tmp_path))
    yield s
    s.close()


def make_employee(**overrides):
    base = dict(last_name="Иванов", first_name="Иван", role=DOCTOR)
    base.update(overrides)
    return Employee(**base)


def make_anesthesia(**overrides):
    base = dict(
        date=date(2026, 8, 4),
        patient_name="Петров Петр Петрович",
        doctor_id=1,
        nurse_id=2,
        created_at=datetime(2026, 8, 4, 10, 30, tzinfo=timezone.utc),
    )
    base.update(overrides)
    return Anesthesia(**base)


def add_doctor_and_nurse(store) -> tuple[int, int]:
    """Добавить врача и медсестру, вернуть (doctor_id, nurse_id)."""
    doctor = store.add(make_employee(role=DOCTOR))
    nurse = store.add(make_employee(role=NURSE))
    return doctor.id, nurse.id


class TestEmployeeStore:
    def test_add_returns_id(self, emp_store):
        saved = emp_store.add(make_employee())
        assert saved.id is not None
        assert saved.id > 0

    def test_roundtrip_all_fields(self, emp_store):
        emp = make_employee(
            last_name="Сидорова",
            first_name="Анна",
            middle_name="Петровна",
            role=NURSE,
            buh_id="B-100",
        )
        saved = emp_store.add(emp)
        loaded = emp_store.get_by_id(saved.id)
        assert loaded == saved
        assert loaded.last_name == "Сидорова"
        assert loaded.first_name == "Анна"
        assert loaded.middle_name == "Петровна"
        assert loaded.role == NURSE
        assert loaded.buh_id == "B-100"

    def test_get_by_id_missing(self, emp_store):
        assert emp_store.get_by_id(999) is None

    def test_get_by_login_found(self, emp_store):
        emp_store.add(make_employee(login="ivanov", password_hash="abc"))
        emp = emp_store.get_by_login("ivanov")
        assert emp is not None
        assert emp.login == "ivanov"
        assert emp.password_hash == "abc"

    def test_get_by_login_missing(self, emp_store):
        assert emp_store.get_by_login("no_such_login") is None

    def test_list_all_sorted_by_last_name(self, emp_store):
        emp_store.add(make_employee(last_name="Иванов", first_name="Иван"))
        emp_store.add(make_employee(last_name="Абрамов", first_name="Пётр"))
        emp_store.add(make_employee(last_name="Иванов", first_name="Пётр"))
        names = [(e.last_name, e.first_name) for e in emp_store.list_all()]
        assert names == [
            ("Абрамов", "Пётр"),
            ("Иванов", "Иван"),
            ("Иванов", "Пётр"),
        ]

    def test_list_nurse_staff_includes_head_nurse(self, emp_store):
        """В пару врачу выбирают и медсестру, и старшую сестру (решение владельца)."""
        emp_store.add(make_employee(role=NURSE, last_name="Сидорова", first_name="Анна"))
        emp_store.add(make_employee(role=HEAD_NURSE, last_name="Волкова", first_name="Вера"))
        emp_store.add(make_employee(role=DOCTOR))
        staff = emp_store.list_nurse_staff()
        assert [(e.last_name, e.role) for e in staff] == [
            ("Волкова", HEAD_NURSE),
            ("Сидорова", NURSE),
        ]

    def test_duplicate_login_raises(self, emp_store):
        emp_store.add(make_employee(login="ivanov", password_hash="abc"))
        with pytest.raises(sqlite3.IntegrityError):
            emp_store.add(make_employee(last_name="Другой", login="ivanov", password_hash="xyz"))

    def test_survives_reopen(self, tmp_path):
        db_path = tmp_path / "test.db"
        with SqliteEmployeeStore(db_path) as s:
            saved = s.add(make_employee())
        with SqliteEmployeeStore(db_path) as s2:
            loaded = s2.get_by_id(saved.id)
            assert loaded == saved


class TestAnesthesiaStore:
    def test_add_returns_id(self, an_store, emp_store):
        add_doctor_and_nurse(emp_store)
        saved = an_store.add(make_anesthesia())
        assert saved.id is not None
        assert saved.id > 0

    def test_roundtrip_all_fields(self, an_store, emp_store):
        doctor_id, nurse_id = add_doctor_and_nurse(emp_store)
        an = make_anesthesia(
            date=date(2026, 7, 15),
            patient_name="Васильев Василий",
            doctor_id=doctor_id,
            nurse_id=nurse_id,
            created_at=datetime(2026, 7, 15, 8, 0, tzinfo=timezone.utc),
        )
        saved = an_store.add(an)
        loaded = an_store.get_by_id(saved.id)
        assert loaded == saved
        assert loaded.date == date(2026, 7, 15)
        assert loaded.created_at.tzinfo is not None
        assert loaded.created_at.utcoffset() == timezone.utc.utcoffset(None)

    def test_get_by_id_missing(self, an_store):
        assert an_store.get_by_id(999) is None

    def test_list_by_doctor_sorted_by_date_desc(self, an_store, emp_store):
        doctor_id, nurse_id = add_doctor_and_nurse(emp_store)
        a1 = an_store.add(make_anesthesia(date=date(2026, 8, 1), doctor_id=doctor_id, nurse_id=nurse_id))
        a2 = an_store.add(make_anesthesia(date=date(2026, 8, 3), doctor_id=doctor_id, nurse_id=nurse_id))
        a3 = an_store.add(make_anesthesia(date=date(2026, 8, 2), doctor_id=doctor_id, nurse_id=nurse_id))
        ids = [a.id for a in an_store.list_by_doctor(doctor_id)]
        assert ids == [a2.id, a3.id, a1.id]

    def test_list_by_doctor_filters_by_doctor(self, an_store, emp_store):
        doctor1 = emp_store.add(make_employee(role=DOCTOR))
        doctor2 = emp_store.add(make_employee(role=DOCTOR))
        nurse = emp_store.add(make_employee(role=NURSE))
        an_store.add(make_anesthesia(doctor_id=doctor1.id, nurse_id=nurse.id))
        an_store.add(make_anesthesia(doctor_id=doctor2.id, nurse_id=nurse.id))
        ids = [a.id for a in an_store.list_by_doctor(doctor1.id)]
        assert len(ids) == 1

    def test_list_by_nurse_filters_and_sorts(self, an_store, emp_store):
        doctor = emp_store.add(make_employee(role=DOCTOR))
        nurse1 = emp_store.add(make_employee(role=NURSE, last_name="Сидорова"))
        nurse2 = emp_store.add(make_employee(role=NURSE, last_name="Козлова"))
        a1 = an_store.add(make_anesthesia(date=date(2026, 8, 1), doctor_id=doctor.id, nurse_id=nurse1.id))
        an_store.add(make_anesthesia(date=date(2026, 8, 2), doctor_id=doctor.id, nurse_id=nurse2.id))
        a3 = an_store.add(make_anesthesia(date=date(2026, 8, 3), doctor_id=doctor.id, nurse_id=nurse1.id))

        ids = [a.id for a in an_store.list_by_nurse(nurse1.id)]
        assert ids == [a3.id, a1.id]  # только свои, свежие сверху

    def test_list_by_nurse_empty(self, an_store, emp_store):
        nurse = emp_store.add(make_employee(role=NURSE))
        assert an_store.list_by_nurse(nurse.id) == []

    def test_update_changes_fields_keeps_id(self, an_store, emp_store):
        doctor_id, nurse_id = add_doctor_and_nurse(emp_store)
        saved = an_store.add(make_anesthesia(doctor_id=doctor_id, nurse_id=nurse_id))
        updated = Anesthesia(
            id=saved.id,
            date=date(2026, 8, 5),
            patient_name="Новый Пациент",
            doctor_id=doctor_id,
            nurse_id=nurse_id,
            created_at=saved.created_at,
        )
        an_store.update(updated)
        loaded = an_store.get_by_id(saved.id)
        assert loaded == updated

    def test_update_missing_raises_keyerror(self, an_store):
        with pytest.raises(KeyError):
            an_store.update(make_anesthesia(id=999))

    def test_delete(self, an_store, emp_store):
        doctor_id, nurse_id = add_doctor_and_nurse(emp_store)
        saved = an_store.add(make_anesthesia(doctor_id=doctor_id, nurse_id=nurse_id))
        an_store.delete(saved.id)
        assert an_store.get_by_id(saved.id) is None

    def test_delete_missing_is_silent(self, an_store):
        an_store.delete(999)

    def test_foreign_key_violation(self, an_store, emp_store):
        nurse = emp_store.add(make_employee(role=NURSE))
        with pytest.raises(sqlite3.IntegrityError):
            an_store.add(make_anesthesia(doctor_id=999, nurse_id=nurse.id))

    def test_survives_reopen(self, tmp_path):
        db_path = tmp_path / "test.db"
        with SqliteEmployeeStore(db_path) as es:
            doctor_id, nurse_id = add_doctor_and_nurse(es)
        with SqliteAnesthesiaStore(db_path) as s:
            saved = s.add(make_anesthesia(doctor_id=doctor_id, nurse_id=nurse_id))
        with SqliteAnesthesiaStore(db_path) as s2:
            loaded = s2.get_by_id(saved.id)
            assert loaded == saved


def add_records(store, doctor_id: int, nurse_id: int, days) -> list[int]:
    """Записи на заданные даты: вернуть их id (метки ставятся по id)."""
    return [
        store.add(make_anesthesia(date=day, doctor_id=doctor_id, nurse_id=nurse_id)).id
        for day in days
    ]


class TestAccruedMark:
    """Метки «учтена» в хранилище: пересчёт месяца одной транзакцией (ADR-0024)."""

    MOMENT = datetime(2026, 9, 11, 20, 0, tzinfo=timezone.utc)

    def test_marks_given_records(self, emp_store, an_store):
        doctor_id, nurse_id = add_doctor_and_nurse(emp_store)
        first, second = add_records(an_store, doctor_id, nurse_id,
                                    [date(2026, 9, 2), date(2026, 9, 5)])

        cleared, marked = an_store.remark_accrued(
            date(2026, 9, 1), date(2026, 9, 30), [first], self.MOMENT
        )

        assert (cleared, marked) == (0, 1)
        assert an_store.get_by_id(first).accrued_at == self.MOMENT
        assert an_store.get_by_id(second).accrued_at is None

    def test_second_run_clears_the_previous_marks(self, emp_store, an_store):
        doctor_id, nurse_id = add_doctor_and_nurse(emp_store)
        first, second = add_records(an_store, doctor_id, nurse_id,
                                    [date(2026, 9, 2), date(2026, 9, 5)])
        an_store.remark_accrued(date(2026, 9, 1), date(2026, 9, 30), [first], self.MOMENT)

        cleared, marked = an_store.remark_accrued(
            date(2026, 9, 1), date(2026, 9, 30), [second], self.MOMENT
        )

        assert (cleared, marked) == (1, 1)
        assert an_store.get_by_id(first).accrued_at is None
        assert an_store.get_by_id(second).accrued_at == self.MOMENT

    def test_another_month_keeps_its_marks(self, emp_store, an_store):
        """Метка принадлежит месяцу: чужой месяц пересчёт не задевает."""
        doctor_id, nurse_id = add_doctor_and_nurse(emp_store)
        august, september = add_records(an_store, doctor_id, nurse_id,
                                        [date(2026, 8, 20), date(2026, 9, 2)])
        an_store.remark_accrued(date(2026, 8, 1), date(2026, 8, 31), [august], self.MOMENT)

        an_store.remark_accrued(date(2026, 9, 1), date(2026, 9, 30), [september], self.MOMENT)

        assert an_store.get_by_id(august).accrued_at == self.MOMENT
        assert an_store.get_by_id(september).accrued_at == self.MOMENT

    def test_id_of_another_month_is_ignored(self, emp_store, an_store):
        doctor_id, nurse_id = add_doctor_and_nurse(emp_store)
        august, = add_records(an_store, doctor_id, nurse_id, [date(2026, 8, 20)])

        cleared, marked = an_store.remark_accrued(
            date(2026, 9, 1), date(2026, 9, 30), [august], self.MOMENT
        )

        assert (cleared, marked) == (0, 0)
        assert an_store.get_by_id(august).accrued_at is None

    def test_empty_ids_only_clear(self, emp_store, an_store):
        doctor_id, nurse_id = add_doctor_and_nurse(emp_store)
        first, = add_records(an_store, doctor_id, nurse_id, [date(2026, 9, 2)])
        an_store.remark_accrued(date(2026, 9, 1), date(2026, 9, 30), [first], self.MOMENT)

        cleared, marked = an_store.remark_accrued(
            date(2026, 9, 1), date(2026, 9, 30), [], self.MOMENT
        )

        assert (cleared, marked) == (1, 0)
        assert an_store.get_by_id(first).accrued_at is None
