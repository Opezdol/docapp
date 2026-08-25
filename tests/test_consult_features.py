"""Тесты доработок консультанта: настройки, аналитика (ФИО/по дням/период),
удаление приказов, ссылки на документ в цитатах. Без сети — фейки."""

import json

import pytest
from fastapi.testclient import TestClient

from docapp.auth.passwords import hash_password
from docapp.consult.service import (
    DEFAULT_SYSTEM_PROMPT,
    SETTING_PROMPT,
    SETTING_TOP_K,
    ConsultService,
)
from docapp.consult.store import SqliteConsultStore
from docapp.domain.employee import DOCTOR, HEAD, Employee
from docapp.storage.sqlite_store import SqliteEmployeeStore
from docapp.web.app import create_app


class FakeEmbed:
    def embed_query(self, text):
        return [1.0, 0.0, 0.0]


class FakeLLM:
    def __init__(self):
        self.last_usage = None
        self.messages = None

    async def stream_chat(self, messages, temperature=0.1):
        self.messages = messages
        yield "Ответ"
        self.last_usage = {"prompt_tokens": 5, "completion_tokens": 3}


# ── настройки: хранилище ──────────────────────────────────────────────

class TestSettingsStore:
    def test_set_get_settings(self, tmp_path):
        with SqliteConsultStore(tmp_path / "consult.db") as store:
            assert store.get_setting("k") is None
            store.set_setting("k", "v")
            assert store.get_setting("k") == "v"
            store.set_setting("k", "v2")  # upsert
            assert store.get_setting("k") == "v2"
            assert store.all_settings() == {"k": "v2"}

    def test_settings_survive_migration_v1(self, tmp_path):
        # v1 БД без таблицы settings — миграция добавляет её.
        import sqlite3
        conn = sqlite3.connect(tmp_path / "consult.db")
        conn.execute("PRAGMA user_version = 1")
        conn.commit()
        conn.close()
        with SqliteConsultStore(tmp_path / "consult.db") as store:
            store.set_setting(SETTING_PROMPT, "привет")
            assert store.get_setting(SETTING_PROMPT) == "привет"


# ── настройки: сервис ─────────────────────────────────────────────────

class TestSettingsService:
    def test_defaults_when_empty(self, tmp_path):
        store = SqliteConsultStore(tmp_path / "consult.db")
        service = ConsultService(store, FakeEmbed(), FakeLLM())
        s = service.settings()
        assert s[SETTING_PROMPT] == DEFAULT_SYSTEM_PROMPT
        assert s[SETTING_TOP_K] == 5
        assert s["temperature"] == 0.1
        assert s["history_messages"] == 6
        store.close()

    def test_prompt_override_used_in_ask(self, tmp_path):
        import numpy as np
        store = SqliteConsultStore(tmp_path / "consult.db")
        doc_id = store.add_document("x.docx", "1", "T", "2026-08-01")
        store.add_chunks(
            doc_id,
            [(0, "", "Текст", np.array([1.0, 0.0, 0.0], dtype=np.float32).tobytes())],
        )
        llm = FakeLLM()
        service = ConsultService(store, FakeEmbed(), llm)
        store.set_setting(SETTING_PROMPT, "Кастомный промпт")
        import asyncio
        asyncio.run(_drain(service.ask(1, "c", "вопрос")))
        assert llm.messages[0]["content"] == "Кастомный промпт"
        store.close()

    def test_invalid_top_k_ignored(self, tmp_path):
        store = SqliteConsultStore(tmp_path / "consult.db")
        service = ConsultService(store, FakeEmbed(), FakeLLM())
        service.update_settings({SETTING_TOP_K: "999"})  # вне диапазона
        assert service.effective_top_k() == 5
        store.close()

    def test_top_k_and_temperature_persisted(self, tmp_path):
        store = SqliteConsultStore(tmp_path / "consult.db")
        service = ConsultService(store, FakeEmbed(), FakeLLM())
        out = service.update_settings({SETTING_TOP_K: "7", "temperature": "0.4"})
        assert out[SETTING_TOP_K] == 7
        assert out["temperature"] == 0.4
        assert store.get_setting(SETTING_TOP_K) == "7"
        assert store.get_setting("temperature") == "0.4"
        store.close()


async def _drain(gen):
    async for _ in gen:
        pass


# ── аналитика: токены по дням и фильтр периода ────────────────────────

