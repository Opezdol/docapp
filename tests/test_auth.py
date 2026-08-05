"""Тесты авторизации: хеши, вход, активная сестра."""

import sqlite3

import pytest

from docapp.auth.auth import Authenticator, InvalidCredentials
from docapp.auth.passwords import hash_password, verify_password
from docapp.domain.employee import DOCTOR, HEAD, NURSE, Employee
from docapp.storage.sqlite_store import (
    SqliteActiveNurseStore,
    SqliteEmployeeStore,
)


def make_employee(**overrides):
    base = dict(last_name="Иванов", first_name="Иван", role=DOCTOR)
    base.update(overrides)
    return Employee(**base)


@pytest.fixture
def emp_store(tmp_path):
    s = SqliteEmployeeStore(tmp_path / "test.db")
    yield s
    s.close()


@pytest.fixture
def an_store(tmp_path):
    s = SqliteActiveNurseStore(tmp_path / "test.db")
    yield s
    s.close()


class TestPasswords:
    def test_hash_format(self):
        stored = hash_password("secret")
        parts = stored.split("$")
        assert len(parts) == 4
        assert parts[0] == "pbkdf2_sha256"
        assert parts[1] == "100000"
        assert len(parts[2]) == 32  # 16 байт соли в hex
        assert len(parts[3]) == 64  # sha256 в hex

    def test_hash_is_salted(self):
        a = hash_password("secret")
        b = hash_password("secret")
        assert a != b  # разные соли — разные строки, это норма

    def test_verify_correct(self):
        stored = hash_password("secret")
        assert verify_password("secret", stored) is True

    def test_verify_wrong(self):
        stored = hash_password("secret")
        assert verify_password("wrong", stored) is False

    def test_verify_garbage(self):
        assert verify_password("x", "") is False
        assert verify_password("x", "not-a-hash") is False
        assert verify_password("x", None) is False

    def test_roundtrip_empty_password(self):
        stored = hash_password("")
        assert verify_password("", stored) is True


class TestAuthenticator:
    def test_success(self, emp_store):
        stored = hash_password("secret")
        emp_store.add(make_employee(login="ivanov", password_hash=stored))
        auth = Authenticator(emp_store)
        emp = auth.authenticate("ivanov", "secret")
        assert emp.login == "ivanov"

    def test_head_can_login(self, emp_store):
        stored = hash_password("secret")
        emp_store.add(make_employee(role=HEAD, login="zav", password_hash=stored))
        auth = Authenticator(emp_store)
        emp = auth.authenticate("zav", "secret")
        assert emp.role == HEAD

    def test_wrong_password(self, emp_store):
        stored = hash_password("secret")
        emp_store.add(make_employee(login="ivanov", password_hash=stored))
        auth = Authenticator(emp_store)
        with pytest.raises(InvalidCredentials) as exc:
            auth.authenticate("ivanov", "wrong")
        assert str(exc.value) == "Неверный логин или пароль"

    def test_unknown_login(self, emp_store):
        auth = Authenticator(emp_store)
        with pytest.raises(InvalidCredentials) as exc:
            auth.authenticate("ghost", "secret")
        assert str(exc.value) == "Неверный логин или пароль"

    def test_same_message_for_both_errors(self, emp_store):
        stored = hash_password("secret")
        emp_store.add(make_employee(login="ivanov", password_hash=stored))
        auth = Authenticator(emp_store)
        with pytest.raises(InvalidCredentials) as e1:
            auth.authenticate("ivanov", "wrong")
        with pytest.raises(InvalidCredentials) as e2:
            auth.authenticate("ghost", "secret")
        assert str(e1.value) == str(e2.value)


class TestActiveNurse:
    def _add_doctor_and_nurse(self, store) -> tuple[int, int]:
        doctor = store.add(make_employee(role=DOCTOR))
        nurse = store.add(make_employee(role=NURSE))
        return doctor.id, nurse.id

    def test_get_none_before_set(self, an_store, emp_store):
        doctor_id, _ = self._add_doctor_and_nurse(emp_store)
        assert an_store.get_active_nurse(doctor_id) is None

    def test_set_and_get(self, an_store, emp_store):
        doctor_id, nurse_id = self._add_doctor_and_nurse(emp_store)
        an_store.set_active_nurse(doctor_id, nurse_id)
        assert an_store.get_active_nurse(doctor_id) == nurse_id

    def test_set_overwrites(self, an_store, emp_store):
        doctor_id, nurse_id = self._add_doctor_and_nurse(emp_store)
        nurse2 = emp_store.add(make_employee(role=NURSE, last_name="Козлова"))
        an_store.set_active_nurse(doctor_id, nurse_id)
        an_store.set_active_nurse(doctor_id, nurse2.id)
        assert an_store.get_active_nurse(doctor_id) == nurse2.id

    def test_set_unknown_nurse_raises(self, an_store, emp_store):
        doctor_id, _ = self._add_doctor_and_nurse(emp_store)
        with pytest.raises(sqlite3.IntegrityError):
            an_store.set_active_nurse(doctor_id, 999)

    def test_survives_reopen(self, tmp_path):
        db_path = tmp_path / "test.db"
        with SqliteEmployeeStore(db_path) as es:
            doctor_id, nurse_id = self._add_doctor_and_nurse(es)
        with SqliteActiveNurseStore(db_path) as s:
            s.set_active_nurse(doctor_id, nurse_id)
        with SqliteActiveNurseStore(db_path) as s2:
            assert s2.get_active_nurse(doctor_id) == nurse_id
