"""Тесты сервиса записей: бизнес-правила ввода анестезий."""

from datetime import date, datetime, timezone

import pytest

from docapp.domain.anesthesia import Anesthesia
from docapp.domain.employee import DOCTOR, NURSE, Employee
from docapp.records.service import AnesthesiaService, NotFoundError, NotOwnedError
from docapp.people.store import SqliteEmployeeStore
from docapp.records.store import SqliteAnesthesiaStore


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


class TestMarkDistributed:
    """Пересчёт меток «учтена» — интерфейс сервиса записей для «Распределения»."""

    def _record(self, stores, day):
        """Запись на конкретную дату: `create` всегда пишет сегодняшнюю."""
        es, as_ = stores
        doctor = es.add(make_employee(role=DOCTOR))
        nurse = es.add(make_employee(last_name="Сидорова", first_name="Анна", role=NURSE))
        return as_.add(
            Anesthesia(
                date=day,
                patient_name="Петров Петр Петрович",
                doctor_id=doctor.id,
                nurse_id=nurse.id,
                created_at=datetime(day.year, day.month, day.day, 9, 0, tzinfo=timezone.utc),
            )
        )

    def test_marks_and_reports_counters(self, service, stores):
        first = self._record(stores, date(2026, 9, 2))
        second = self._record(stores, date(2026, 9, 5))
        _, as_ = stores

        result = service.mark_distributed("2026-09-01", "2026-09-30", [first.id])

        assert result == {"cleared": 0, "marked": 1}
        assert as_.get_by_id(first.id).accrued_at is not None
        assert as_.get_by_id(second.id).accrued_at is None

    def test_second_run_clears_the_previous_marks(self, service, stores):
        """Повторный прогон месяца не накапливает устаревшие метки (ТЗ, шаг 4)."""
        first = self._record(stores, date(2026, 9, 2))
        second = self._record(stores, date(2026, 9, 5))
        _, as_ = stores
        service.mark_distributed("2026-09-01", "2026-09-30", [first.id])

        result = service.mark_distributed("2026-09-01", "2026-09-30", [second.id])

        assert result == {"cleared": 1, "marked": 1}
        assert as_.get_by_id(first.id).accrued_at is None
        assert as_.get_by_id(second.id).accrued_at is not None

    def test_other_month_keeps_its_marks(self, service, stores):
        """Метка принадлежит месяцу: пересчёт сентября август не задевает."""
        august = self._record(stores, date(2026, 8, 20))
        september = self._record(stores, date(2026, 9, 2))
        _, as_ = stores
        service.mark_distributed("2026-08-01", "2026-08-31", [august.id])

        service.mark_distributed("2026-09-01", "2026-09-30", [september.id])

        assert as_.get_by_id(august.id).accrued_at is not None
        assert as_.get_by_id(september.id).accrued_at is not None

    def test_record_of_another_month_is_not_marked(self, service, stores):
        """id из другого месяца метку не получает: метка — прогону месяца."""
        august = self._record(stores, date(2026, 8, 20))
        _, as_ = stores

        result = service.mark_distributed("2026-09-01", "2026-09-30", [august.id])

        assert result == {"cleared": 0, "marked": 0}
        assert as_.get_by_id(august.id).accrued_at is None

    def test_empty_list_only_clears(self, service, stores):
        """Прогон без совпадений снимает прежние метки месяца и ничего не ставит."""
        first = self._record(stores, date(2026, 9, 2))
        _, as_ = stores
        service.mark_distributed("2026-09-01", "2026-09-30", [first.id])

        result = service.mark_distributed("2026-09-01", "2026-09-30", [])

        assert result == {"cleared": 1, "marked": 0}
        assert as_.get_by_id(first.id).accrued_at is None

    def test_reversed_period_raises(self, service, stores):
        self._record(stores, date(2026, 9, 2))
        with pytest.raises(ValueError, match="раньше"):
            service.mark_distributed("2026-09-30", "2026-09-01", [1])

    def test_update_keeps_the_mark(self, service, stores):
        """Правка записи метку не снимает: «учтена» — свойство прогона, а не полей."""
        doctor_id, nurse_id = _add_doctor_and_nurse(stores)
        created = service.create(doctor_id, nurse_id, "Пациент А")
        _, as_ = stores
        today = date.today()
        first_day = today.replace(day=1)
        service.mark_distributed(first_day.isoformat(), today.isoformat(), [created.id])

        service.update(doctor_id, created.id, "Пациент Б", nurse_id)

        assert as_.get_by_id(created.id).accrued_at is not None