class TestTokenByDay:
    def test_token_totals_by_day_groups(self, tmp_path):
        with SqliteConsultStore(tmp_path / "consult.db") as store:
            store.add_message(1, "c1", "assistant", "a", "[]", 10, 2, "2026-08-03T10:00:00")
            store.add_message(1, "c2", "assistant", "b", "[]", 5, 1, "2026-08-03T11:00:00")
            store.add_message(1, "c3", "assistant", "c", "[]", 7, 3, "2026-08-04T09:00:00")
            rows = store.token_totals_by_day()
            by_day = {r["day"]: r for r in rows}
            assert by_day["2026-08-03"]["total_prompt"] == 15
            assert by_day["2026-08-03"]["total_completion"] == 3
            assert by_day["2026-08-04"]["total_prompt"] == 7

    def test_token_totals_period_filter(self, tmp_path):
        with SqliteConsultStore(tmp_path / "consult.db") as store:
            store.add_message(1, "c1", "assistant", "a", "[]", 10, 0, "2026-08-03T10:00:00")
            store.add_message(1, "c2", "assistant", "b", "[]", 10, 0, "2026-08-05T10:00:00")
            rows = store.token_totals(from_date="2026-08-03", to_date="2026-08-04")
            assert rows[0]["count"] == 1  # только 3-е августа


# ── API: настройки, аналитика с ФИО, удаление ─────────────────────────

def _seed_head(db_path):
    with SqliteEmployeeStore(db_path) as es:
        head = es.add(
            Employee(
                last_name="Петров", first_name="Пётр", middle_name="Петрович",
                role=HEAD, login="head", password_hash=hash_password("pass123"),
            )
        )
        es.add(
            Employee(
                last_name="Иванов", first_name="Иван", middle_name="Иванович",
                role=DOCTOR, login="doc", password_hash=hash_password("secret"),
            )
        )
    return head.id


@pytest.fixture
def head_client(tmp_path, monkeypatch):
    monkeypatch.setenv("CONSULT_INDEX_DIR", str(tmp_path / "consult"))
    monkeypatch.setenv("CONSULT_DOCS_DIR", str(tmp_path / "consult" / "documents"))
    db_path = tmp_path / "web.db"
    _seed_head(db_path)
    app = create_app(db_path=db_path, secret="s")
    with TestClient(app, follow_redirects=False) as c:
        c.post("/login", data={"login": "head", "password": "pass123"})
        yield c


@pytest.fixture
def doctor_client(tmp_path, monkeypatch):
    monkeypatch.setenv("CONSULT_INDEX_DIR", str(tmp_path / "consult"))
    monkeypatch.setenv("CONSULT_DOCS_DIR", str(tmp_path / "consult" / "documents"))
    db_path = tmp_path / "web.db"
    _seed_head(db_path)
    app = create_app(db_path=db_path, secret="s")
    with TestClient(app, follow_redirects=False) as c:
        c.post("/login", data={"login": "doc", "password": "secret"})
        yield c


class TestSettingsApi:
    def test_settings_get_and_update(self, head_client):
        r = head_client.get("/orders/settings")
        assert r.status_code == 200
        assert r.json()["system_prompt"] == DEFAULT_SYSTEM_PROMPT

        r = head_client.post(
            "/orders/settings",
            json={"system_prompt": "Свой промпт", "top_k": 8, "temperature": 0.5},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["system_prompt"] == "Свой промпт"
        assert body["top_k"] == 8
        assert body["temperature"] == 0.5

    def test_settings_forbidden_for_doctor(self, doctor_client):
        assert doctor_client.get("/orders/settings").status_code == 403
        assert doctor_client.post("/orders/settings", json={}).status_code == 403


class TestQuestionsWithNames:
    def test_questions_include_names(self, head_client):
        r = head_client.get("/orders/questions")
        assert r.status_code == 200
        body = r.json()
        assert "by_day" in body
        assert isinstance(body["totals"], list)


class TestDeleteDocument:
    def test_delete_requires_head(self, doctor_client):
        r = doctor_client.delete("/orders/documents/999")
        assert r.status_code == 403
        assert r.json()["error"] == "Только заведующий"

    def test_delete_missing_404(self, head_client):
        r = head_client.delete("/orders/documents/999")
        assert r.status_code == 404


def test_citations_include_document_id(tmp_path):
    """search возвращает document_id — ссылки в цитатах кликабельны."""
    import numpy as np
    from docapp.consult.search import SearchIndex
    store = SqliteConsultStore(tmp_path / "consult.db")
    doc_id = store.add_document("x.docx", "1", "T", "2026-08-01")
    store.add_chunks(doc_id, [(0, "Раздел 1", "Осмотр пациента", np.array([1.0, 0.0, 0.0], dtype=np.float32).tobytes())])
    idx = SearchIndex(store)
    hits = idx.search("осмотр пациента", [1.0, 0.0, 0.0], n=1)
    assert hits[0]["document_id"] == doc_id
    store.close()
