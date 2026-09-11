"""API-тесты подприложения «Дежурства»: роли, отчёт за смену, доска, выгрузка.

Сотрудники сидятся как в tests/test_web.py (SqliteEmployeeStore + хеши),
вход — POST /login. БД «Дежурств», «Потребностей» и «Компендиума» — временные
(monkeypatch env), чтобы не трогать data/. Время смены фиксируется
(monkeypatch DutyService.now) на 18:00 31.08.2026 UTC — окно открыто.
"""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from docapp.auth.passwords import hash_password
from docapp.domain.employee import DOCTOR, HEAD, NURSE, Employee
from docapp.duty.service import DutyService
from docapp.people.store import SqliteEmployeeStore
from docapp.web.app import create_app

#: Фиксированное «сейчас»: 18:00 31.08.2026 (внутри окна смены 16:00–09:30).
FIXED_NOW = datetime(2026, 8, 31, 18, 0, tzinfo=timezone.utc)

#: Минимальный каталог потребностей (чтобы create_app не трогал data/needs).
CATALOG_YAML = """\
bases:
  Ленская: [травма, урология]
  Таймырская: [экстренная, гной]

groups:
  Неспецифика:
    Атропин: амп
    Дексаметазон: амп
  Растворы:
    Физ 200/250: фл
    Рингер: фл
"""


def _seed(db_path) -> dict:
    with SqliteEmployeeStore(db_path) as es:
        doctor = es.add(
            Employee(
                last_name="Иванов", first_name="Иван", middle_name="Иванович",
                role=DOCTOR, login="ivanov", password_hash=hash_password("secret"),
            )
        )
        head = es.add(
            Employee(
                last_name="Петров", first_name="Пётр",
                role=HEAD, login="petrov", password_hash=hash_password("pass123"),
            )
        )
        nurse = es.add(
            Employee(
                last_name="Сидорова", first_name="Анна", middle_name="Петровна",
                role=NURSE, login="anna", password_hash=hash_password("anna_pass"),
            )
        )
    return {"doctor_id": doctor.id, "head_id": head.id, "nurse_id": nurse.id}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DUTY_TZ", "UTC")
    catalog = tmp_path / "catalog.yaml"
    catalog.write_text(CATALOG_YAML, encoding="utf-8")
    monkeypatch.setenv("NEEDS_CATALOG", str(catalog))
    # Фиксируем «сейчас» для всего сервиса дежурств.
    monkeypatch.setattr(DutyService, "now", lambda self: FIXED_NOW)

    db_path = tmp_path / "web.db"
    _seed(db_path)
    app = create_app(db_path=db_path, secret="test-secret")
    with TestClient(app, follow_redirects=False) as c:
        yield c


def _login(client, login, password):
    r = client.post("/login", data={"login": login, "password": password})
    assert r.status_code == 303
    assert r.headers["location"] == "/"
    return r


