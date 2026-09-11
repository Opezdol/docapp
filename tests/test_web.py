"""Тесты веб-интерфейса: вход, выбор сестры, ввод, правка, удаление."""

import pytest
from fastapi.testclient import TestClient

from docapp.auth.passwords import hash_password
from docapp.domain.employee import DOCTOR, HEAD, HEAD_NURSE, NURSE, Employee
from docapp.storage.sqlite_store import SqliteEmployeeStore
from docapp.web.app import create_app


def _seed(db_path) -> dict:
    """Создать врача, медсестру (с логином), заведующего, старшую сестру и заведующего отделением."""
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
                middle_name="Петровна",
                role=NURSE,
                login="anna",
                password_hash=hash_password("anna_pass"),
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
        head_nurse = es.add(
            Employee(
                last_name="Волкова",
                first_name="Вера",
                middle_name="Сергеевна",
                role=HEAD_NURSE,
                login="vera",
                password_hash=hash_password("vera_pass"),
            )
        )
        zav = es.add(
            Employee(
                last_name="Зайцев",
                first_name="Захар",
                middle_name="Захарович",
                role=HEAD,
                login="zav",
                password_hash=hash_password("zav_pass"),
            )
        )
    return {
        "doctor_id": doctor.id,
        "nurse_id": nurse.id,
        "head_id": head.id,
        "head_nurse_id": head_nurse.id,
        "zav_id": zav.id,
    }


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
        assert r.status_code == 200
        assert r.json() == {"ok": True, "nurse_id": 2}

    def test_active_nurse_shown_on_index(self, client):
        _login(client)
        client.post("/nurse", data={"nurse_id": 2})
        r = client.get("/")
        assert "Сидорова Анна" in r.text


class TestAnesthesiaFlow:
    def _add(self, client, patient_name="Петров Петр Петрович", nurse_id: int | None = 2):
        data: dict = {"patient_name": patient_name}
        if nurse_id is not None:
            data["nurse_id"] = nurse_id
        return client.post("/anesthesia", data=data)

    def test_add_without_nurse_shows_error(self, client):
        _login(client)
        r = self._add(client, nurse_id=None)
        assert r.status_code == 303
        page = client.get("/")
        assert "Сначала выберите медсестру" in page.text

    def test_add_with_nurse(self, client):
        _login(client)
        r = self._add(client)
        assert r.status_code == 303
        page = client.get("/")
        assert "Петров Петр Петрович" in page.text

    def test_edit_own_record(self, client):
        _login(client)
        self._add(client)
        # найти id записи через страницу правки нельзя напрямую —
        # берём первую ссылку «Изменить» со страницы
        page = client.get("/")
        assert "/anesthesia/1/edit" in page.text
        r = client.post(
            "/anesthesia/1/update",
            data={
                "patient_name": "Новый Пациент",
                "nurse_id": 2,
            },
        )
        assert r.status_code == 303
        page = client.get("/")
        assert "Новый Пациент" in page.text
        assert "Петров Петр Петрович" not in page.text

    def test_edit_foreign_record_forbidden(self, client):
        _login(client)
        self._add(client)
        # второй врач (заведующий) не может править чужую запись
        _login(client, login="petrov", password="pass123")
        r = client.post(
            "/anesthesia/1/update",
            data={
                "patient_name": "Взлом",
                "nurse_id": 2,
            },
        )
        assert r.status_code == 303
        page = client.get("/")
        assert "Нельзя изменять чужую запись" in page.text

    def test_delete_own_record(self, client):
        _login(client)
        self._add(client)
        r = client.post("/anesthesia/1/delete")
        assert r.status_code == 303
        page = client.get("/")
        assert "Пока нет записей" in page.text

    def test_delete_foreign_record_forbidden(self, client):
        _login(client)
        self._add(client)
        _login(client, login="petrov", password="pass123")
        r = client.post("/anesthesia/1/delete")
        assert r.status_code == 303
        page = client.get("/")
        assert "Нельзя изменять чужую запись" in page.text


class TestNurseView:
    """Медсестра входит и видит свои анестезии — только просмотр (ADR-5)."""

    def _add_as_doctor(self, client, nurse_id=2, patient_name="Петров Петр Петрович"):
        _login(client)
        return client.post("/anesthesia", data={"patient_name": patient_name, "nurse_id": nurse_id})

    def test_nurse_can_login(self, client):
        r = _login(client, login="anna", password="anna_pass")
        assert r.status_code == 303
        page = client.get("/")
        assert "Здравствуйте, Анна Петровна" in page.text

    def test_nurse_sees_only_own_records(self, client):
        self._add_as_doctor(client, nurse_id=2, patient_name="Пациент Свой")
        # вторая медсестра — через хранилище приложения
        other = Employee(
            last_name="Козлова", first_name="Мария", role=NURSE,
            login="masha", password_hash=hash_password("masha_pass"),
        )
        other = client.app.state.employees.add(other)
        assert other.id is not None
        self._add_as_doctor(client, nurse_id=other.id, patient_name="Пациент Чужой")

        _login(client, login="anna", password="anna_pass")
        page = client.get("/")
        assert "Пациент Свой" in page.text
        assert "Пациент Чужой" not in page.text

    def test_nurse_has_no_input_form(self, client):
        _login(client, login="anna", password="anna_pass")
        page = client.get("/")
        assert "Новая анестезия" not in page.text
        assert "Изменить" not in page.text
        assert "Удалить" not in page.text

    def test_nurse_cannot_edit_foreign(self, client):
        self._add_as_doctor(client)
        _login(client, login="anna", password="anna_pass")
        r = client.post(
            "/anesthesia/1/update",
            data={
                "patient_name": "Взлом",
                "nurse_id": 2,
            },
        )
        assert r.status_code == 303
        page = client.get("/")
        assert "Нельзя изменять чужую запись" in page.text

    def test_nurse_cannot_delete(self, client):
        self._add_as_doctor(client)
        _login(client, login="anna", password="anna_pass")
        r = client.post("/anesthesia/1/delete")
        assert r.status_code == 303
        page = client.get("/")
        assert "Нельзя изменять чужую запись" in page.text

    def test_nurse_cannot_choose_nurse(self, client):
        _login(client, login="anna", password="anna_pass")
        r = client.post("/nurse", data={"nurse_id": 2})
        assert r.status_code == 403
        assert "Выбор сестры доступен только врачам" in r.json()["error"]


