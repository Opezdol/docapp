"""API-тесты подприложения «Потребности»: роли, заявки, доска, отчёты (T6).

Сотрудники сидятся как в tests/test_web.py (SqliteEmployeeStore + хеши
паролей), вход — POST /login. Каталог и БД «Потребностей» — временные
(monkeypatch NEEDS_DB/NEEDS_CATALOG), чтобы не зависеть от seed-файла;
консультант тоже уводится во временный каталог, чтобы не трогать data/consult.
Неделя во всех запросах фиксированная (WEEK), чтобы тесты не зависели
от даты запуска.
"""

import pytest
from fastapi.testclient import TestClient

from docapp.auth.passwords import hash_password
from docapp.domain.employee import DOCTOR, HEAD, HEAD_NURSE, NURSE, Employee
from docapp.storage.sqlite_store import SqliteEmployeeStore
from docapp.web.app import create_app

#: Фиксированная неделя (понедельник) для всех запросов тестов.
WEEK = "2026-08-10"

#: Временный каталог: две базы по две точки, группы с особой «Растворы».
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
    """Врач, медсестра, старшая сестра и заведующий — с логинами и паролями."""
    with SqliteEmployeeStore(db_path) as es:
        doctor = es.add(
            Employee(
                last_name="Иванов", first_name="Иван", middle_name="Иванович",
                role=DOCTOR, login="ivanov", password_hash=hash_password("secret"),
            )
        )
        nurse = es.add(
            Employee(
                last_name="Сидорова", first_name="Анна", middle_name="Петровна",
                role=NURSE, login="anna", password_hash=hash_password("anna_pass"),
            )
        )
        head_nurse = es.add(
            Employee(
                last_name="Орлова", first_name="Елена", middle_name="Викторовна",
                role=HEAD_NURSE, login="elena", password_hash=hash_password("elena_pass"),
            )
        )
        head = es.add(
            Employee(
                last_name="Петров", first_name="Пётр",
                role=HEAD, login="petrov", password_hash=hash_password("pass123"),
            )
        )
    return {
        "doctor_id": doctor.id,
        "nurse_id": nurse.id,
        "head_nurse_id": head_nurse.id,
        "head_id": head.id,
    }


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Приложение с временными БД: свои needs.db/catalog.yaml, консультант в tmp."""
    monkeypatch.setenv("NEEDS_DB", str(tmp_path / "needs.db"))
    catalog = tmp_path / "catalog.yaml"
    catalog.write_text(CATALOG_YAML, encoding="utf-8")
    monkeypatch.setenv("NEEDS_CATALOG", str(catalog))
    # консультант — тоже во временный каталог, чтобы не трогать data/consult
    monkeypatch.setenv("CONSULT_INDEX_DIR", str(tmp_path / "consult"))
    monkeypatch.setenv("CONSULT_DOCS_DIR", str(tmp_path / "consult" / "documents"))
    db_path = tmp_path / "web.db"
    _seed(db_path)
    app = create_app(db_path=db_path, secret="test-secret")
    with TestClient(app, follow_redirects=False) as c:
        yield c


def _login(client, login, password):
    """Войти; возвращает ответ POST /login (ожидается 303 на /)."""
    r = client.post("/login", data={"login": login, "password": password})
    assert r.status_code == 303
    assert r.headers["location"] == "/"
    return r


class TestNeedsPage:
    def test_requires_login(self, client):
        r = client.get("/needs", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/login"

    def test_role_access(self, client):
        # врач — 403 везде (ADR-11)
        _login(client, "ivanov", "secret")
        assert client.get("/needs").status_code == 403
        client.post("/logout")
        # медсестра — 200
        _login(client, "anna", "anna_pass")
        r = client.get("/needs")
        assert r.status_code == 200
        assert "Потребности" in r.text
        client.post("/logout")
        # старшая сестра — 200
        _login(client, "elena", "elena_pass")
        assert client.get("/needs").status_code == 200
        client.post("/logout")
        # заведующий — 200
        _login(client, "petrov", "pass123")
        assert client.get("/needs").status_code == 200


class TestCatalog:
    def test_catalog_for_nurse(self, client):
        _login(client, "anna", "anna_pass")
        r = client.get("/needs/api/catalog")
        assert r.status_code == 200
        data = r.json()
        assert set(data["bases"]) == {"Ленская", "Таймырская"}
        assert data["bases"]["Ленская"] == ["травма", "урология"]
        assert data["groups"]["Неспецифика"]["Атропин"] == "амп"
        assert data["groups"]["Растворы"]["Рингер"] == "фл"

    def test_catalog_forbidden_for_doctor(self, client):
        _login(client, "ivanov", "secret")
        assert client.get("/needs/api/catalog").status_code == 403


class TestRequests:
    def test_save_request_with_snapshots(self, client):
        _login(client, "anna", "anna_pass")
        r = client.post(
            "/needs/api/request",
            json={
                "base": "Ленская",
                "point": "травма",
                "week": WEEK,
                "lines": [{"item": "Атропин", "qty": 5}, {"item": "Физ 200/250", "qty": 3}],
            },
        )
        assert r.status_code == 200
        req = r.json()["request"]
        assert req["base"] == "Ленская"
        assert req["point"] == "травма"
        assert req["week_start"] == WEEK
        assert req["status"] == "draft"
        nurse = client.app.state.employees.get_by_login("anna")
        assert req["author_id"] == nurse.id
        # строки пришли со снимками unit/grp из каталога
        lines = {line["item"]: line for line in req["lines"]}
        assert lines["Атропин"]["unit"] == "амп"
        assert lines["Атропин"]["grp"] == "Неспецифика"
        assert lines["Атропин"]["qty"] == 5
        assert lines["Физ 200/250"]["unit"] == "фл"
        assert lines["Физ 200/250"]["grp"] == "Растворы"

    def test_doctor_cannot_save(self, client):
        _login(client, "ivanov", "secret")
        r = client.post(
            "/needs/api/request",
            json={"base": "Ленская", "point": "травма", "lines": [{"item": "Атропин", "qty": 1}]},
        )
        assert r.status_code == 403

    def test_foreign_draft_hidden_until_sent(self, client):
        _login(client, "anna", "anna_pass")
        r = client.post(
            "/needs/api/request",
            json={
                "base": "Ленская", "point": "травма", "week": WEEK,
                "lines": [{"item": "Атропин", "qty": 2}],
            },
        )
        assert r.status_code == 200
        client.post("/logout")

        # вторая медсестра
        other = Employee(
            last_name="Козлова", first_name="Мария", role=NURSE,
            login="masha", password_hash=hash_password("masha_pass"),
        )
        client.app.state.employees.add(other)
        _login(client, "masha", "masha_pass")
        # чужой черновик не виден (404) и не правится (403)
        r = client.get("/needs/api/request", params={"base": "Ленская", "point": "травма", "week": WEEK})
        assert r.status_code == 404
        r = client.post(
            "/needs/api/request",
            json={
                "base": "Ленская", "point": "травма", "week": WEEK,
                "lines": [{"item": "Атропин", "qty": 9}],
            },
        )
        assert r.status_code == 403
        client.post("/logout")

        # автор отправляет заявку — теперь чужая, но отправленная видна
        _login(client, "anna", "anna_pass")
        r = client.post("/needs/api/request/submit", json={"base": "Ленская", "point": "травма", "week": WEEK})
        assert r.status_code == 200
        assert r.json()["request"]["status"] == "sent"
        client.post("/logout")

        _login(client, "masha", "masha_pass")
        r = client.get("/needs/api/request", params={"base": "Ленская", "point": "травма", "week": WEEK})
        assert r.status_code == 200
        assert r.json()["status"] == "sent"

    def test_closed_week_blocks_save(self, client):
        _login(client, "elena", "elena_pass")
        r = client.post("/needs/api/close", json={"base": "Ленская", "week": WEEK})
        assert r.status_code == 200
        client.post("/logout")

        _login(client, "anna", "anna_pass")
        r = client.post(
            "/needs/api/request",
            json={
                "base": "Ленская", "point": "травма", "week": WEEK,
                "lines": [{"item": "Атропин", "qty": 2}],
            },
        )
        assert r.status_code == 409


class TestBoard:
    def _seed_board(self, client):
        """Заявка медсестры (отправленная) на травме; урология пуста."""
        _login(client, "anna", "anna_pass")
        client.post(
            "/needs/api/request",
            json={
                "base": "Ленская", "point": "травма", "week": WEEK,
                "lines": [{"item": "Атропин", "qty": 2}],
            },
        )
        client.post("/needs/api/request/submit", json={"base": "Ленская", "point": "травма", "week": WEEK})
        client.post("/logout")

    def test_board_for_head_nurse(self, client):
        self._seed_board(client)
        _login(client, "elena", "elena_pass")
        r = client.get("/needs/api/board", params={"week": WEEK})
        assert r.status_code == 200
        cells = {(c["base"], c["point"]): c for c in r.json()["board"]}
        # все точки обеих баз
        assert set(cells) == {
            ("Ленская", "травма"), ("Ленская", "урология"),
            ("Таймырская", "экстренная"), ("Таймырская", "гной"),
        }
        # статусы, имена авторов, флаг закрытия недели
        assert cells[("Ленская", "травма")]["status"] == "sent"
        assert cells[("Ленская", "травма")]["author_name"] == "Сидорова Анна Петровна"
        assert cells[("Ленская", "урология")]["status"] == "none"
        assert cells[("Ленская", "урология")]["author_name"] is None
        assert cells[("Ленская", "травма")]["is_closed"] is False
        assert cells[("Таймырская", "экстренная")]["is_closed"] is False

    def test_board_forbidden_for_nurse(self, client):
        _login(client, "anna", "anna_pass")
        assert client.get("/needs/api/board").status_code == 403


class TestCloseReopen:
    def test_close_returns_unsent_points(self, client):
        _login(client, "anna", "anna_pass")
        client.post(
            "/needs/api/request",
            json={
                "base": "Ленская", "point": "травма", "week": WEEK,
                "lines": [{"item": "Атропин", "qty": 2}],
            },
        )
        client.post("/logout")

        _login(client, "elena", "elena_pass")
        r = client.post("/needs/api/close", json={"base": "Ленская", "week": WEEK})
        assert r.status_code == 200
        data = r.json()
        assert data["closed"] is True
        # заявка травмы не отправлена — обе точки базы в предупреждении
        assert set(data["unsent_points"]) == {"травма", "урология"}

    def test_reopen_after_close(self, client):
        _login(client, "elena", "elena_pass")
        client.post("/needs/api/close", json={"base": "Ленская", "week": WEEK})
        r = client.post("/needs/api/reopen", json={"base": "Ленская", "week": WEEK})
        assert r.status_code == 200
        assert r.json() == {"reopened": True}

    def test_nurse_cannot_close(self, client):
        _login(client, "anna", "anna_pass")
        r = client.post("/needs/api/close", json={"base": "Ленская", "week": WEEK})
        assert r.status_code == 403


class TestReport:
    def _seed_sent_requests(self, client):
        """Две отправленные заявки Ленской за WEEK (Атропин и Физ 200/250)."""
        _login(client, "anna", "anna_pass")
        client.post(
            "/needs/api/request",
            json={
                "base": "Ленская", "point": "травма", "week": WEEK,
                "lines": [{"item": "Атропин", "qty": 2}, {"item": "Физ 200/250", "qty": 4}],
            },
        )
        client.post(
            "/needs/api/request",
            json={
                "base": "Ленская", "point": "урология", "week": WEEK,
                "lines": [{"item": "Атропин", "qty": 1}],
            },
        )
        client.post("/needs/api/request/submit", json={"base": "Ленская", "point": "травма", "week": WEEK})
        client.post("/needs/api/request/submit", json={"base": "Ленская", "point": "урология", "week": WEEK})
        client.post("/logout")

    def test_report_xlsx_for_head_nurse(self, client):
        self._seed_sent_requests(client)
        _login(client, "elena", "elena_pass")
        r = client.get("/needs/report.xlsx", params={"base": "Ленская", "week": WEEK})
        assert r.status_code == 200
        assert "spreadsheetml" in r.headers["content-type"]
        assert r.content[:2] == b"PK"  # сигнатура zip/xlsx

    def test_report_xlsx_forbidden_for_nurse(self, client):
        _login(client, "anna", "anna_pass")
        r = client.get("/needs/report.xlsx", params={"base": "Ленская", "week": WEEK})
        assert r.status_code == 403

    def test_report_page_html(self, client):
        self._seed_sent_requests(client)
        _login(client, "elena", "elena_pass")
        r = client.get("/needs/report", params={"base": "Ленская", "week": WEEK})
        assert r.status_code == 200
        assert "Отчёт по потребностям" in r.text
        assert "База:" in r.text  # html_table отрендерен через |safe


class TestAnalytics:
    def _seed_sent_request(self, client):
        _login(client, "anna", "anna_pass")
        client.post(
            "/needs/api/request",
            json={
                "base": "Ленская", "point": "травма", "week": WEEK,
                "lines": [{"item": "Атропин", "qty": 2}, {"item": "Физ 200/250", "qty": 3}],
            },
        )
        client.post("/needs/api/request/submit", json={"base": "Ленская", "point": "травма", "week": WEEK})
        client.post("/logout")

    def test_analytics_for_head_nurse(self, client):
        self._seed_sent_request(client)
        _login(client, "elena", "elena_pass")
        r = client.get(
            "/needs/api/analytics",
            params={"from": WEEK, "to": WEEK},
        )
        assert r.status_code == 200
        data = r.json()
        assert "solutions" in data
        assert "groups" in data
        assert data["groups"]["Неспецифика"]["Атропин"]["qty"] == 2
        # особая группа «Растворы» идёт поточково в solutions
        assert data["solutions"]["Физ 200/250"]["ИТОГО"] == 3

    def test_analytics_forbidden_for_nurse(self, client):
        _login(client, "anna", "anna_pass")
        r = client.get(
            "/needs/api/analytics",
            params={"from": WEEK, "to": WEEK},
        )
        assert r.status_code == 403
