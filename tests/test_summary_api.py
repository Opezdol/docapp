"""Тесты «Сводки» через HTTP: страница, права, данные и запись из соседа.

Сводка ходит за записями к модулю `records`, поэтому здесь же проверяется, что
запись, поданная через главную страницу, сразу видна в сводке — то есть модули
смотрят на одни данные через интерфейсы, а не каждый в свою копию.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from docapp.auth.passwords import hash_password
from docapp.domain.employee import DOCTOR, HEAD, NURSE, Employee
from docapp.people.store import SqliteEmployeeStore
from docapp.web.app import create_app
from factories import make_db


def _seed(db_path) -> dict:
    """Заведующий, врач и медсестра с логинами; плюс одна запись врача."""
    with SqliteEmployeeStore(db_path) as employees:
        head = employees.add(
            Employee(last_name="Иванов", first_name="Иван", role=HEAD,
                     login="head", password_hash=hash_password("headpass"))
        )
        doctor = employees.add(
            Employee(last_name="Петров", first_name="Пётр", role=DOCTOR,
                     login="doc", password_hash=hash_password("docpass"))
        )
        nurse = employees.add(
            Employee(last_name="Сидорова", first_name="Анна", role=NURSE,
                     login="nurse", password_hash=hash_password("nursepass"))
        )
    return {"head": head, "doctor": doctor, "nurse": nurse}


@pytest.fixture
def client(tmp_path):
    db = make_db(tmp_path, seed=False)
    ids = _seed(db)
    app = create_app(db_path=db, secret="test-secret")
    with TestClient(app, follow_redirects=False) as c:
        c.ids = ids
        yield c


def login(client, who: str) -> None:
    """Войти под сотрудником: head/doc/nurse."""
    passwords = {"head": "headpass", "doc": "docpass", "nurse": "nursepass"}
    response = client.post("/login", data={"login": who, "password": passwords[who]})
    assert response.status_code == 303


class TestPage:
    def test_requires_login(self, client):
        assert client.get("/summary").status_code == 303
        assert client.get("/summary").headers["location"] == "/login"

    def test_renders_for_head(self, client):
        login(client, "head")
        response = client.get("/summary")
        assert response.status_code == 200
        assert "Сводка по анестезиям" in response.text
        assert "Всего записей" in response.text

    def test_menu_has_summary(self, client):
        login(client, "head")
        assert 'href="/summary"' in client.get("/").text

    def test_bad_split_is_a_message_not_a_crash(self, client):
        """Неизвестный разрез — страница с ошибкой 400, а не 500."""
        login(client, "head")
        response = client.get("/summary?by=base")
        assert response.status_code == 400
        assert "разрез" in response.text


class TestApi:
    def test_requires_login(self, client):
        assert client.get("/summary/api/aggregate").status_code == 401

    def test_head_gets_all(self, client):
        login(client, "head")
        payload = client.get("/summary/api/aggregate").json()
        assert payload["scope"] == "all"
        assert payload["by"] == "doctor"
        assert payload["total"] == 0                      # записей ещё никто не подал

    def test_unknown_split_is_400(self, client):
        login(client, "head")
        response = client.get("/summary/api/aggregate?by=base")
        assert response.status_code == 400
        assert "by" in response.json()["detail"]

    def test_period_from_parameters(self, client):
        login(client, "head")
        payload = client.get("/summary/api/aggregate?from=2020-01-01&to=2020-12-31").json()
        assert payload["from"] == "2020-01-01" and payload["to"] == "2020-12-31"

    def test_doctor_sees_only_his(self, client):
        login(client, "doc")
        payload = client.get("/summary/api/aggregate").json()
        assert payload["scope"] == "own"

    def test_nurse_sees_only_hers(self, client):
        login(client, "nurse")
        payload = client.get("/summary/api/aggregate?by=nurse").json()
        assert payload["scope"] == "own"


class TestSeesRecordsFromNeighbourModule:
    def test_record_added_on_main_page_appears_in_summary(self, client):
        """Сквозная проверка шва: запись из модуля `records` видна «Сводке»."""
        login(client, "doc")
        created = client.post(
            "/anesthesia",
            data={"patient_name": "Пациент А.", "nurse_id": str(client.ids["nurse"].id)},
        )
        assert created.status_code == 303

        payload = client.get("/summary/api/aggregate").json()
        assert payload["total"] == 1
        assert payload["rows"] == [
            {"key": str(client.ids["doctor"].id), "count": 1, "name": "Петров Пётр"}
        ]

    def test_head_sees_records_of_other_doctors(self, client):
        """Заведующий видит чужие записи: у него records.view_all."""
        login(client, "doc")
        client.post(
            "/anesthesia",
            data={"patient_name": "Пациент Б.", "nurse_id": str(client.ids["nurse"].id)},
        )
        client.post("/logout")

        login(client, "head")
        payload = client.get("/summary/api/aggregate").json()
        assert payload["scope"] == "all" and payload["total"] == 1
