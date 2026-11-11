"""Панель заведующего: сотрудники (ADR-0030, ТЗ панели, шаг 1.2).

Проверяется поведение, а не внутренности: кто видит панель, что происходит при
создании, правке, увольнении и сбросе пароля. Правила службы (нельзя уволить
себя, логин занят, пароль короткий) проверяются через HTTP — так видно, что
человек получает текст, а не 500.
"""

import re

import pytest
from fastapi.testclient import TestClient

from docapp.auth.passwords import hash_password
from docapp.domain.employee import DOCTOR, HEAD, HEAD_NURSE, NURSE, Employee
from docapp.people.store import SqliteEmployeeStore
from docapp.web.app import create_app


def _seed(db_path) -> dict:
    """Заведующий, врач, медсестра с учёткой, старшая сестра, медсестра без учётки."""
    with SqliteEmployeeStore(db_path) as store:
        head = store.add(
            Employee(
                last_name="Зайцев",
                first_name="Захар",
                role=HEAD,
                login="zav",
                password_hash=hash_password("zav_pass"),
            )
        )
        doctor = store.add(
            Employee(
                last_name="Иванов",
                first_name="Иван",
                role=DOCTOR,
                login="ivanov",
                password_hash=hash_password("secret"),
            )
        )
        nurse = store.add(
            Employee(
                last_name="Сидорова",
                first_name="Анна",
                role=NURSE,
                login="anna",
                password_hash=hash_password("anna_pass"),
            )
        )
        head_nurse = store.add(
            Employee(
                last_name="Волкова",
                first_name="Вера",
                role=HEAD_NURSE,
                login="vera",
                password_hash=hash_password("vera_pass"),
            )
        )
        no_account = store.add(
            Employee(last_name="Кузнецова", first_name="Ольга", role=NURSE)
        )
    return {
        "head": head,
        "doctor": doctor,
        "nurse": nurse,
        "head_nurse": head_nurse,
        "no_account": no_account,
    }


@pytest.fixture
def panel(tmp_path):
    db_path = tmp_path / "admin.db"
    ids = _seed(db_path)
    app = create_app(db_path=db_path, secret="test-secret")
    with TestClient(app, follow_redirects=False) as client:
        yield client, ids


def _login(client, login: str, password: str):
    return client.post("/login", data={"login": login, "password": password})


def _login_head(client):
    assert _login(client, "zav", "zav_pass").status_code == 303


def _create(client, **overrides):
    data = {
        "last_name": "Петров",
        "first_name": "Пётр",
        "middle_name": "Петрович",
        "role": DOCTOR,
        "login": "petrov",
        "password": "petrov_pass",
    }
    data.update(overrides)
    return client.post("/admin/employee", data=data)


__all__ = ["panel"]


class TestAccess:
    def test_anonymous_is_sent_to_login(self, panel):
        client, _ = panel
        response = client.get("/admin")
        assert response.status_code == 303
        assert response.headers["location"] == "/login"

    @pytest.mark.parametrize(
        "login,password",
        [("ivanov", "secret"), ("vera", "vera_pass"), ("anna", "anna_pass")],
    )
    def test_only_head_sees_the_panel(self, panel, login, password):
        client, _ = panel
        assert _login(client, login, password).status_code == 303
        assert client.get("/admin").status_code == 403
        assert client.post("/admin/employee", data={}).status_code == 403

    def test_menu_link_only_for_head(self, panel):
        client, _ = panel
        _login_head(client)
        assert 'href="/admin"' in client.get("/").text
        client.post("/logout")

        _login(client, "ivanov", "secret")
        assert 'href="/admin"' not in client.get("/").text

    def test_page_lists_staff(self, panel):
        client, _ = panel
        _login_head(client)
        page = client.get("/admin")
        assert page.status_code == 200
        assert "Зайцев" in page.text
        assert "Сидорова" in page.text
        # Уволенных по умолчанию нет — здесь ещё никто не уволен.
        assert "показать уволенных" in page.text


class TestCreate:
    def test_creates_employee_with_given_password(self, panel):
        client, _ = panel
        _login_head(client)

        assert _create(client).status_code == 303
        assert "Петров" in client.get("/admin").text

        client.post("/logout")
        assert _login(client, "petrov", "petrov_pass").status_code == 303

    def test_generated_password_is_shown_once_and_works(self, panel):
        client, _ = panel
        _login_head(client)

        response = _create(client, login="novikov", password="")
        assert response.status_code == 200
        match = re.search(r"пароль: <b>([A-Za-z0-9]+)</b>", response.text)
        assert match, response.text
        password = match.group(1)
        assert len(password) >= 8
        # Повторный показ страницы пароль уже не печатает.
        assert "пароль: <b>" not in client.get("/admin").text

        client.post("/logout")
        assert _login(client, "novikov", password).status_code == 303

    def test_employee_without_account(self, panel):
        client, _ = panel
        _login_head(client)

        assert _create(client, last_name="Орлова", first_name="Ольга", login="", password="").status_code == 303
        page = client.get("/admin").text
        assert "Орлова" in page
        assert "—" in page

    def test_duplicate_login_is_a_message(self, panel):
        client, _ = panel
        _login_head(client)

        response = _create(client, login="anna", password="anna_pass2")
        assert response.status_code == 400
        assert "Логин уже занят" in response.text

    def test_short_password_is_refused(self, panel):
        client, _ = panel
        _login_head(client)

        response = _create(client, password="1234567")
        assert response.status_code == 400
        assert "короче 8" in response.text

    def test_password_without_login_is_refused(self, panel):
        client, _ = panel
        _login_head(client)

        response = _create(client, login="", password="12345678")
        assert response.status_code == 400
        assert "Пароль без логина" in response.text

    def test_empty_name_is_refused(self, panel):
        client, _ = panel
        _login_head(client)

        response = _create(client, last_name="  ")
        assert response.status_code == 400
        assert "Фамилия и имя обязательны" in response.text


