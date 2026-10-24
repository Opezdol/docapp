"""Тесты раздела «Шкалы»: расчёт, границы порогов, доступ (ТЗ-шкалы, ADR-0028).

Контракт — поведение: балл и подпись зоны на границах (2/3, 8/9, 1/2), одно
поле «Пол» на две шкалы, и раздел, который ничего не хранит.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from docapp.auth.passwords import hash_password
from docapp.domain.employee import DOCTOR, EDITOR, HEAD_NURSE, NURSE, Employee
from docapp.modules import MODULES
from docapp.people.store import SqliteEmployeeStore
from docapp.scales import calc
from docapp.web.app import create_app
from factories import make_db

#: Сотрудники для сценариев: (логин, пароль, роль, фамилия).
PEOPLE = (
    ("doc", "doc_pass", DOCTOR, "Врач"),
    ("eds", "eds_pass", EDITOR, "Редактор"),
    ("nurse", "nurse_pass", NURSE, "Сестра"),
    ("headn", "headn_pass", HEAD_NURSE, "Старшая"),
)


@pytest.fixture()
def client(tmp_path):
    """Приложение целиком: вход нужен, чтобы проверить доступ к разделу."""
    db = make_db(tmp_path)
    with SqliteEmployeeStore(db) as store:
        for login, password, role, last_name in PEOPLE:
            store.add(
                Employee(
                    last_name=last_name,
                    first_name="Тест",
                    role=role,
                    login=login,
                    password_hash=hash_password(password),
                )
            )
    app = create_app(db_path=db, secret="test", modules=MODULES)
    with TestClient(app) as test_client:
        yield test_client


def _login(client, login="doc", password="doc_pass") -> None:
    response = client.post(
        "/login", data={"login": login, "password": password}, follow_redirects=False
    )
    assert response.status_code == 303


def _result(key: str, answers: dict[str, int]) -> str:
    """Результат шкалы строкой — так, как его увидит врач."""
    raw = {field: str(value) for field, value in answers.items()}
    (rendered,) = calc.plan((key,), raw)
    return rendered.result


class TestScalesDeclaration:
    """Состав раздела: ровно четыре шкалы из ТЗ, в объявленном порядке."""

    def test_four_scales(self):
        assert [scale.key for scale in calc.SCALES] == [
            "stop_bang",
            "apfel",
            "aldrete",
            "nu_desc",
        ]

    def test_max_points(self):
        assert {scale.key: scale.max_points for scale in calc.SCALES} == {
            "stop_bang": 8,
            "apfel": 4,
            "aldrete": 10,
            "nu_desc": 10,
        }

    def test_no_scale_knows_anesthesia_or_patients(self):
        """Шов: калькулятор не знает ни записей анестезий, ни пациентов (ADR-0028)."""
        import re
        from pathlib import Path

        import docapp.scales

        root = Path(docapp.scales.__file__).parent
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            assert not re.search(r"\banesthesia\b|\bpatient", text), path.name


class TestStopBang:
    @pytest.mark.parametrize(
        "answers,expected",
        [
            ({}, "0 из 8 — низкий риск СОАС"),
            ({"snore": 1, "tired": 1}, "2 из 8 — низкий риск СОАС"),
            ({"snore": 1, "tired": 1, "apnea": 1}, "3 из 8 — промежуточный риск СОАС"),
            (
                {"snore": 1, "tired": 1, "apnea": 1, "pressure": 1},
                "4 из 8 — промежуточный риск СОАС",
            ),
            (
                {"snore": 1, "tired": 1, "apnea": 1, "pressure": 1, "bmi": 1},
                "5 из 8 — высокий риск СОАС",
            ),
            # мужской пол — это отдельный пункт шкалы, а не общая справка
            ({"sex": 0}, "1 из 8 — низкий риск СОАС"),
        ],
    )
    def test_bands(self, answers, expected):
        assert _result("stop_bang", answers) == expected

    def test_all_eight(self):
        answers = {item.key: 1 for item in calc.STOP_BANG.items}
        answers["sex"] = 0  # мужской пол даёт балл STOP-BANG
        assert _result("stop_bang", answers) == "8 из 8 — высокий риск СОАС"


class TestApfel:
    @pytest.mark.parametrize(
        "answers,expected",
        [
            ({}, "0 из 4 — риск ПОТР около 10%"),
            ({"sex": 1}, "1 из 4 — риск ПОТР около 21%"),
            ({"sex": 1, "non_smoker": 1}, "2 из 4 — риск ПОТР около 39%"),
            (
                {"sex": 1, "non_smoker": 1, "history": 1},
                "3 из 4 — риск ПОТР около 61%",
            ),
            (
                {"sex": 1, "non_smoker": 1, "history": 1, "opioids": 1},
                "4 из 4 — риск ПОТР около 79%",
            ),
        ],
    )
    def test_bands(self, answers, expected):
        assert _result("apfel", answers) == expected

    def test_male_sex_gives_nothing(self):
        """В Apfel балл даёт женский пол: общий «Пол» в двух шкалах читается розно."""
        assert _result("apfel", {"sex": 0}) == "0 из 4 — риск ПОТР около 10%"


class TestAldrete:
    @pytest.mark.parametrize(
        "answers,expected",
        [
            ({}, "0 из 10 — не готова к переводу"),
            (
                {
                    "activity": 2,
                    "breathing": 2,
                    "circulation": 2,
                    "consciousness": 2,
                    "saturation": 0,
                },
                "8 из 10 — не готова к переводу",
            ),
            (
                {
                    "activity": 2,
                    "breathing": 2,
                    "circulation": 2,
                    "consciousness": 2,
                    "saturation": 1,
                },
                "9 из 10 — готовность к переводу в отделение",
            ),
            (
                {
                    "activity": 2,
                    "breathing": 2,
                    "circulation": 2,
                    "consciousness": 2,
                    "saturation": 2,
                },
                "10 из 10 — готовность к переводу в отделение",
            ),
        ],
    )
    def test_bands(self, answers, expected):
        assert _result("aldrete", answers) == expected


class TestNuDesc:
    @pytest.mark.parametrize(
        "answers,expected",
        [
            ({}, "0 из 10 — отрицательный скрининг"),
            ({"disorientation": 1}, "1 из 10 — отрицательный скрининг"),
            ({"disorientation": 2}, "2 из 10 — положительный скрининг"),
            ({"retardation": 2}, "2 из 10 — положительный скрининг"),
        ],
    )
    def test_bands(self, answers, expected):
        assert _result("nu_desc", answers) == expected


class TestAnswers:
    def test_empty_answers_count_as_zero(self):
        """Незаполненный пункт — ноль, а не «неизвестно» (ТЗ §4)."""
        (rendered,) = calc.plan(("aldrete",), {})
        assert rendered.points == 0

    def test_unknown_value_is_ignored(self):
        (rendered,) = calc.plan(("stop_bang",), {"snore": "7"})
        assert rendered.points == 0

    def test_unknown_scale_key_is_dropped(self):
        assert calc.plan(("bogus",), {}) == ()

    def test_order_follows_declaration(self):
        """Порядок шкал на странице — объявленный, как бы ни пришли ключи."""
        rendered = calc.plan(("nu_desc", "stop_bang"), {})
        assert [item.scale.key for item in rendered] == ["stop_bang", "nu_desc"]


class TestPage:
    def test_page_requires_login(self, client):
        response = client.get("/scales", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/login"

    @pytest.mark.parametrize("login,password", [("doc", "doc_pass"), ("eds", "eds_pass")])
    def test_doctor_and_editor_allowed(self, client, login, password):
        _login(client, login, password)
        response = client.get("/scales")
        assert response.status_code == 200
        for title in ("STOP-BANG", "Apfel", "Aldrete", "Nu-DESC"):
            assert title in response.text

    @pytest.mark.parametrize(
        "login,password", [("nurse", "nurse_pass"), ("headn", "headn_pass")]
    )
    def test_nurse_and_head_nurse_denied(self, client, login, password):
        _login(client, login, password)
        assert client.get("/scales").status_code == 403

    @pytest.mark.parametrize("login,password", [("doc", "doc_pass"), ("eds", "eds_pass")])
    def test_menu_item_visible(self, client, login, password):
        _login(client, login, password)
        assert "Шкалы" in client.get("/").text

    @pytest.mark.parametrize(
        "login,password", [("nurse", "nurse_pass"), ("headn", "headn_pass")]
    )
    def test_menu_item_hidden(self, client, login, password):
        _login(client, login, password)
        assert "Шкалы" not in client.get("/").text

    def test_post_shows_fields_and_result(self, client):
        _login(client)
        response = client.post(
            "/scales",
            data={"scales": "stop_bang", "snore": "1", "tired": "1", "apnea": "1"},
        )
        assert response.status_code == 200
        assert 'name="snore"' in response.text
        assert "3 из 8 — промежуточный риск СОАС" in response.text

    def test_post_keeps_answers(self, client):
        """После пересчёта введённое не теряется (ТЗ §4)."""
        _login(client)
        response = client.post("/scales", data={"scales": "stop_bang", "snore": "1"})
        assert 'name="snore" value="1" checked' in response.text

    def test_sex_is_asked_once_for_two_scales(self, client):
        """Одно поле «Пол» питает STOP-BANG и Apfel (ТЗ §4)."""
        _login(client)
        response = client.post(
            "/scales", data={"scales": ["stop_bang", "apfel"], "sex": "1"}
        )
        assert response.status_code == 200
        # группа радиокнопок «Пол» одна: в шапке группы написано один раз
        assert response.text.count("<legend>Пол") == 1
        assert "учтено из общего поля" in response.text
        # женщина: 0 баллов STOP-BANG, 1 балл Apfel — из одного ответа
        assert "0 из 8 — низкий риск СОАС" in response.text
        assert "1 из 4 — риск ПОТР около 21%" in response.text

    def test_post_without_selection_hints(self, client):
        _login(client)
        response = client.post("/scales", data={})
        assert response.status_code == 200
        assert "Отметьте хотя бы одну шкалу" in response.text

    def test_reset_link_present(self, client):
        _login(client)
        response = client.get("/scales")
        assert 'href="/scales"' in response.text
        assert "Сбросить" in response.text
