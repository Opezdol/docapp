"""Тесты инвариантов домена: Employee и Anesthesia."""

from datetime import date, datetime, timezone, timedelta

import pytest

from docapp.domain.employee import DOCTOR, HEAD, NURSE, Employee
from docapp.domain.anesthesia import Anesthesia


def make_employee(**overrides):
    base = dict(last_name="Иванов", first_name="Иван", role=DOCTOR)
    base.update(overrides)
    return Employee(**base)


def make_anesthesia(**overrides):
    base = dict(
        date=date(2026, 8, 4),
        patient_name="Петров Петр Петрович",
        history_number="12345",
        doctor_id=1,
        nurse_id=2,
        created_at=datetime(2026, 8, 4, 10, 30, tzinfo=timezone.utc),
    )
    base.update(overrides)
    return Anesthesia(**base)


class TestEmployeeValid:
    def test_minimal_doctor(self):
        emp = make_employee()
        assert emp.last_name == "Иванов"
        assert emp.first_name == "Иван"
        assert emp.middle_name == ""
        assert emp.role == DOCTOR
        assert emp.id is None
        assert emp.login is None

    def test_with_login_and_password(self):
        emp = make_employee(login="ivanov", password_hash="abc")
        assert emp.login == "ivanov"
        assert emp.password_hash == "abc"

    def test_nurse_without_login(self):
        emp = make_employee(
            role=NURSE, last_name="Сидорова", first_name="Анна", middle_name="Петровна"
        )
        assert emp.role == NURSE
        assert emp.login is None
        assert emp.password_hash is None

    def test_nurse_can_have_login(self):
        emp = make_employee(
            role=NURSE,
            last_name="Сидорова",
            first_name="Анна",
            login="anna",
            password_hash="abc",
        )
        assert emp.role == NURSE
        assert emp.login == "anna"

    def test_head_with_buh_id(self):
        emp = make_employee(role=HEAD, buh_id="B-100")
        assert emp.buh_id == "B-100"

    def test_full_name_with_middle(self):
        emp = make_employee(middle_name="Иванович")
        assert emp.full_name == "Иванов Иван Иванович"

    def test_full_name_without_middle(self):
        emp = make_employee(middle_name="")
        assert emp.full_name == "Иванов Иван"

    def test_short_name_with_middle(self):
        emp = make_employee(middle_name="Иванович")
        assert emp.short_name == "Иван Иванович"

    def test_short_name_without_middle(self):
        emp = make_employee(middle_name="")
        assert emp.short_name == "Иван"


class TestEmployeeInvalid:
    @pytest.mark.parametrize(
        "kwargs",
        [
            {"last_name": ""},
            {"last_name": "   "},
            {"first_name": ""},
            {"first_name": "  "},
        ],
    )
    def test_empty_names(self, kwargs):
        with pytest.raises(ValueError):
            make_employee(**kwargs)

    def test_bad_role(self):
        with pytest.raises(ValueError):
            make_employee(role="nurse_anesthetist")

    def test_login_without_password(self):
        with pytest.raises(ValueError):
            make_employee(login="ivanov")

    def test_password_without_login(self):
        with pytest.raises(ValueError):
            make_employee(password_hash="abc")

    def test_empty_buh_id(self):
        with pytest.raises(ValueError):
            make_employee(buh_id="  ")


class TestAnesthesiaValid:
    def test_minimal(self):
        an = make_anesthesia()
        assert an.patient_name == "Петров Петр Петрович"
        assert an.history_number == "12345"
        assert an.doctor_id == 1
        assert an.nurse_id == 2
        assert an.id is None

    def test_with_id(self):
        an = make_anesthesia(id=42)
        assert an.id == 42


class TestAnesthesiaInvalid:
    @pytest.mark.parametrize(
        "kwargs",
        [
            {"patient_name": ""},
            {"patient_name": "   "},
            {"history_number": ""},
            {"history_number": "  "},
        ],
    )
    def test_empty_text_fields(self, kwargs):
        with pytest.raises(ValueError):
            make_anesthesia(**kwargs)

    def test_doctor_is_nurse(self):
        with pytest.raises(ValueError):
            make_anesthesia(doctor_id=5, nurse_id=5)

    def test_naive_created_at(self):
        with pytest.raises(ValueError):
            make_anesthesia(created_at=datetime(2026, 8, 4, 10, 30))

    def test_wrong_tz_created_at(self):
        msk = timezone(timedelta(hours=3))
        with pytest.raises(ValueError):
            make_anesthesia(created_at=datetime(2026, 8, 4, 10, 30, tzinfo=msk))


class TestFrozen:
    def test_employee_is_frozen(self):
        emp = make_employee()
        with pytest.raises(Exception):
            emp.last_name = "Другой"

    def test_anesthesia_is_frozen(self):
        an = make_anesthesia()
        with pytest.raises(Exception):
            an.patient_name = "Другой"