class TestMyData:
    """Страница «Мои данные» — просмотр личных данных (ADR-9)."""

    def test_me_page_shows_data(self, client):
        _login(client)
        r = client.get("/me")
        assert r.status_code == 200
        assert "Мои данные" in r.text
        assert "Иванов Иван Иванович" in r.text  # полное ФИО
        assert "Врач" in r.text
        assert "ivanov" in r.text  # логин
        assert "— не задан —" in r.text  # buh_id пуст

    def test_me_shows_buh_id_when_set(self, client):
        _login(client)
        client.app.state.employees.update_buh_id(1, "B-1042")
        r = client.get("/me")
        assert "B-1042" in r.text
        assert "— не задан —" not in r.text

    def test_me_requires_login(self, client):
        r = client.get("/me", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/login"

    def test_nurse_sees_own_data(self, client):
        _login(client, login="anna", password="anna_pass")
        r = client.get("/me")
        assert "Сидорова Анна Петровна" in r.text
        assert "Медсестра" in r.text


class TestAppMenu:
    """Меню подприложений и заглушки (ADR-9)."""

    def test_menu_on_main_page(self, client):
        _login(client)
        r = client.get("/")
        assert "Анестезии" in r.text
        assert "Компендиум" in r.text
        assert 'href="/me"' in r.text  # кликабельное имя

    def test_active_item_on_main(self, client):
        _login(client)
        r = client.get("/")
        # у пункта «Анестезии» класс active
        assert 'class="app-link active"' in r.text

    def test_nurse_menu_hides_compendium_shows_needs(self, client):
        # ADR-11: медсёстрам «Компендиум» закрыт, «Потребности» — открыты.
        _login(client, login="anna", password="anna_pass")
        r = client.get("/")
        assert "Компендиум" not in r.text
        assert "Потребности" in r.text
        assert 'href="/needs"' in r.text

    def test_doctor_menu_shows_compendium_hides_needs(self, client):
        # ADR-11: врачам «Потребности» закрыты, «Компендиум» — открыт.
        _login(client)  # врач ivanov
        r = client.get("/")
        assert "Компендиум" in r.text
        assert "Потребности" not in r.text

    def test_head_nurse_menu(self, client):
        # ADR-11 + ADR-0023: старшая сестра читает «Компендиум» и ведёт «Потребности»,
        # но «Дежурства» ей недоступны (она не врач).
        _login(client, login="vera", password="vera_pass")
        r = client.get("/")
        assert "Компендиум" in r.text
        assert "Потребности" in r.text
        assert "Дежурства" not in r.text

    def test_head_menu_shows_all_four(self, client):
        # заведующий видит все разделы, включая «Дежурства»
        _login(client, login="zav", password="zav_pass")
        r = client.get("/")
        for label in ("Анестезии", "Компендиум", "Потребности", "Дежурства"):
            assert label in r.text

    def test_menu_is_rendered_from_permissions(self, client):
        """Меню — из таблицы прав: в шаблоне нет условий по ролям."""
        from pathlib import Path

        import docapp.web

        base = Path(docapp.web.__file__).parent / "templates" / "base.html"
        text = base.read_text(encoding="utf-8")
        assert "user.role" not in text

    def test_compendium_page(self, client):
        _login(client)
        r = client.get("/compendium")
        assert r.status_code == 200
        assert "Компендиум" in r.text

    def test_compendium_requires_login(self, client):
        r = client.get("/compendium", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/login"

    def test_no_menu_on_login_page(self, client):
        r = client.get("/login")
        assert "Компендиум" not in r.text
        assert "Анестезии" not in r.text


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


class TestSessionConfig:
    """Конфигурация сессий: https_only управляется DOCAPP_HTTPS_ONLY (ADR-7)."""

    def _make_app(self, tmp_path, monkeypatch):
        # «Компендиум» в tmp, чтобы не трогать реальные data/wiki
        return create_app(tmp_path / "w.db", "s")

    def _session_middleware(self, app):
        # В starlette 1.3.x параметры middleware лежат в .kwargs (не .options)
        mw = [m for m in app.user_middleware if m.cls.__name__ == "SessionMiddleware"][0]
        return mw.kwargs

    def test_https_only_default_false(self, monkeypatch, tmp_path):
        monkeypatch.delenv("DOCAPP_HTTPS_ONLY", raising=False)
        app = self._make_app(tmp_path, monkeypatch)
        mw = self._session_middleware(app)
        assert mw.get("https_only") is False

    def test_https_only_true_when_env_set(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DOCAPP_HTTPS_ONLY", "1")
        app = self._make_app(tmp_path, monkeypatch)
        mw = self._session_middleware(app)
        assert mw.get("https_only") is True
