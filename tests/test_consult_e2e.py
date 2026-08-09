"""Сквозной E2E-тест подприложения «Приказы» (ТЗ-консультант, задача T11).

Полная цепочка без сети: сборка индекса (build.build_index с фейковым
эмбеддером) -> приложение (create_app) -> вход -> страница /orders ->
документы -> вопрос со стримингом SSE -> сохранение диалога с токенами ->
переиндексация. Фейками подменяются только эмбеддер и LLM; всё остальное
реальное: БД, парсинг .docx, нарезка, поиск (numpy+BM25), роутер, шаблоны.
pytest-asyncio не нужен: TestClient выполняет async-цепочку сам.
"""

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from docapp.auth.passwords import hash_password
from docapp.consult import build
from docapp.consult.config import load_consult_config
from docapp.consult.service import ConsultService
from docapp.consult.store import SqliteConsultStore
from docapp.domain.employee import DOCTOR, HEAD, Employee
from docapp.storage.sqlite_store import SqliteEmployeeStore
from docapp.web.app import create_app


class FakeEmbed:
    """Фейковый эмбеддер: фиксированный вектор [1.0, 0.0, 0.0] (без сети).

    Конструктор принимает config (как у EmbeddingClient), чтобы
    build.build_index мог создавать его через EmbeddingClient(config).
    """

    def __init__(self, config=None) -> None:
        self.config = config

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0, 0.0] for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        return [1.0, 0.0, 0.0]


class FakeLLM:
    """Фейковый LLM: два токена ответа и usage из финального чанка (без сети)."""

    def __init__(self) -> None:
        self.last_usage: dict[str, int] | None = None
        self.messages: list[dict] | None = None

    async def stream_chat(self, messages, temperature: float = 0.1):
        self.messages = messages
        self.last_usage = {"prompt_tokens": 5, "completion_tokens": 3}
        yield "Ответ"
        yield " [1]"


def _seed_employees(db_path: Path) -> None:
    """Заведующий (head/pass123) и врач (doc/secret) — по образцу _seed из test_web.py."""
    with SqliteEmployeeStore(db_path) as es:
        es.add(
            Employee(
                last_name="Петров",
                first_name="Пётр",
                middle_name="Петрович",
                role=HEAD,
                login="head",
                password_hash=hash_password("pass123"),
            )
        )
        es.add(
            Employee(
                last_name="Иванов",
                first_name="Иван",
                middle_name="Иванович",
                role=DOCTOR,
                login="doc",
                password_hash=hash_password("secret"),
            )
        )


def _login(client, login="head", password="pass123"):
    return client.post("/login", data={"login": login, "password": password})


def _sse_events(body: str) -> list[dict]:
    """Разобрать SSE-поток в список событий (строки «data: JSON»)."""
    events = []
    for block in body.split("\n\n"):
        if not block.strip():
            continue
        for line in block.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[len("data: "):]))
    return events


@pytest.fixture
def e2e(tmp_path, monkeypatch, sample_docx):
    """Полная цепочка: собранный индекс -> приложение -> подмена сети -> клиент."""
    # 1. Индекс — в tmp-каталоге, env ставится ДО создания приложения,
    #    чтобы create_app (load_consult_config) указывал туда же.
    monkeypatch.setenv("CONSULT_INDEX_DIR", str(tmp_path / "consult"))

    # 2. Один приказ в папке документов.
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    shutil.copy(sample_docx, docs_dir / "prikaz_123.docx")

    # 3. Эмбеддер в build — фейк (сеть не используется).
    monkeypatch.setattr(build, "EmbeddingClient", FakeEmbed)

    # 4. Реальная сборка индекса: парсинг .docx, нарезка, запись в SQLite.
    config = load_consult_config()
    n_chunks = build.build_index(docs_dir, config, config.index_dir / "consult.db")
    assert n_chunks > 0

    # 5. Сотрудники: заведующий и врач.
    web_db = tmp_path / "web.db"
    _seed_employees(web_db)

    # 6. Приложение: консультант инициализируется из собранного consult.db
    #    (создание клиентов — без сетевых вызовов, только транспорт).
    app = create_app(db_path=web_db, secret="test-secret")

    # 7. Подмена сети: фейковые эмбеддер и LLM, сервис поверх реального индекса.
    state = app.state.consult
    store = SqliteConsultStore(config.index_dir / "consult.db")
    state["embed"] = FakeEmbed()
    state["llm"] = FakeLLM()
    state["service"] = ConsultService(store, state["embed"], state["llm"])

    # 8. Тестовый клиент без следования редиректам.
    with TestClient(app, follow_redirects=False) as c:
        yield c


class TestE2E:
    """Сквозные сценарии: страница, документы, вопрос, история, права, reindex."""

    def test_page_renders_with_stats(self, e2e):
        _login(e2e)
        r = e2e.get("/orders")
        assert r.status_code == 200
        assert "Консультант" in r.text
        # Статистика — из реально собранного индекса (1 документ из sample_docx).
        assert "1 документов" in r.text

    def test_documents_show_built_index(self, e2e):
        _login(e2e)
        r = e2e.get("/orders/documents")
        assert r.status_code == 200
        body = r.json()
        assert body["documents"][0]["filename"] == "prikaz_123.docx"
        assert body["chunks"] > 0

    def test_ask_end_to_end_sse(self, e2e):
        _login(e2e)
        r = e2e.post(
            "/orders/ask", json={"question": "что должен сделать анестезиолог?"}
        )
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        assert '"type": "delta"' in r.text
        assert '"type": "done"' in r.text
        # Цитаты — из реального поиска по индексу: номер приказа "123"
        # извлечён парсером/нарезкой из sample_docx.
        assert '"doc_number": "123"' in r.text
        done = [e for e in _sse_events(r.text) if e["type"] == "done"][0]
        assert done["conversation_id"]
        assert done["citations"][0]["doc_number"] == "123"
        assert done["prompt_tokens"] == 5
        assert done["completion_tokens"] == 3

    def test_message_saved_after_ask(self, e2e):
        _login(e2e)
        r = e2e.post(
            "/orders/ask", json={"question": "что должен сделать анестезиолог?"}
        )
        done = [e for e in _sse_events(r.text) if e["type"] == "done"][0]
        conv_id = done["conversation_id"]

        # История из API: вопрос и ответ с цитатами.
        r = e2e.get(f"/orders/conversation?conversation_id={conv_id}")
        assert r.status_code == 200
        messages = r.json()["messages"]
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "что должен сделать анестезиолог?"
        assert messages[1]["role"] == "assistant"
        assert messages[1]["content"] == "Ответ [1]"
        assert messages[1]["citations"][0]["doc_number"] == "123"

        # Учёт токенов (F5): usage из финального чанка фейкового LLM сохранён.
        store = e2e.app.state.consult["service"].store
        rows = store.list_messages(conv_id)
        assert rows[1]["prompt_tokens"] == 5
        assert rows[1]["completion_tokens"] == 3

    def test_ask_401_anonymous(self, e2e):
        r = e2e.post("/orders/ask", json={"question": "вопрос"})
        assert r.status_code == 401

    def test_reindex_as_head_ok(self, e2e):
        _login(e2e)
        r = e2e.post("/orders/reindex")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        # Сервис пересоздан из реального consult.db (без сети — фейки в state).
        assert body["documents"] == 1
        assert body["chunks"] > 0
