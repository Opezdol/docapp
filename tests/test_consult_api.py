"""Тесты API подприложения «Приказы»: страница, SSE-чат, документы, переиндексация.

Сервис консультанта подменяется фейком (FakeService) — сеть и реальные
клиенты RouterAI в тестах не используются. Стиль — как в test_web.py:
TestClient(follow_redirects=False), tmp_path БД, _seed.
"""

import json
import logging

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
                "id": 1,
                "filename": "prikaz-001.docx",
                "doc_number": "001",
                "title": "О графике",
                "added_at": "2026-08-01T10:00:00",
            }
        ]

    def document_text(self, document_id):
        return {1: "Полный текст приказа о графике.", 2: "текст"}.get(document_id)

    def document_source(self, document_id):
        if document_id == 1:
            return {"source": b"%PDF-1.4 fake", "source_name": "prikaz-001.pdf"}
        return None

    def index_stats(self) -> dict:
        return {"documents": 1, "chunks": 3}

    def conversation(self, employee_id, conversation_id):
        return [
            {
                "role": "user",
                "content": "Старый вопрос",
                "citations": [],
                "created_at": "2026-08-01T10:00:00",
            }
        ]

    def recent_questions(self, limit=100):
        return [{"employee_id": 1, "content": "Что?"}]

    def token_totals(self):
        return [
            {"employee_id": 1, "total_prompt": 10, "total_completion": 4, "count": 2}
        ]

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
def client(tmp_path, monkeypatch):
    # Консультант полностью в tmp: пересборка индекса (reindex/upload)
    # не должна трогать реальную data/consult и сеть.
    monkeypatch.setenv("CONSULT_INDEX_DIR", str(tmp_path / "consult"))
    monkeypatch.setenv("CONSULT_DOCS_DIR", str(tmp_path / "consult" / "documents"))
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
        body = r.json()
        assert body["ok"] is True
        assert body["busy"] is True  # пересборка ушла в фон


class TestStatus:
    """GET /orders/status — статус фоновой пересборки индекса (F11)."""

    def test_status_endpoint(self, client):
        _login(client)
        r = client.get("/orders/status")
        assert r.status_code == 200
        body = r.json()
        assert "busy" in body
        assert body["busy"] is False
        assert "documents" in body
        assert body["documents"] == 1  # из FakeService.index_stats
        assert "chunks" in body

    def test_status_requires_login(self, client):
        r = client.get("/orders/status")
        assert r.status_code == 401


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


class TestConversation:
    """GET /orders/conversation — восстановление диалога (F3)."""

    def test_conversation_returns_messages(self, client):
        _login(client)
        r = client.get("/orders/conversation?conversation_id=conv-1")
        assert r.status_code == 200
        messages = r.json()["messages"]
        assert messages[0]["content"] == "Старый вопрос"
        assert messages[0]["role"] == "user"

    def test_conversation_empty_without_id(self, client):
        _login(client)
        r = client.get("/orders/conversation")
        assert r.status_code == 200
        assert r.json()["messages"] == []

    def test_conversation_requires_login(self, client):
        r = client.get("/orders/conversation?conversation_id=conv-1")
        assert r.status_code == 401


class TestQuestions:
    """GET /orders/questions — аналитика заведующего (F4)."""

    def test_questions_head_only(self, client):
        _login(client)  # врач — доступ запрещён
        r = client.get("/orders/questions")
        assert r.status_code == 403
        assert r.json()["error"] == "Только заведующий"

        _login(client, login="petrov", password="pass123")  # заведующий
        r = client.get("/orders/questions")
        assert r.status_code == 200
        body = r.json()
        assert body["questions"][0]["content"] == "Что?"
        assert body["totals"][0]["total_prompt"] == 10
        assert body["totals"][0]["total_completion"] == 4

    def test_questions_requires_login(self, client):
        r = client.get("/orders/questions")
        assert r.status_code == 401


class TestDocumentReadDownload:
    """Чтение и скачивание приказов (F10): страница текста и оригинальный файл."""

    def test_document_page_renders(self, client):
        _login(client)
        r = client.get("/orders/documents/1")
        assert r.status_code == 200
        assert "Полный текст приказа" in r.text
        assert "Скачать оригинал" in r.text
        assert "Приказ №001" in r.text

    def test_document_page_missing_404(self, client):
        _login(client)
        r = client.get("/orders/documents/999")
        assert r.status_code == 404

    def test_document_download_original(self, client):
        _login(client)
        r = client.get("/orders/documents/1/download")
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/pdf"
        assert "prikaz-001.pdf" in r.headers["content-disposition"]
        assert r.content == b"%PDF-1.4 fake"

    def test_document_download_txt_fallback(self, client):
        _login(client)
        r = client.get("/orders/documents/2/download")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/plain")
        assert r.content == b"\xd1\x82\xd0\xb5\xd0\xba\xd1\x81\xd1\x82"

    def test_document_page_requires_login(self, client):
        r = client.get("/orders/documents/1", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/login"

    def test_document_download_requires_login(self, client):
        r = client.get("/orders/documents/1/download", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/login"


class TestStartupChecks:
    """Проверки при старте приложения: предупреждения и лимиты (гигиена VPS)."""

    def test_missing_api_key_logs_warning(self, monkeypatch, tmp_path, caplog):
        # Без ключа RouterAI приложение стартует, но пишет warning.
        monkeypatch.delenv("CONSULT_API_KEY", raising=False)
        monkeypatch.setenv("CONSULT_INDEX_DIR", str(tmp_path / "consult"))
        monkeypatch.setenv("CONSULT_DOCS_DIR", str(tmp_path / "consult" / "documents"))
        with caplog.at_level(logging.WARNING):
            create_app(tmp_path / "w.db", "s")
        assert any("CONSULT_API_KEY" in r.message for r in caplog.records)

    def test_upload_size_limit(self, monkeypatch, client):
        # Файл больше лимита — 400 ДО запуска фоновой пересборки (сети нет).
        monkeypatch.setattr("docapp.consult.router.MAX_UPLOAD_BYTES", 100)
        _login(client, login="petrov", password="pass123")  # заведующий
        r = client.post(
            "/orders/documents/upload",
            files=[("files", ("big.pdf", b"x" * 200, "application/pdf"))],
        )
        assert r.status_code == 400
        assert "слишком большой" in r.text

    def test_upload_ok_size(self, monkeypatch, client):
        # Файл в пределах лимита не даёт 400 по размеру: врач получает 403
        # (роль не та) — до запуска пересборки и сети дело не доходит.
        monkeypatch.setattr("docapp.consult.router.MAX_UPLOAD_BYTES", 100)
        _login(client)  # врач
        r = client.post(
            "/orders/documents/upload",
            files=[("files", ("small.pdf", b"x" * 50, "application/pdf"))],
        )
        assert r.status_code == 403
        assert r.json()["error"] == "Только заведующий"

