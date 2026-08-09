"""Тесты API подприложения «Приказы»: страница, SSE-чат, документы, переиндексация.

Сервис консультанта подменяется фейком (FakeService) — сеть и реальные
клиенты RouterAI в тестах не используются. Стиль — как в test_web.py:
TestClient(follow_redirects=False), tmp_path БД, _seed.
"""

import json

import pytest
from fastapi.testclient import TestClient

from docapp.auth.passwords import hash_password
from docapp.domain.employee import DOCTOR, HEAD, Employee
from docapp.storage.sqlite_store import SqliteEmployeeStore
from docapp.web.app import create_app


class FakeStore:
    """Минимальная заглушка хранилища: у reindex только close()."""

    def close(self) -> None:
        pass


class FakeService:
    """Фейк ConsultService: фиксированные документы/статистика и стрим событий."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.store = FakeStore()

    def documents(self) -> list[dict]:
        return [
            {
                "filename": "prikaz-001.docx",
                "doc_number": "001",
                "title": "О графике",
                "added_at": "2026-08-01T10:00:00",
            }
        ]

    def index_stats(self) -> dict:
        return {"documents": 1, "chunks": 3}

    async def ask(self, employee_id, conversation_id, question, history=None):
        self.calls.append(
            {
                "employee_id": employee_id,
                "conversation_id": conversation_id,
                "question": question,
            }
        )
        yield {"type": "delta", "text": "От"}
        yield {
            "type": "done",
            "citations": [{"doc_number": "001"}],
            "prompt_tokens": 10,
            "completion_tokens": 4,
        }


def _seed(db_path) -> dict:
    """Создать врача и заведующего (HEAD), вернуть их id."""
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
        head = es.add(
            Employee(
                last_name="Петров",
                first_name="Пётр",
                role=HEAD,
                login="petrov",
                password_hash=hash_password("pass123"),
            )
        )
    return {"doctor_id": doctor.id, "head_id": head.id}


@pytest.fixture
def client(tmp_path):
    db_path = tmp_path / "web.db"
    _seed(db_path)
    app = create_app(db_path=db_path, secret="test-secret")
    # Реальный сервис консультанта подменяется фейком — без сети и LLM.
    app.state.consult["service"] = FakeService()
    with TestClient(app, follow_redirects=False) as c:
        yield c


def _login(client, login="ivanov", password="secret"):
    return client.post("/login", data={"login": login, "password": password})


def _sse_events(body: str) -> list[dict]:
    """Разобрать SSE-поток в список событий."""
    events = []
    for block in body.split("\n\n"):
        if not block.strip():
            continue
        for line in block.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[len("data: "):]))
    return events


class TestOrdersPage:
    """Страница /orders (F1)."""

    def test_orders_requires_login(self, client):
        r = client.get("/orders", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/login"

    def test_orders_page_renders(self, client):
        _login(client)
        r = client.get("/orders")
        assert r.status_code == 200
        assert "Консультант" in r.text
        assert "1 документов" in r.text


class TestAsk:
    """POST /orders/ask — стриминг ответа (F2/F3/F5)."""

    def test_ask_streams_sse(self, client):
        _login(client)
        r = client.post("/orders/ask", json={"question": "вопрос"})
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        assert 'data: {"type": "delta"' in r.text
        assert '"type": "done"' in r.text
        assert "conversation_id" in r.text

    def test_ask_returns_same_conversation_id(self, client):
        _login(client)
        r = client.post(
            "/orders/ask", json={"question": "вопрос", "conversation_id": "conv-abc"}
        )
        done = [e for e in _sse_events(r.text) if e["type"] == "done"][0]
        assert done["conversation_id"] == "conv-abc"

    def test_ask_empty_question_400(self, client):
        _login(client)
        r = client.post("/orders/ask", json={"question": "  "})
        assert r.status_code == 400

    def test_ask_requires_login(self, client):
        r = client.post("/orders/ask", json={"question": "вопрос"})
        assert r.status_code == 401


class TestReindex:
    """POST /orders/reindex — только заведующий (F7)."""

    def test_reindex_requires_head(self, client):
        _login(client)  # врач — доступ запрещён
        r = client.post("/orders/reindex")
        assert r.status_code == 403
        assert r.json()["error"] == "Только заведующий"

        _login(client, login="petrov", password="pass123")  # заведующий
        r = client.post("/orders/reindex")
        assert r.status_code == 200
        assert r.json()["ok"] is True


class TestDocuments:
    """GET /orders/documents — список документов (F6)."""

    def test_documents_list(self, client):
        _login(client)
        r = client.get("/orders/documents")
        assert r.status_code == 200
        body = r.json()
        assert body["chunks"] == 3
        assert body["documents"][0]["filename"] == "prikaz-001.docx"

    def test_documents_requires_login(self, client):
        r = client.get("/orders/documents")
        assert r.status_code == 401