class TestEdit:
    def test_edit_name_role_login_and_buh_id(self, panel):
        client, ids = panel
        _login_head(client)
        nurse = ids["nurse"]

        response = client.post(
            f"/admin/employee/{nurse.id}",
            data={
                "last_name": "Сидорова",
                "first_name": "Анна",
                "middle_name": "Петровна",
                "role": NURSE,
                "login": "anna2",
                "buh_id": "B-77",
            },
        )
        assert response.status_code == 303

        page = client.get(f"/admin/employee/{nurse.id}").text
        assert "Петровна" in page
        assert "anna2" in page
        assert "B-77" in page

        client.post("/logout")
        assert _login(client, "anna2", "anna_pass").status_code == 303

    def test_account_is_issued_to_employee_without_one(self, panel):
        client, ids = panel
        _login_head(client)
        worker = ids["no_account"]

        response = client.post(
            f"/admin/employee/{worker.id}",
            data={
                "last_name": worker.last_name,
                "first_name": worker.first_name,
                "middle_name": "",
                "role": NURSE,
                "login": "kuznecova",
                "buh_id": "",
            },
        )
        assert response.status_code == 200
        match = re.search(r"<b>([A-Za-z0-9]+)</b> —", response.text)
        assert match, response.text
        password = match.group(1)

        client.post("/logout")
        assert _login(client, "kuznecova", password).status_code == 303

    def test_login_cannot_be_blanked(self, panel):
        client, ids = panel
        _login_head(client)
        nurse = ids["nurse"]

        response = client.post(
            f"/admin/employee/{nurse.id}",
            data={
                "last_name": nurse.last_name,
                "first_name": nurse.first_name,
                "middle_name": "",
                "role": NURSE,
                "login": "",
                "buh_id": "",
            },
        )
        assert response.status_code == 400
        assert "логин нельзя оставить пустым" in response.text

    def test_cannot_change_own_role(self, panel):
        client, ids = panel
        _login_head(client)
        head = ids["head"]

        response = client.post(
            f"/admin/employee/{head.id}",
            data={
                "last_name": head.last_name,
                "first_name": head.first_name,
                "middle_name": "",
                "role": DOCTOR,
                "login": "zav",
                "buh_id": "",
            },
        )
        assert response.status_code == 400
        assert "Нельзя менять роль самому себе" in response.text
        # Панель по-прежнему доступна.
        assert client.get("/admin").status_code == 200


class TestDismiss:
    def test_dismissed_is_hidden_and_cannot_log_in(self, panel):
        client, ids = panel
        _login_head(client)
        nurse = ids["nurse"]

        assert client.post(
            f"/admin/employee/{nurse.id}/active", data={"active": "0"}
        ).status_code == 303

        assert f'href="/admin/employee/{nurse.id}"' not in client.get("/admin").text
        with_dismissed = client.get("/admin?all=1").text
        assert f'href="/admin/employee/{nurse.id}"' in with_dismissed
        assert "уволен" in with_dismissed

        client.post("/logout")
        response = _login(client, "anna", "anna_pass")
        assert response.status_code == 401
        assert "Учётная запись закрыта" in response.text

    def test_restore_returns_access(self, panel):
        client, ids = panel
        _login_head(client)
        nurse = ids["nurse"]

        client.post(f"/admin/employee/{nurse.id}/active", data={"active": "0"})
        assert client.post(
            f"/admin/employee/{nurse.id}/active", data={"active": "1"}
        ).status_code == 303
        assert "Сидорова" in client.get("/admin").text

        client.post("/logout")
        assert _login(client, "anna", "anna_pass").status_code == 303

    def test_cannot_dismiss_self(self, panel):
        client, ids = panel
        _login_head(client)
        head = ids["head"]

        response = client.post(
            f"/admin/employee/{head.id}/active", data={"active": "0"}
        )
        assert response.status_code == 303
        page = client.get("/admin").text
        assert "Нельзя уволить самого себя" in page
        assert "Зайцев" in page
        assert client.get("/admin").status_code == 200


class TestPassword:
    def test_reset_shows_new_password_and_old_stops_working(self, panel):
        client, ids = panel
        _login_head(client)
        nurse = ids["nurse"]

        response = client.post(f"/admin/employee/{nurse.id}/password")
        assert response.status_code == 200
        match = re.search(r"<b>([A-Za-z0-9]+)</b> —", response.text)
        assert match, response.text
        new_password = match.group(1)

        client.post("/logout")
        assert _login(client, "anna", new_password).status_code == 303
        client.post("/logout")
        assert _login(client, "anna", "anna_pass").status_code == 401

    def test_reset_without_account_is_a_message(self, panel):
        client, ids = panel
        _login_head(client)

        response = client.post(f"/admin/employee/{ids['no_account'].id}/password")
        assert response.status_code == 400
        assert "нет учётной записи" in response.text

    def test_head_can_reset_own_password(self, panel):
        client, ids = panel
        _login_head(client)

        response = client.post(f"/admin/employee/{ids['head'].id}/password")
        assert response.status_code == 200
        assert "показывается один раз" in response.text
