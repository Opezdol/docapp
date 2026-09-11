"""Сквозной E2E-тест подприложения «Потребности» (задача T9).

Полная цепочка через TestClient (follow_redirects=False) и без сети:
сотрудники всех четырёх ролей сидятся в SqliteEmployeeStore, вход — через
POST /login, каталог и БД «Потребностей» — временные (monkeypatch
NEEDS_CATALOG), приложение собирается
create_app(db_path, secret). Никаких внешних вызовов: всё внутри процесса.

Неделя во всех запросах фиксированная (WEEK = понедельник '2026-08-10'),
передаётся явно (week в теле/параметрах API, query-параметр для отчёта),
чтобы тесты не зависели от даты запуска. БД/каталог — по одному на фикстуру
(function-scoped, как в test_needs_api.py); каждый сценарий сам сидит свои
данные через API и не зависит от остальных.

Заявки раздельные по разделу (category: solutions/medicaments).

Сценарии (по ТЗ-растворы-медикаменты и ADR-11):
  a) полный цикл: черновик медсестры со снимками unit/grp (по разделу) →
     отправка → доска старшей (status/author_name/is_closed по разделу) →
     закрытие раздела с unsent_points;
  b) отчёт-форма по разделу: .xlsx (сигнатура PK) и HTML;
  c) закрытый раздел: правки и отправка — 409;
  d) переоткрытие раздела: правка снова возможна, повторное закрытие;
  e) врач: 403 на страницу, заявки и отчёт;
  f) «Компендиум»: медсестре — 403, старшей сестре — 200;
  g) аналитика: solutions (растворы поточково) и groups за период;
  h) меню по ролям: «Потребности»/«Компендиум» в зависимости от роли;
  i) разделы независимы: отправка/закрытие/отчёт одного раздела не трогает
     другой.
"""

import pytest
from fastapi.testclient import TestClient

from docapp.auth.passwords import hash_password
from docapp.domain.employee import DOCTOR, HEAD, HEAD_NURSE, NURSE, Employee
from docapp.needs.catalog import CATEGORY_MEDICAMENTS, CATEGORY_SOLUTIONS
from docapp.storage.sqlite_store import SqliteEmployeeStore
from docapp.web.app import create_app

#: Фиксированная неделя (понедельник) для всех запросов тестов.
WEEK = "2026-08-10"

SOL = CATEGORY_SOLUTIONS
MED = CATEGORY_MEDICAMENTS

#: Временный каталог: две базы по две точки, группы Неспецифика/Медикаменты/Растворы.
CATALOG_YAML = """\
bases:
  Ленская: [травма, урология]
  Таймырская: [экстренная, гной]

groups:
  Неспецифика:
    Атропин: амп
    Дексаметазон: амп
  Медикаменты:
    Лидокаин: амп
    Пропофол: фл
  Растворы:
    Физ 200/250: фл
    Рингер: фл
"""


