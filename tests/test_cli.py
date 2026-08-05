"""Тесты консольного скрипта docapp.cli."""

import os

import pytest

from docapp.auth.passwords import verify_password
from docapp.domain.employee import DOCTOR, HEAD, NURSE, Employee
from docapp.storage.sqlite_store import SqliteEmployeeStore


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    """Направляем БД скрипта во временный файл."""
    db = tmp_path / "cli.db"
    monkeypatch.setenv("DOCAPP_DB", str(db))
    return db


def _run_cli(*argv):
    """Запустить docapp.cli с аргументами, вернуть (код, stdout, stderr)."""
    import io
    import sys
    from contextlib import redirect_stdout, redirect_stderr

    from docapp import cli

    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        # подменяем sys.argv, main() сам парсит аргументы
        sys.argv = ["docapp", *argv]
        try:
            code = cli.main()
        except SystemExit as exc:  # argparse кидает SystemExit при ошибке
            code = exc.code if isinstance(exc.code, int) else 2
    return code, out.getvalue(), err.getvalue()


class TestCreateUser:
    def test_creates_doctor_with_generated_password(self, cli_env):
        code, out, _ = _run_cli(
            "user", "Иванов", "Иван", "Иванович",
            "--role", DOCTOR, "--login", "ivanov",
        )
        assert code == 0
        assert "Иванов Иван Иванович" in out
        assert "Логин: ivanov" in out
        # пароль сгенерирован и напечатан ровно один раз
        assert "Пароль:" in out

        with SqliteEmployeeStore(cli_env) as store:
            emp = store.get_by_login("ivanov")
            assert emp is not None
            assert emp.role == DOCTOR
            # извлечём напечатанный пароль и проверим, что он подходит
            password_line = [l for l in out.splitlines() if l.startswith("Пароль:")][0]
            password = password_line.split(":", 1)[1].split("←")[0].strip()
            assert verify_password(password, emp.password_hash)

    def test_creates_head_with_explicit_password(self, cli_env):
        code, out, _ = _run_cli(
            "user", "Петров", "Пётр", "",
            "--role", HEAD, "--login", "petrov", "--password", "secret",
        )
        assert code == 0
        assert "Пароль: задан вами" in out

        with SqliteEmployeeStore(cli_env) as store:
            emp = store.get_by_login("petrov")
            assert emp is not None
            assert emp.role == HEAD
            assert verify_password("secret", emp.password_hash)

    def test_missing_login_returns_error(self, cli_env):
        code, _, err = _run_cli("user", "Иванов", "Иван", "--role", DOCTOR)
        assert code == 2
        assert "обязателен --login" in err

    def test_unknown_role_rejected(self, cli_env):
        code, _, err = _run_cli("user", "Иванов", "Иван", "--role", "boss", "--login", "x")
        assert code == 2  # argparse отклоняет неверное значение


class TestCreateNurse:
    def test_creates_nurse(self, cli_env):
        code, out, _ = _run_cli("nurse", "Сидорова", "Анна", "Петровна")
        assert code == 0
        assert "Сидорова Анна Петровна" in out
        assert "роль: nurse" in out

        with SqliteEmployeeStore(cli_env) as store:
            nurses = store.list_nurses()
            assert len(nurses) == 1
            assert nurses[0].role == NURSE
            assert nurses[0].login is None

    def test_duplicate_login_rejected(self, cli_env):
        _run_cli("user", "Иванов", "Иван", "--login", "ivanov")
        code, _, err = _run_cli("user", "Иванов", "Пётр", "--login", "ivanov")
        # sqlite3.IntegrityError перехвачен скриптом — человеческое сообщение
        assert code == 2
        assert "уже занят" in err

    def test_create_with_buh_id(self, cli_env):
        code, out, _ = _run_cli(
            "user", "Иванов", "Иван", "--role", DOCTOR,
            "--login", "ivanov", "--buh-id", "B-100",
        )
        assert code == 0
        with SqliteEmployeeStore(cli_env) as store:
            emp = store.get_by_login("ivanov")
            assert emp is not None
            assert emp.buh_id == "B-100"

    def test_create_nurse_with_buh_id(self, cli_env):
        code, _, _ = _run_cli("nurse", "Сидорова", "Анна", "--buh-id", "S-7")
        assert code == 0
        with SqliteEmployeeStore(cli_env) as store:
            nurses = store.list_nurses()
            assert len(nurses) == 1
            assert nurses[0].buh_id == "S-7"


class TestSetBuhId:
    def test_set_and_replace(self, cli_env):
        _run_cli("user", "Иванов", "Иван", "--role", DOCTOR, "--login", "ivanov")
        code, out, _ = _run_cli("buh-id", "1", "B-100")
        assert code == 0
        assert "Номер в бухгалтерии: B-100" in out
        with SqliteEmployeeStore(cli_env) as store:
            emp = store.get_by_id(1)
            assert emp is not None
            assert emp.buh_id == "B-100"

        # замена номера
        code, out, _ = _run_cli("buh-id", "1", "B-200")
        assert code == 0
        with SqliteEmployeeStore(cli_env) as store:
            emp = store.get_by_id(1)
            assert emp is not None
            assert emp.buh_id == "B-200"

    def test_unknown_employee(self, cli_env):
        code, _, err = _run_cli("buh-id", "999", "B-100")
        assert code == 2
        assert "не найден" in err

    def test_empty_buh_id_rejected(self, cli_env):
        _run_cli("user", "Иванов", "Иван", "--role", DOCTOR, "--login", "ivanov")
        code, _, err = _run_cli("buh-id", "1", "   ")
        assert code == 2
        assert "не может быть пустым" in err
