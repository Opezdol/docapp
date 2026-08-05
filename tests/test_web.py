"""Тесты веб-интерфейса: вход, выбор сестры, ввод, правка, удаление."""

import pytest
from fastapi.testclient import TestClient

from docapp.auth.passwords import hash_password
from docapp.domain.employee import DOCTOR, NURSE, Employee
from docapp.storage.sqlite_store import SqliteEmployeeStore
from docapp.web.app import create_app


def _seed(db_path) -> dict:
    """Создать врача, медсестру и заведующего, вернуть их данные."""
    with SqliteEmployeeStore(db_path) as es:
        doctor = es.add(
            Employee(
                last_name="Иванов",
                first_name="Иван",
                middle_name="Иванович",
                role=DOCTOR,
                login="ivanov",
                password_hash=hash_password("secret"),
            )
        )
        nurse = es.add(
            Employee(
                last_name="Сидорова",
                first_name="Анна",
                role=NURSE,
            )
        )
        head = es.add(
            Employee(
                last_name="Петров",
                first_name="Пётр",
                role=DOCTOR,
                login="petrov",
                password_hash=hash_password("pass123"),
            )
        )
    return {"doctor_id": doctor.id, "nurse_id": nurse.id, "head_id": head.id}


@pytest.fixture
def client(tmp_path):
    db_path = tmp_path / "web.db"
    _seed(db_path)
    app = create_app(db_path=db_path, secret="test-secret")
    # follow_redirects=False: проверяем сами 303-редиректы,
    # а не финальную страницу (starlette>=1.3 по умолчанию следует).
    with TestClient(app, follow_redirects=False) as c:
        yield c


def _login(client, login="ivanov", password="secret"):
    return client.post("/login", data={"login": login, "password": password})


class TestLogin:
    def test_root_redirects_to_login(self, client):
        r = client.get("/", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/login"

    def test_login_success(self, client):
        r = _login(client)
        assert r.status_code == 303
        assert r.headers["location"] == "/"

    def test_login_wrong_password(self, client):
        r = _login(client, password="wrong")
        assert r.status_code == 401
        assert "Неверный логин или пароль" in r.text

    def test_login_unknown_user(self, client):
        r = _login(client, login="ghost")
        assert r.status_code == 401

    def test_index_after_login(self, client):
        _login(client)
        r = client.get("/")
        assert r.status_code == 200
        assert "Здравствуйте, Иван Иванович" in r.text

    def test_logout(self, client):
        _login(client)
        r = client.post("/logout")
        assert r.status_code == 303
        assert r.headers["location"] == "/login"
        assert client.get("/", follow_redirects=False).status_code == 303


class TestNurseSelection:
    def test_choose_nurse(self, client):
        _login(client)
        r = client.post("/nurse", data={"nurse_id": 2})
        assert r.status_code == 303
        assert r.headers["location"] == "/"

    def test_active_nurse_shown_on_index(self, client):
        _login(client)
        client.post("/nurse", data={"nurse_id": 2})
        r = client.get("/")
        assert "Сидорова Анна" in r.text


class TestAnesthesiaFlow:
    def _add(self, client, **overrides):
        data = {
            "procedure_date": "2026-08-04",
            "patient_name": "Петров Петр Петрович",
            "history_number": "12345",
        }
        data.update(overrides)
        return client.post("/anesthesia", data=data)

    def test_add_without_nurse_shows_error(self, client):
        _login(client)
        r = self._add(client)
        assert r.status_code == 303
        page = client.get("/")
        assert "Сначала выберите медсестру" in page.text

    def test_add_with_nurse(self, client):
        _login(client)
        client.post("/nurse", data={"nurse_id": 2})
        r = self._add(client)
        assert r.status_code == 303
        page = client.get("/")
        assert "Петров Петр Петрович" in page.text
        assert "и/б 12345" in page.text

    def test_add_future_date_shows_error(self, client):
        _login(client)
        client.post("/nurse", data={"nurse_id": 2})
        self._add(client, procedure_date="2099-01-01")
        page = client.get("/")
        assert "Дата не может быть в будущем" in page.text

    def test_edit_own_record(self, client):
        _login(client)
        client.post("/nurse", data={"nurse_id": 2})
        self._add(client)
        # найти id записи через страницу правки нельзя напрямую —
        # берём первую ссылку «Изменить» со страницы
        page = client.get("/")
        assert "/anesthesia/1/edit" in page.text
        r = client.post(
            "/anesthesia/1/update",
            data={
                "procedure_date": "2026-08-04",
                "patient_name": "Новый Пациент",
                "history_number": "777",
                "nurse_id": 2,
            },
        )
        assert r.status_code == 303
        page = client.get("/")
        assert "Новый Пациент" in page.text
        assert "Петров Петр Петрович" not in page.text

    def test_edit_foreign_record_forbidden(self, client):
        _login(client)
        client.post("/nurse", data={"nurse_id": 2})
        self._add(client)
        # второй врач (заведующий) не может править чужую запись
        _login(client, login="petrov", password="pass123")
        r = client.post(
            "/anesthesia/1/update",
            data={
                "procedure_date": "2026-08-04",
                "patient_name": "Взлом",
                "history_number": "1",
                "nurse_id": 2,
            },
        )
        assert r.status_code == 303
        page = client.get("/")
        assert "Нельзя изменять чужую запись" in page.text

    def test_delete_own_record(self, client):
        _login(client)
        client.post("/nurse", data={"nurse_id": 2})
        self._add(client)
        r = client.post("/anesthesia/1/delete")
        assert r.status_code == 303
        page = client.get("/")
        assert "Пока нет записей" in page.text

    def test_delete_foreign_record_forbidden(self, client):
        _login(client)
        client.post("/nurse", data={"nurse_id": 2})
        self._add(client)
        _login(client, login="petrov", password="pass123")
        r = client.post("/anesthesia/1/delete")
        assert r.status_code == 303
        page = client.get("/")
        assert "Нельзя изменять чужую запись" in page.text


class TestPwa:
    def test_manifest(self, client):
        r = client.get("/static/manifest.json")
        assert r.status_code == 200
        assert "docapp" in r.text

    def test_icons(self, client):
        assert client.get("/static/icon-192.png").status_code == 200
        assert client.get("/static/icon-512.png").status_code == 200

    def test_service_worker(self, client):
        r = client.get("/static/sw.js")
        assert r.status_code == 200

    def test_index_has_manifest_link(self, client):
        _login(client)
        r = client.get("/")
        assert 'rel="manifest"' in r.text
