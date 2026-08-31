"""Тесты сервиса записей: бизнес-правила ввода анестезий."""

from datetime import date, timezone

import pytest

from docapp.domain.employee import DOCTOR, NURSE, Employee
from docapp.records.service import AnesthesiaService, NotFoundError, NotOwnedError
from docapp.storage.sqlite_store import SqliteAnesthesiaStore, SqliteEmployeeStore


def make_employee(**overrides):
    base = dict(last_name="Иванов", first_name="Иван", role=DOCTOR)
    base.update(overrides)
    return Employee(**base)


@pytest.fixture
def stores(tmp_path):
    db_path = tmp_path / "test.db"
    es = SqliteEmployeeStore(db_path)
    as_ = SqliteAnesthesiaStore(db_path)
    yield es, as_
    es.close()
    as_.close()


@pytest.fixture
def service(stores):
    _, as_ = stores
    return AnesthesiaService(as_)


def _add_doctor_and_nurse(stores) -> tuple[int, int]:
    es, _ = stores
    doctor = es.add(make_employee(role=DOCTOR))
    nurse = es.add(make_employee(role=NURSE))
    return doctor.id, nurse.id


def _add_second_doctor(stores) -> int:
    es, _ = stores
    return es.add(make_employee(last_name="Петров", first_name="Пётр", role=DOCTOR)).id


class TestCreate:
    def test_without_nurse_raises(self, service, stores):
        doctor_id, _ = _add_doctor_and_nurse(stores)
        with pytest.raises(ValueError):
            service.create(doctor_id, 0, "Петров Петр")

    def test_creates_record(self, service, stores):
        doctor_id, nurse_id = _add_doctor_and_nurse(stores)

        an = service.create(doctor_id, nurse_id, "Петров Петр")
        assert an.id is not None
        assert an.doctor_id == doctor_id
        assert an.nurse_id == nurse_id
        assert an.patient_name == "Петров Петр"
        assert an.date == date.today()  # дата подачи проставляется автоматически
        assert an.created_at.tzinfo is not None
        assert an.created_at.utcoffset() == timezone.utc.utcoffset(None)


class TestListMine:
    def test_returns_only_doctor_records(self, service, stores):
        doctor_id, nurse_id = _add_doctor_and_nurse(stores)
        doctor2_id = _add_second_doctor(stores)

        a1 = service.create(doctor_id, nurse_id, "Пациент А")
        service.create(doctor2_id, nurse_id, "Пациент Б")
        a3 = service.create(doctor_id, nurse_id, "Пациент В")

        mine = service.list_mine(doctor_id)
        assert [a.id for a in mine] == [a3.id, a1.id]  # свежие сверху (id DESC)


class TestUpdate:
    def test_updates_own_record(self, service, stores):
        doctor_id, nurse_id = _add_doctor_and_nurse(stores)
        created = service.create(doctor_id, nurse_id, "Пациент А")

        updated = service.update(doctor_id, created.id, "Новый Пациент", nurse_id)
        assert updated.id == created.id
        assert updated.created_at == created.created_at  # факт не меняется
        assert updated.date == created.date  # дата подачи не меняется
        assert updated.patient_name == "Новый Пациент"

    def test_update_foreign_record_raises(self, service, stores):
        doctor_id, nurse_id = _add_doctor_and_nurse(stores)
        doctor2_id = _add_second_doctor(stores)
        created = service.create(doctor_id, nurse_id, "Пациент А")

        with pytest.raises(NotOwnedError):
            service.update(doctor2_id, created.id, "Чужая правка", nurse_id)

    def test_update_missing_raises(self, service, stores):
        doctor_id, nurse_id = _add_doctor_and_nurse(stores)
        with pytest.raises(NotFoundError):
            service.update(doctor_id, 999, "Х", nurse_id)


class TestDelete:
    def test_deletes_own_record(self, service, stores):
        _, as_ = stores
        doctor_id, nurse_id = _add_doctor_and_nurse(stores)
        created = service.create(doctor_id, nurse_id, "Пациент А")
        service.delete(doctor_id, created.id)
        assert as_.get_by_id(created.id) is None

    def test_delete_foreign_record_raises(self, service, stores):
        doctor_id, nurse_id = _add_doctor_and_nurse(stores)
        doctor2_id = _add_second_doctor(stores)
        created = service.create(doctor_id, nurse_id, "Пациент А")
        with pytest.raises(NotOwnedError):
            service.delete(doctor2_id, created.id)

    def test_delete_missing_raises(self, service, stores):
        doctor_id, _ = _add_doctor_and_nurse(stores)
        with pytest.raises(NotFoundError):
            service.delete(doctor_id, 999)
