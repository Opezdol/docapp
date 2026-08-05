"""Тесты сервиса записей: бизнес-правила ввода анестезий."""

from datetime import date, datetime, timedelta, timezone

import pytest

from docapp.domain.employee import DOCTOR, NURSE, Employee
from docapp.records.service import (
    ActiveNurseRequired,
    AnesthesiaService,
    FutureDateError,
    NotFoundError,
    NotOwnedError,
)
from docapp.storage.sqlite_store import (
    SqliteActiveNurseStore,
    SqliteAnesthesiaStore,
    SqliteEmployeeStore,
)


def make_employee(**overrides):
    base = dict(last_name="Иванов", first_name="Иван", role=DOCTOR)
    base.update(overrides)
    return Employee(**base)


@pytest.fixture
def stores(tmp_path):
    db_path = tmp_path / "test.db"
    es = SqliteEmployeeStore(db_path)
    as_ = SqliteAnesthesiaStore(db_path)
    ans = SqliteActiveNurseStore(db_path)
    yield es, as_, ans
    es.close()
    as_.close()
    ans.close()


@pytest.fixture
def service(stores):
    _, as_, ans = stores
    return AnesthesiaService(as_, ans)


def _add_doctor_and_nurse(stores) -> tuple[int, int]:
    es, _, _ = stores
    doctor = es.add(make_employee(role=DOCTOR))
    nurse = es.add(make_employee(role=NURSE))
    return doctor.id, nurse.id


def _add_second_doctor(stores) -> int:
    es, _, _ = stores
    return es.add(make_employee(last_name="Петров", first_name="Пётр", role=DOCTOR)).id


class TestCreate:
    def test_without_active_nurse_raises(self, service, stores):
        doctor_id, _ = _add_doctor_and_nurse(stores)
        with pytest.raises(ActiveNurseRequired):
            service.create(doctor_id, date.today(), "Петров Петр", "123")

    def test_creates_with_active_nurse(self, service, stores):
        doctor_id, nurse_id = _add_doctor_and_nurse(stores)
        ans = stores[2]
        ans.set_active_nurse(doctor_id, nurse_id)

        an = service.create(doctor_id, date.today(), "Петров Петр", "123")
        assert an.id is not None
        assert an.doctor_id == doctor_id
        assert an.nurse_id == nurse_id
        assert an.patient_name == "Петров Петр"
        assert an.history_number == "123"
        assert an.date == date.today()
        assert an.created_at.tzinfo is not None
        assert an.created_at.utcoffset() == timezone.utc.utcoffset(None)

    def test_future_date_raises(self, service, stores):
        doctor_id, nurse_id = _add_doctor_and_nurse(stores)
        stores[2].set_active_nurse(doctor_id, nurse_id)
        tomorrow = date.today() + timedelta(days=1)
        with pytest.raises(FutureDateError):
            service.create(doctor_id, tomorrow, "Петров Петр", "123")

    def test_today_is_allowed(self, service, stores):
        doctor_id, nurse_id = _add_doctor_and_nurse(stores)
        stores[2].set_active_nurse(doctor_id, nurse_id)
        an = service.create(doctor_id, date.today(), "Петров Петр", "123")
        assert an.date == date.today()


class TestListMine:
    def test_returns_only_doctor_records(self, service, stores):
        es, as_, ans = stores
        doctor_id, nurse_id = _add_doctor_and_nurse(stores)
        doctor2_id = _add_second_doctor(stores)
        ans.set_active_nurse(doctor_id, nurse_id)
        ans.set_active_nurse(doctor2_id, nurse_id)

        a1 = service.create(doctor_id, date.today(), "Пациент А", "1")
        service.create(doctor2_id, date.today(), "Пациент Б", "2")
        a3 = service.create(doctor_id, date.today(), "Пациент В", "3")

        mine = service.list_mine(doctor_id)
        assert [a.id for a in mine] == [a3.id, a1.id]  # свежие сверху (id DESC)


class TestUpdate:
    def test_updates_own_record(self, service, stores):
        es, as_, ans = stores
        doctor_id, nurse_id = _add_doctor_and_nurse(stores)
        ans.set_active_nurse(doctor_id, nurse_id)
        created = service.create(doctor_id, date.today(), "Пациент А", "1")

        updated = service.update(
            doctor_id,
            created.id,
            date.today(),
            "Новый Пациент",
            "999",
            nurse_id,
        )
        assert updated.id == created.id
        assert updated.created_at == created.created_at  # факт не меняется
        assert updated.patient_name == "Новый Пациент"
        assert updated.history_number == "999"

    def test_update_foreign_record_raises(self, service, stores):
        es, as_, ans = stores
        doctor_id, nurse_id = _add_doctor_and_nurse(stores)
        doctor2_id = _add_second_doctor(stores)
        ans.set_active_nurse(doctor_id, nurse_id)
        created = service.create(doctor_id, date.today(), "Пациент А", "1")

        with pytest.raises(NotOwnedError):
            service.update(doctor2_id, created.id, date.today(), "Чужая правка", "1", nurse_id)

    def test_update_missing_raises(self, service, stores):
        doctor_id, nurse_id = _add_doctor_and_nurse(stores)
        with pytest.raises(NotFoundError):
            service.update(doctor_id, 999, date.today(), "Х", "1", nurse_id)

    def test_update_future_date_raises(self, service, stores):
        es, as_, ans = stores
        doctor_id, nurse_id = _add_doctor_and_nurse(stores)
        ans.set_active_nurse(doctor_id, nurse_id)
        created = service.create(doctor_id, date.today(), "Пациент А", "1")
        tomorrow = date.today() + timedelta(days=1)
        with pytest.raises(FutureDateError):
            service.update(doctor_id, created.id, tomorrow, "Х", "1", nurse_id)


class TestDelete:
    def test_deletes_own_record(self, service, stores):
        es, as_, ans = stores
        doctor_id, nurse_id = _add_doctor_and_nurse(stores)
        ans.set_active_nurse(doctor_id, nurse_id)
        created = service.create(doctor_id, date.today(), "Пациент А", "1")
        service.delete(doctor_id, created.id)
        assert as_.get_by_id(created.id) is None

    def test_delete_foreign_record_raises(self, service, stores):
        es, as_, ans = stores
        doctor_id, nurse_id = _add_doctor_and_nurse(stores)
        doctor2_id = _add_second_doctor(stores)
        ans.set_active_nurse(doctor_id, nurse_id)
        created = service.create(doctor_id, date.today(), "Пациент А", "1")
        with pytest.raises(NotOwnedError):
            service.delete(doctor2_id, created.id)

    def test_delete_missing_raises(self, service, stores):
        doctor_id, _ = _add_doctor_and_nurse(stores)
        with pytest.raises(NotFoundError):
            service.delete(doctor_id, 999)
