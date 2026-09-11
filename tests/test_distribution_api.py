"""Тесты «Распределения» через HTTP: страница, доступ, данные и запись из соседа.

Раздел целиком принадлежит заведующему (`distribution.manage`, ADR-0024): врач,
медсестра и старшая сестра не видят ни пункта меню, ни данных.

Ходит раздел за записями к модулю `records`, поэтому здесь же проверяется, что
запись, поданная через главную страницу, сразу видна в распределении — модули
смотрят на одни данные через интерфейсы, а не каждый в свою копию.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from docapp.auth.passwords import hash_password
from docapp.domain.employee import DOCTOR, HEAD, HEAD_NURSE, NURSE, Employee
from docapp.people.store import SqliteEmployeeStore
from docapp.web.app import create_app
from factories import make_db

PASSWORDS = {
    "head": "headpass",
    "doc": "docpass",
    "nurse": "nursepass",
    "headnurse": "hnpass",
}


def _seed(db_path) -> dict:
    """Заведующий, врач, медсестра и старшая сестра с логинами."""
    with SqliteEmployeeStore(db_path) as employees:
        head = employees.add(
            Employee(last_name="Иванов", first_name="Иван", role=HEAD,
                     login="head", password_hash=hash_password(PASSWORDS["head"]))
        )
        doctor = employees.add(
            Employee(last_name="Петров", first_name="Пётр", role=DOCTOR,
                     login="doc", password_hash=hash_password(PASSWORDS["doc"]))
        )
        nurse = employees.add(
            Employee(last_name="Сидорова", first_name="Анна", role=NURSE,
                     login="nurse", password_hash=hash_password(PASSWORDS["nurse"]))
        )
        head_nurse = employees.add(
            Employee(last_name="Волкова", first_name="Вера", role=HEAD_NURSE,
                     login="headnurse", password_hash=hash_password(PASSWORDS["headnurse"]))
        )
    return {"head": head, "doctor": doctor, "nurse": nurse, "head_nurse": head_nurse}


@pytest.fixture
def client(tmp_path):
    db = make_db(tmp_path, seed=False)
    ids = _seed(db)
    app = create_app(db_path=db, secret="test-secret")
    with TestClient(app, follow_redirects=False) as c:
        c.ids = ids
        yield c


def login(client, who: str) -> None:
    """Войти под сотрудником: head/doc/nurse/headnurse."""
    response = client.post("/login", data={"login": who, "password": PASSWORDS[who]})
    assert response.status_code == 303


class TestPage:
    def test_requires_login(self, client):
        assert client.get("/distribution").status_code == 303
        assert client.get("/distribution").headers["location"] == "/login"

    def test_renders_for_head(self, client):
        login(client, "head")
        response = client.get("/distribution")
        assert response.status_code == 200
        assert "Распределение" in response.text
        assert "Всего записей" in response.text

    def test_menu_has_distribution(self, client):
        login(client, "head")
        assert 'href="/distribution"' in client.get("/").text

    def test_bad_split_is_a_message_not_a_crash(self, client):
        """Неизвестный разрез — страница с ошибкой 400, а не 500."""
        login(client, "head")
        response = client.get("/distribution?by=base")
        assert response.status_code == 400
        assert "разрез" in response.text


class TestAccess:
    """Раздел закрыт для всех, кроме заведующего (ADR-0024)."""

    @pytest.mark.parametrize("who", ["doc", "nurse", "headnurse"])
    def test_page_is_forbidden(self, client, who):
        login(client, who)
        assert client.get("/distribution").status_code == 403

    @pytest.mark.parametrize("who", ["doc", "nurse", "headnurse"])
    def test_api_is_forbidden(self, client, who):
        login(client, who)
        assert client.get("/distribution/api/aggregate").status_code == 403

    @pytest.mark.parametrize("who", ["doc", "nurse", "headnurse"])
    def test_menu_item_is_hidden(self, client, who):
        """Пункта меню нет ни у врача, ни у медсестры, ни у старшей сестры."""
        login(client, who)
        assert 'href="/distribution"' not in client.get("/").text


class TestApi:
    def test_requires_login(self, client):
        assert client.get("/distribution/api/aggregate").status_code == 401

    def test_head_gets_everyone(self, client):
        login(client, "head")
        payload = client.get("/distribution/api/aggregate").json()
        assert payload["by"] == "doctor"
        assert payload["total"] == 0                      # записей ещё никто не подал

    def test_unknown_split_is_400(self, client):
        login(client, "head")
        response = client.get("/distribution/api/aggregate?by=base")
        assert response.status_code == 400
        assert "by" in response.json()["detail"]

    def test_period_from_parameters(self, client):
        login(client, "head")
        payload = client.get("/distribution/api/aggregate?from=2020-01-01&to=2020-12-31").json()
        assert payload["from"] == "2020-01-01" and payload["to"] == "2020-12-31"


class TestSeesRecordsFromNeighbourModule:
    def test_record_added_on_main_page_appears_in_distribution(self, client):
        """Сквозная проверка шва: запись из модуля `records` видна «Распределению»."""
        login(client, "doc")
        created = client.post(
            "/anesthesia",
            data={"patient_name": "Пациент А.", "nurse_id": str(client.ids["nurse"].id)},
        )
        assert created.status_code == 303
        client.post("/logout")

        login(client, "head")
        payload = client.get("/distribution/api/aggregate").json()
        assert payload["total"] == 1
        assert payload["rows"] == [
            {"key": str(client.ids["doctor"].id), "count": 1, "name": "Петров Пётр"}
        ]
