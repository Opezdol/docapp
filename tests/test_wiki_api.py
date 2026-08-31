"""Тесты API подприложения «Компендиум»: страница, статья (HTML), доступ по ролям.

Сервис подменяется фейком без сети; приложение — create_app с временными БД.
"""

import pytest
from fastapi.testclient import TestClient

from docapp.auth.passwords import hash_password
from docapp.domain.employee import DOCTOR, EDITOR, HEAD, HEAD_NURSE, NURSE, Employee
from docapp.storage.sqlite_store import SqliteEmployeeStore
from docapp.web.app import create_app


class FakeService:
    """Фейк WikiService: статья и список — фиксированные, без сети и БД."""

    def stats(self):
        return {"articles": 1, "published": 1, "sources": 0, "sections": 1}

    def articles(self):
        return [{"id": 1, "title": "Атропин", "status": "published", "version": 1,
                 "updated_at": "2026-08-01T10:00:00", "published_revision_id": 1}]

    def article(self, article_id):
        if article_id != 1:
            return None
        return {
            "id": 1, "title": "Атропин", "status": "published",
            "body_md": "# Атропин\n\nДозировка 0.5 мг.", "version": 1,
            "published_revision_id": 1, "created_at": "2026-08-01T10:00:00",
            "updated_at": "2026-08-01T10:00:00", "links": [],
        }

    def article_public(self, article_id):
        return self.article(article_id)


def _seed(db_path) -> dict:
    with SqliteEmployeeStore(db_path) as es:
        doctor = es.add(Employee(last_name="Иванов", first_name="Иван", role=DOCTOR,
                                 login="doc", password_hash=hash_password("secret")))
        head = es.add(Employee(last_name="Петров", first_name="Пётр", role=HEAD,
                               login="head", password_hash=hash_password("pass123")))
        editor = es.add(Employee(last_name="Смирнов", first_name="Сергей", role=EDITOR,
                                 login="ed", password_hash=hash_password("edpass")))
        nurse = es.add(Employee(last_name="Сидорова", first_name="Анна", role=NURSE,
                                login="anna", password_hash=hash_password("annapass")))
        head_nurse = es.add(Employee(last_name="Волкова", first_name="Вера", role=HEAD_NURSE,
                                     login="vera", password_hash=hash_password("verapass")))
    return {"doctor_id": doctor.id, "head_id": head.id, "editor_id": editor.id,
            "nurse_id": nurse.id, "head_nurse_id": head_nurse.id}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("WIKI_DB", str(tmp_path / "wiki" / "wiki.db"))
    db_path = tmp_path / "web.db"
    _seed(db_path)
    app = create_app(db_path=db_path, secret="test-secret")
    app.state.compendium["service"] = FakeService()
    with TestClient(app, follow_redirects=False) as c:
        yield c


def _login(client, login, password):
    return client.post("/login", data={"login": login, "password": password})


class TestCompendiumPage:
    def test_page_requires_login(self, client):
        r = client.get("/compendium", follow_redirects=False)
        assert r.status_code == 303

    def test_page_renders_for_doctor(self, client):
        _login(client, "doc", "secret")
        r = client.get("/compendium")
        assert r.status_code == 200
        assert "Компендиум" in r.text

    def test_nurse_forbidden(self, client):
        _login(client, "anna", "annapass")
        assert client.get("/compendium").status_code == 403


class TestArticlePage:
    def test_article_page_renders_for_doctor(self, client):
        _login(client, "doc", "secret")
        r = client.get("/compendium/articles/1")
        assert r.status_code == 200
        assert "Атропин" in r.text
        assert "Дозировка 0.5 мг" in r.text
        # врач — только чтение, без панели правки
        assert 'id="article-edit"' not in r.text

    def test_article_page_curator_has_edit(self, client):
        _login(client, "ed", "edpass")
        r = client.get("/compendium/articles/1")
        assert r.status_code == 200
        assert 'id="article-edit"' in r.text
        assert 'id="article-edit-panel"' in r.text

    def test_article_page_missing_404(self, client):
        _login(client, "doc", "secret")
        assert client.get("/compendium/articles/999").status_code == 404

    def test_article_page_requires_login(self, client):
        r = client.get("/compendium/articles/1", follow_redirects=False)
        assert r.status_code == 303


class TestEditorRole:
    def test_editor_role_valid_in_domain(self):
        emp = Employee(last_name="X", first_name="Y", role=EDITOR)
        assert emp.role == "editor"