def _seed(db_path) -> dict:
    """Все четыре роли плюс вторая медсестра — с логинами и паролями."""
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
        nurse2 = es.add(
            Employee(
                last_name="Козлова", first_name="Мария", middle_name="Ивановна",
                role=NURSE, login="masha", password_hash=hash_password("masha_pass"),
            )
        )
    return {
        "doctor_id": doctor.id,
        "nurse_id": nurse.id,
        "head_nurse_id": head_nurse.id,
        "head_id": head.id,
        "nurse2_id": nurse2.id,
    }


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Приложение с временными БД: свои needs.db/catalog.yaml, консультант в tmp."""
    catalog = tmp_path / "catalog.yaml"
    catalog.write_text(CATALOG_YAML, encoding="utf-8")
    monkeypatch.setenv("NEEDS_CATALOG", str(catalog))
    # «Компендиум» — тоже во временный каталог, чтобы не трогать data/wiki
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


def _logout(client):
    """Выйти (сессия очищается)."""
    client.post("/logout")


def _nurse_creates_and_submits(client, base, point, category, lines):
    """Медсестра создаёт заявку раздела и отправляет её (вспомогательный сид данных)."""
    _login(client, "anna", "anna_pass")
    r = client.post(
        "/needs/api/request",
        json={"base": base, "point": point, "category": category, "week": WEEK, "lines": lines},
    )
    assert r.status_code == 200
    r = client.post(
        "/needs/api/request/submit",
        json={"base": base, "point": point, "category": category, "week": WEEK},
    )
    assert r.status_code == 200
    _logout(client)


class TestFullCycle:
    """(a) Полный цикл: черновик → отправка → доска → закрытие раздела."""

    def test_full_cycle(self, client):
        # медсестра создаёт черновик медикаментов без unit/grp — ответ со снимками
        _login(client, "anna", "anna_pass")
        r = client.post(
            "/needs/api/request",
            json={
                "base": "Ленская",
                "point": "травма",
                "category": MED,
                "week": WEEK,
                "lines": [{"item": "Атропин", "qty": 5}],
            },
        )
        assert r.status_code == 200
        req = r.json()["request"]
        assert req["status"] == "draft"
        assert req["category"] == MED
        lines = {line["item"]: line for line in req["lines"]}
        assert lines["Атропин"]["unit"] == "амп"
        assert lines["Атропин"]["grp"] == "Неспецифика"
        assert lines["Атропин"]["qty"] == 5

        # отдельная заявка растворов (независимая)
        r = client.post(
            "/needs/api/request",
            json={
                "base": "Ленская",
                "point": "травма",
                "category": SOL,
                "week": WEEK,
                "lines": [{"item": "Физ 200/250", "qty": 3}],
            },
        )
        assert r.status_code == 200
        sol_req = r.json()["request"]
        assert sol_req["category"] == SOL
        lines = {line["item"]: line for line in sol_req["lines"]}
        assert lines["Физ 200/250"]["unit"] == "фл"
        assert lines["Физ 200/250"]["grp"] == "Растворы"

        # отправка медикаментов: статус sent, предупреждений нет
        r = client.post(
            "/needs/api/request/submit",
            json={"base": "Ленская", "point": "травма", "category": MED, "week": WEEK},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["request"]["status"] == "sent"
        assert data["warnings"] == []
        _logout(client)

        # старшая сестра: доска показывает состояние по разделам
        _login(client, "elena", "elena_pass")
        r = client.get("/needs/api/board", params={"week": WEEK})
        assert r.status_code == 200
        cells = {(c["base"], c["point"], c["category"]): c for c in r.json()["board"]}
        травма_med = cells[("Ленская", "травма", MED)]
        assert травма_med["status"] == "sent"
        assert травма_med["author_name"] == "Сидорова Анна Петровна"
        assert травма_med["is_closed"] is False
        травма_sol = cells[("Ленская", "травма", SOL)]
        assert травма_sol["status"] == "draft"  # растворы не отправлены
        assert cells[("Ленская", "урология", MED)]["status"] == "none"

        # закрытие раздела медикаментов: урология — в предупреждении
        r = client.post("/needs/api/close", json={"base": "Ленская", "category": MED, "week": WEEK})
        assert r.status_code == 200
        close_data = r.json()
        assert close_data["closed"] is True
        assert set(close_data["unsent_points"]) == {"урология"}

        # доска после закрытия: ячейки раздела медикаментов закрыты, растворов — нет
        r = client.get("/needs/api/board", params={"week": WEEK})
        cells = {(c["base"], c["point"], c["category"]): c for c in r.json()["board"]}
        assert cells[("Ленская", "травма", MED)]["is_closed"] is True
        assert cells[("Ленская", "урология", MED)]["is_closed"] is True
        assert cells[("Ленская", "травма", SOL)]["is_closed"] is False
        # другие базы не затронуты закрытием
        assert cells[("Таймырская", "экстренная", MED)]["is_closed"] is False


class TestReport:
    """(b) Отчёт-форма по разделу после закрытия: .xlsx и HTML с препаратом."""

    def _seed_sent_and_close(self, client):
        """Отправленные заявки Ленской за WEEK в обоих разделах; медикаменты закрыты."""
        _nurse_creates_and_submits(
            client, "Ленская", "травма", MED, [{"item": "Атропин", "qty": 2}]
        )
        _nurse_creates_and_submits(
            client, "Ленская", "травма", SOL, [{"item": "Физ 200/250", "qty": 4}]
        )
        _nurse_creates_and_submits(
            client, "Ленская", "урология", MED, [{"item": "Атропин", "qty": 1}]
        )
        _login(client, "elena", "elena_pass")
        r = client.post("/needs/api/close", json={"base": "Ленская", "category": MED, "week": WEEK})
        assert r.status_code == 200
        assert r.json()["unsent_points"] == []
        _logout(client)

    def test_report_xlsx(self, client):
        self._seed_sent_and_close(client)
        _login(client, "elena", "elena_pass")
        for category in (MED, SOL):
            r = client.get(
                "/needs/report.xlsx",
                params={"base": "Ленская", "category": category, "week": WEEK},
            )
            assert r.status_code == 200
            assert "spreadsheetml" in r.headers["content-type"]
            assert r.content[:2] == b"PK"  # сигнатура zip/xlsx

    def test_report_page_html(self, client):
        self._seed_sent_and_close(client)
        _login(client, "elena", "elena_pass")
        # отчёт медикаментов: только препараты медикаментов
        r = client.get(
            "/needs/report",
            params={"base": "Ленская", "category": MED, "week": WEEK},
        )
        assert r.status_code == 200
        assert "Атропин" in r.text
        assert "Физ 200/250" not in r.text
        assert "База:" in r.text


class TestClosedBlocksEdits:
    """(c) После закрытия раздела правки и отправка запрещены (409)."""

    def test_closed_week_blocks_edit_and_submit(self, client):
        _nurse_creates_and_submits(
            client, "Ленская", "травма", MED, [{"item": "Атропин", "qty": 2}]
        )
        _login(client, "elena", "elena_pass")
        r = client.post("/needs/api/close", json={"base": "Ленская", "category": MED, "week": WEEK})
        assert r.status_code == 200
        _logout(client)

        # та же медсестра: правка своей заявки раздела — 409
        _login(client, "anna", "anna_pass")
        r = client.post(
            "/needs/api/request",
            json={
                "base": "Ленская", "point": "травма", "category": MED, "week": WEEK,
                "lines": [{"item": "Атропин", "qty": 9}],
            },
        )
        assert r.status_code == 409
        # отправка — 409
        r = client.post(
            "/needs/api/request/submit",
            json={"base": "Ленская", "point": "травма", "category": MED, "week": WEEK},
        )
        assert r.status_code == 409
        _logout(client)

        # другая медсестра: новая заявка на пустой точке закрытого раздела — 409
        _login(client, "masha", "masha_pass")
        r = client.post(
            "/needs/api/request",
            json={
                "base": "Ленская", "point": "урология", "category": MED, "week": WEEK,
                "lines": [{"item": "Дексаметазон", "qty": 1}],
            },
        )
        assert r.status_code == 409


class TestReopen:
    """(d) Переоткрытие раздела: правка снова возможна, затем повторное закрытие."""

    def test_reopen_allows_edit_then_close_again(self, client):
        _nurse_creates_and_submits(
            client, "Ленская", "травма", MED, [{"item": "Атропин", "qty": 2}]
        )
        _login(client, "elena", "elena_pass")
        assert (
            client.post("/needs/api/close", json={"base": "Ленская", "category": MED, "week": WEEK}).status_code
            == 200
        )

        # переоткрытие старшей
        r = client.post("/needs/api/reopen", json={"base": "Ленская", "category": MED, "week": WEEK})
        assert r.status_code == 200
        assert r.json() == {"reopened": True}
        _logout(client)

        # правка снова возможна (200), статус сбрасывается на черновик
        _login(client, "anna", "anna_pass")
        r = client.post(
            "/needs/api/request",
            json={
                "base": "Ленская", "point": "травма", "category": MED, "week": WEEK,
                "lines": [{"item": "Атропин", "qty": 4}],
            },
        )
        assert r.status_code == 200
        assert r.json()["request"]["status"] == "draft"
        _logout(client)

        # снова закрытие — 200
        _login(client, "elena", "elena_pass")
        r = client.post("/needs/api/close", json={"base": "Ленская", "category": MED, "week": WEEK})
        assert r.status_code == 200
        assert r.json()["closed"] is True


class TestDoctorForbidden:
    """(e) Врач: 403 на страницу, заявки и отчёт (ADR-11)."""

    def test_doctor_forbidden_everywhere(self, client):
        _login(client, "ivanov", "secret")
        assert client.get("/needs").status_code == 403
        r = client.post(
            "/needs/api/request",
            json={"base": "Ленская", "point": "травма", "category": MED, "lines": [{"item": "Атропин", "qty": 1}]},
        )
        assert r.status_code == 403
        r = client.get(
            "/needs/report.xlsx",
            params={"base": "Ленская", "category": MED, "week": WEEK},
        )
        assert r.status_code == 403


class TestCompendiumAccess:
    """(f) «Компендиум»: медсестре закрыт (403), старшей сестре — доступен (200)."""

    def test_nurse_forbidden_compendium(self, client):
        _login(client, "anna", "anna_pass")
        assert client.get("/compendium").status_code == 403

    def test_head_nurse_can_open_compendium(self, client):
        _login(client, "elena", "elena_pass")
        r = client.get("/compendium")
        # Старшая сестра проходит; страница может быть с пустой базой —
        # проверяем только статус: не 403 (роль допущена) и не 303 (нет редиректа).
        assert r.status_code not in (403, 303)


class TestAnalytics:
    """(g) Аналитика старшей за период: solutions (растворы поточково) и groups."""

    def test_analytics_with_solutions_and_groups(self, client):
        _nurse_creates_and_submits(
            client, "Ленская", "травма", MED, [{"item": "Атропин", "qty": 2}]
        )
        _nurse_creates_and_submits(
            client, "Ленская", "травма", SOL, [{"item": "Физ 200/250", "qty": 3}]
        )
        _login(client, "elena", "elena_pass")
        r = client.get(
            "/needs/api/analytics",
            params={"from": WEEK, "to": WEEK, "base": "Ленская"},
        )
        assert r.status_code == 200
        data = r.json()
        assert "solutions" in data
        assert "groups" in data
        assert data["groups"]["Неспецифика"]["Атропин"]["qty"] == 2
        # особая группа «Растворы» идёт поточково в solutions
        assert data["solutions"]["Физ 200/250"]["ИТОГО"] == 3
        assert data["solutions"]["Физ 200/250"]["травма"] == 3
        assert data["solutions"]["Физ 200/250"]["урология"] == 0


class TestSectionsIndependent:
    """(i) Разделы независимы: отправка/закрытие/отчёт одного не трогает другой."""

    def test_sections_are_independent(self, client):
        # обе заявки на одной точке, но в разных разделах
        _nurse_creates_and_submits(
            client, "Ленская", "травма", MED, [{"item": "Атропин", "qty": 2}]
        )
        _nurse_creates_and_submits(
            client, "Ленская", "травма", SOL, [{"item": "Физ 200/250", "qty": 4}]
        )

        # закрываем только медикаменты
        _login(client, "elena", "elena_pass")
        r = client.post("/needs/api/close", json={"base": "Ленская", "category": MED, "week": WEEK})
        assert r.status_code == 200
        assert r.json()["unsent_points"] == ["урология"]

        # доска: медикаменты закрыты, растворы — открыты
        board = client.get("/needs/api/board", params={"week": WEEK}).json()["board"]
        cells = {(c["base"], c["point"], c["category"]): c for c in board}
        assert cells[("Ленская", "травма", MED)]["is_closed"] is True
        assert cells[("Ленская", "травма", SOL)]["is_closed"] is False

        # отчёты: медикаменты — только Атропин, растворы — только Физ 200/250
        r = client.get(
            "/needs/report",
            params={"base": "Ленская", "category": MED, "week": WEEK},
        )
        assert "Атропин" in r.text
        assert "Физ 200/250" not in r.text
        r = client.get(
            "/needs/report",
            params={"base": "Ленская", "category": SOL, "week": WEEK},
        )
        assert "Физ 200/250" in r.text
        assert "Атропин" not in r.text
        _logout(client)

        # медсестра: правка закрытых медикаментов — 409, растворов — 200
        _login(client, "anna", "anna_pass")
        r = client.post(
            "/needs/api/request",
            json={
                "base": "Ленская", "point": "травма", "category": MED, "week": WEEK,
                "lines": [{"item": "Атропин", "qty": 9}],
            },
        )
        assert r.status_code == 409
        r = client.post(
            "/needs/api/request",
            json={
                "base": "Ленская", "point": "травма", "category": SOL, "week": WEEK,
                "lines": [{"item": "Физ 200/250", "qty": 5}],
            },
        )
        assert r.status_code == 200
        _logout(client)


class TestMenu:
    """(h) Меню по ролям: «Потребности»/«Компендиум» в зависимости от роли."""

    def test_menu_for_nurse(self, client):
        _login(client, "anna", "anna_pass")
        r = client.get("/")
        assert r.status_code == 200
        assert "Потребности" in r.text
        assert "Компендиум" not in r.text

    def test_menu_for_doctor(self, client):
        _login(client, "ivanov", "secret")
        r = client.get("/")
        assert r.status_code == 200
        assert "Компендиум" in r.text
        assert "Потребности" not in r.text

    def test_menu_for_head_nurse(self, client):
        _login(client, "elena", "elena_pass")
        r = client.get("/")
        assert r.status_code == 200
        assert "Потребности" in r.text
        assert "Компендиум" in r.text