class TestDutyPage:
    def test_requires_login(self, client):
        r = client.get("/duty", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/login"

    def test_doctor_access(self, client):
        _login(client, "ivanov", "secret")
        r = client.get("/duty")
        assert r.status_code == 200
        assert "Дежурства" in r.text
        assert "Смена за" in r.text

    def test_head_access(self, client):
        _login(client, "petrov", "pass123")
        r = client.get("/duty")
        assert r.status_code == 200
        assert "Разлиновка за период" in r.text

    def test_nurse_forbidden(self, client):
        _login(client, "anna", "anna_pass")
        assert client.get("/duty").status_code == 403


class TestDoctorReport:
    def test_get_report_open(self, client):
        _login(client, "ivanov", "secret")
        r = client.get("/duty/api/report", params={"base": "Ленская"})
        assert r.status_code == 200
        data = r.json()
        assert data["is_open"] is True
        assert data["shift_date"] == "2026-08-31"
        assert data["report"] is None

    def test_save_and_send(self, client):
        _login(client, "ivanov", "secret")
        r = client.post(
            "/duty/api/report",
            json={"base": "Ленская", "operations": [
                {"operation": "НХО", "start_time": "21:00", "end_time": "21:45"},
            ]},
        )
        assert r.status_code == 200
        rep = r.json()["report"]
        assert rep["status"] == "draft"
        assert rep["shift_date"] == "2026-08-31"
        assert rep["base"] == "Ленская"
        assert rep["operations"][0]["operation"] == "НХО"
        doctor = client.app.state.employees.get_by_login("ivanov")
        assert rep["doctor_id"] == doctor.id

        r = client.post("/duty/api/send", json={"base": "Ленская"})
        assert r.status_code == 200
        assert r.json()["report"]["status"] == "sent"

    def test_save_after_send_reverts_to_draft(self, client):
        # отправленный (sent) отчёт можно править — правка возвращает в draft
        _login(client, "ivanov", "secret")
        client.post(
            "/duty/api/report",
            json={"base": "Ленская", "operations": [
                {"operation": "НХО", "start_time": "21:00", "end_time": "21:45"},
            ]},
        )
        client.post("/duty/api/send", json={"base": "Ленская"})
        r = client.post(
            "/duty/api/report",
            json={"base": "Ленская", "operations": [
                {"operation": "НХО", "start_time": "22:00", "end_time": "22:30"},
            ]},
        )
        assert r.status_code == 200
        assert r.json()["report"]["status"] == "draft"

    def test_send_empty_raises(self, client):
        _login(client, "ivanov", "secret")
        r = client.post("/duty/api/send", json={"base": "Ленская"})
        assert r.status_code == 400

    def test_invalid_time_raises(self, client):
        _login(client, "ivanov", "secret")
        r = client.post(
            "/duty/api/report",
            json={"base": "Ленская", "operations": [
                {"operation": "НХО", "start_time": "22:00", "end_time": "21:00"},
            ]},
        )
        assert r.status_code == 400

    def test_head_cannot_save(self, client):
        _login(client, "petrov", "pass123")
        r = client.post("/duty/api/report", json={"base": "Ленская", "operations": []})
        assert r.status_code == 403


class TestHeadBoardExport:
    def _seed_sent(self, client):
        _login(client, "ivanov", "secret")
        client.post(
            "/duty/api/report",
            json={"base": "Ленская", "operations": [
                {"operation": "НХО", "start_time": "21:00", "end_time": "21:45"},
                {"operation": "Травма", "start_time": "23:00", "end_time": "23:30"},
            ]},
        )
        client.post("/duty/api/send", json={"base": "Ленская"})
        client.post("/logout")

    def test_board_for_head(self, client):
        self._seed_sent(client)
        _login(client, "petrov", "pass123")
        r = client.get("/duty/api/board", params={"from": "2026-08-31", "to": "2026-08-31"})
        assert r.status_code == 200
        reports = r.json()["reports"]
        assert len(reports) == 1
        assert reports[0]["base"] == "Ленская"
        assert reports[0]["doctor_name"] == "Иванов Иван Иванович"
        assert reports[0]["status"] == "sent"
        assert len(reports[0]["operations"]) == 2

    def test_board_forbidden_for_doctor(self, client):
        _login(client, "ivanov", "secret")
        r = client.get("/duty/api/board", params={"from": "2026-08-31", "to": "2026-08-31"})
        assert r.status_code == 403

    def test_xlsx_for_head(self, client):
        # в разлиновку попадают только закрытые (closed) отчёты — закрываем перед выгрузкой
        self._seed_sent(client)
        _login(client, "petrov", "pass123")
        reports = client.get("/duty/api/board", params={"from": "2026-08-31", "to": "2026-08-31"}).json()["reports"]
        client.post("/duty/api/close", json={"report_id": reports[0]["id"]})
        r = client.get("/duty/report.xlsx", params={"from": "2026-08-31", "to": "2026-08-31"})
        assert r.status_code == 200
        assert "spreadsheetml" in r.headers["content-type"]
        assert r.content[:2] == b"PK"  # сигнатура zip/xlsx

    def test_xlsx_forbidden_for_doctor(self, client):
        _login(client, "ivanov", "secret")
        r = client.get("/duty/report.xlsx", params={"from": "2026-08-31", "to": "2026-08-31"})
        assert r.status_code == 403


class TestHeadClose:
    def _seed_sent(self, client):
        _login(client, "ivanov", "secret")
        client.post(
            "/duty/api/report",
            json={"base": "Ленская", "operations": [
                {"operation": "НХО", "start_time": "21:00", "end_time": "21:45"},
            ]},
        )
        client.post("/duty/api/send", json={"base": "Ленская"})
        client.post("/logout")

    def _first_report_id(self, client):
        r = client.get("/duty/api/board", params={"from": "2026-08-31", "to": "2026-08-31"})
        return r.json()["reports"][0]["id"]

    def test_close_and_reopen(self, client):
        self._seed_sent(client)
        _login(client, "petrov", "pass123")
        rid = self._first_report_id(client)
        assert client.post("/duty/api/close", json={"report_id": rid}).json()["ok"] is True
        board = client.get("/duty/api/board", params={"from": "2026-08-31", "to": "2026-08-31"}).json()["reports"]
        assert board[0]["status"] == "closed"
        assert client.post("/duty/api/reopen", json={"report_id": rid}).json()["ok"] is True
        board = client.get("/duty/api/board", params={"from": "2026-08-31", "to": "2026-08-31"}).json()["reports"]
        assert board[0]["status"] == "sent"

    def test_close_shift(self, client):
        self._seed_sent(client)
        _login(client, "petrov", "pass123")
        assert client.post("/duty/api/close-shift", json={"shift_date": "2026-08-31"}).json()["ok"] is True
        board = client.get("/duty/api/board", params={"from": "2026-08-31", "to": "2026-08-31"}).json()["reports"]
        assert board[0]["status"] == "closed"

    def test_close_forbidden_for_doctor(self, client):
        self._seed_sent(client)
        _login(client, "ivanov", "secret")
        assert client.post("/duty/api/close", json={"report_id": 1}).status_code == 403

    def test_close_forbidden_for_nurse(self, client):
        self._seed_sent(client)
        _login(client, "anna", "anna_pass")
        assert client.post("/duty/api/close", json={"report_id": 1}).status_code == 403
