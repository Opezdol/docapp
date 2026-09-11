"""Тесты доработок UI: сетка точек у медсестры и хэш коммита в бейдже."""

import pytest
from fastapi.testclient import TestClient

import docapp.config as config
from docapp.auth.passwords import hash_password
from docapp.domain.employee import HEAD_NURSE, NURSE, Employee
from docapp.people.store import SqliteEmployeeStore
from docapp.web.app import create_app

CATALOG_YAML = """\
bases:
  Ленская: [травма, урология]
  Таймырская: [экстренная, гной]

groups:
  Неспецифика:
    Атропин: амп
"""


def _seed(db_path):
    with SqliteEmployeeStore(db_path) as es:
        nurse = es.add(
            Employee(
                last_name="Сидорова", first_name="Анна", middle_name="Петровна",
                role=NURSE, login="anna", password_hash=hash_password("anna_pass"),
            )
        )
        head_nurse = es.add(
            Employee(
                last_name="Орлова", first_name="Елена",
                role=HEAD_NURSE, login="elena", password_hash=hash_password("elena_pass"),
            )
        )
    return nurse.id, head_nurse.id


@pytest.fixture
def client(tmp_path, monkeypatch):
    catalog = tmp_path / "catalog.yaml"
    catalog.write_text(CATALOG_YAML, encoding="utf-8")
    monkeypatch.setenv("NEEDS_CATALOG", str(catalog))
    db_path = tmp_path / "web.db"
    _seed(db_path)
    app = create_app(db_path=db_path, secret="test-secret")
    with TestClient(app, follow_redirects=False) as c:
        yield c


def _login(client, login, password):
    r = client.post("/login", data={"login": login, "password": password})
    assert r.status_code == 303


class TestNursePointGrid:
    def test_nurse_sees_point_grid_not_select(self, client):
        _login(client, "anna", "anna_pass")
        r = client.get("/needs")
        assert r.status_code == 200
        # Сетка точек присутствует; старый <select id="needs-point"> убран.
        assert 'id="needs-points"' in r.text
        assert 'id="needs-point"' not in r.text
        assert "Точки пополнения" in r.text
        # У медсестры НЕТ доски старшей (#needs-board): needs.js должен это
        # выдерживать (раньше безусловный boardEl.addEventListener ронял скрипт).
        assert 'id="needs-board"' not in r.text

    def test_head_nurse_has_board_not_point_grid(self, client):
        _login(client, "elena", "elena_pass")
        r = client.get("/needs")
        assert r.status_code == 200
        # Старшая видит доску, но не сетку точек медсестры.
        assert 'id="needs-board"' in r.text
        assert 'id="needs-points"' not in r.text
        assert 'id="needs-point"' not in r.text
        # Панели «Отчёт и аналитика» больше нет (кнопки ушли на доску под базы).
        assert "Отчёт и аналитика" not in r.text
        assert 'id="needs-report-links"' not in r.text
        # Ссылка на аналитику — в шапке доски.
        assert "Аналитика" in r.text
        # Форма заявки у старшей скрыта (показывается кликом по точке доски).
        assert "needs-form-hidden" in r.text


class TestGitRevisionBadge:
    def test_revision_from_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DOCAPP_GIT_REVISION", "abc1234")
        db_path = tmp_path / "web.db"
        with SqliteEmployeeStore(db_path) as es:
            es.add(
                Employee(
                    last_name="Иванов", first_name="Иван",
                    role=NURSE, login="ivanov", password_hash=hash_password("x"),
                )
            )
        app = create_app(db_path=db_path, secret="s")
        with TestClient(app, follow_redirects=False) as c:
            c.post("/login", data={"login": "ivanov", "password": "x"})
            r = c.get("/")
        assert r.status_code == 200
        assert "abc1234" in r.text
        assert "rev-badge" in r.text


class TestGitRevisionFunction:
    def test_revision_from_file_fallback(self, tmp_path, monkeypatch):
        monkeypatch.delenv("DOCAPP_GIT_REVISION", raising=False)
        monkeypatch.setattr(config, "BASE_DIR", tmp_path)
        (tmp_path / "REVISION").write_text("deadbeef\n", encoding="utf-8")
        assert config.git_revision() == "deadbeef"

    def test_revision_empty_without_sources(self, tmp_path, monkeypatch):
        # Без env, без файла, без git (каталог без .git) — пустая строка.
        monkeypatch.delenv("DOCAPP_GIT_REVISION", raising=False)
        monkeypatch.setattr(config, "BASE_DIR", tmp_path)
        # нет REVISION и .git — subprocess git вернёт пусто (cwd не в репозитории)
        assert config.git_revision() == ""
