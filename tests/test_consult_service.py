"""Тесты сервиса консультанта: RAG-цепочка ask, история, аналитика, пустой индекс.

Все фейки работают без сети: эмбеддинги детерминированные, LLM отдаёт
фиксированные токены. Хранилище — SQLite во временной директории.
"""

import json

import numpy as np
import pytest

from docapp.consult.service import ConsultService
from docapp.consult.store import SqliteConsultStore


def emb(values):
    """Эмбеддинг float32 -> байты для хранения в SQLite."""
    return np.array(values, dtype=np.float32).tobytes()


class FakeEmbed:
    """Детерминированный эмбеддер без сети.

    Тексты с «пациент» дают [1.0, 0.0, 0.0] (совпадает с чанком тестовой БД),
    остальные — [0.0, 1.0, 0.0].
    """

    def embed_query(self, text: str) -> list[float]:
        if "пациент" in text:
            return [1.0, 0.0, 0.0]
        return [0.0, 1.0, 0.0]


class FakeLLM:
    """Фейк LLM (duck typing, без наследния LLMClient): фиксированные токены.

    Запоминает отправленные messages в self.messages и выставляет
    self.last_usage — как настоящий LLMClient после стрима.
    """

    def __init__(self):
        self.messages = None
        self.last_usage = None

    async def stream_chat(self, messages: list[dict], temperature: float = 0.1):
        self.messages = messages
        yield "От"
        yield "вет"
        self.last_usage = {"prompt_tokens": 10, "completion_tokens": 4}


@pytest.fixture
def store_factory(tmp_path):
    """Фабрика хранилища во временной БД; все хранилища закрываются в конце теста."""
    stores = []

    def factory():
        s = SqliteConsultStore(tmp_path / "consult.db")
        stores.append(s)
        return s

    yield factory
    for s in stores:
        s.close()


@pytest.fixture
def empty_store_factory(tmp_path):
    """Фабрика пустого хранилища (своя БД, чтобы не пересекаться с store_factory)."""
    stores = []

    def factory():
        s = SqliteConsultStore(tmp_path / "consult-empty.db")
        stores.append(s)
        return s

    yield factory
    for s in stores:
        s.close()


def build_filled_store(store_factory):
    """Хранилище с 1 документом и 1 чанком про осмотр пациента."""
    store = store_factory()
    doc_id = store.add_document(
        "prikaz-123.docx", "123", "ПРИКАЗ № 123", "2026-08-01T10:00:00"
    )
    store.add_chunks(
        doc_id,
        [
            (
                0,
                "Раздел 1",
                "Дежурный анестезиолог обязан осмотреть пациента до операции.",
                emb([1.0, 0.0, 0.0]),
            )
        ],
    )
    return store


def make_service(store, llm=None):
    """Сервис на хранилище store с фейковыми эмбеддером и LLM."""
    return ConsultService(store, FakeEmbed(), llm or FakeLLM())


@pytest.mark.asyncio
async def test_ask_streams_and_saves(store_factory):
    store = build_filled_store(store_factory)
    service = make_service(store)

    events = [
        event
        async for event in service.ask(1, "conv-1", "что должен сделать анестезиолог?")
    ]

    deltas = "".join(e["text"] for e in events if e["type"] == "delta")
    assert deltas == "Ответ"

    done = [e for e in events if e["type"] == "done"][0]
    assert done["citations"][0]["doc_number"] == "123"
    assert done["prompt_tokens"] == 10
    assert done["completion_tokens"] == 4

    messages = store.list_messages("conv-1")
    assert len(messages) == 2
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[0]["content"] == "что должен сделать анестезиолог?"
    assert messages[1]["content"] == "Ответ"
    assert messages[1]["prompt_tokens"] == 10
    assert messages[1]["completion_tokens"] == 4
    citations = json.loads(messages[1]["citations"])
    assert citations[0]["doc_number"] == "123"


@pytest.mark.asyncio
async def test_ask_empty_index(empty_store_factory):
    store = empty_store_factory()
    service = make_service(store)

    events = [event async for event in service.ask(1, "conv-1", "любой вопрос")]

    deltas = [e["text"] for e in events if e["type"] == "delta"]
    assert any("Индекс пуст" in d for d in deltas)

    done = [e for e in events if e["type"] == "done"][0]
    assert done["citations"] == []
    assert done["prompt_tokens"] == 0
    assert done["completion_tokens"] == 0

    # сообщения не сохраняются при пустом индексе
    assert store.list_messages("conv-1") == []


@pytest.mark.asyncio
async def test_ask_uses_history(store_factory):
    store = build_filled_store(store_factory)
    llm = FakeLLM()
    service = make_service(store, llm)

    history = [
        {"role": "user", "content": "старый вопрос"},
        {"role": "assistant", "content": "старый ответ"},
    ]
    async for _ in service.ask(1, "conv-1", "новый вопрос", history=history):
        pass

    assert [m["role"] for m in llm.messages] == ["system", "user", "assistant", "user"]
    assert llm.messages[1]["content"] == "старый вопрос"
    assert llm.messages[2]["content"] == "старый ответ"
    assert llm.messages[3]["content"].startswith("Вопрос: новый вопрос")
    assert "Фрагменты приказов" in llm.messages[3]["content"]


@pytest.mark.asyncio
async def test_recent_questions(store_factory):
    store = build_filled_store(store_factory)
    service = make_service(store)

    async for _ in service.ask(1, "conv-1", "вопрос первый"):
        pass
    async for _ in service.ask(1, "conv-2", "вопрос второй"):
        pass

    questions = service.recent_questions(employee_id=1)
    assert len(questions) == 2
    assert {q["content"] for q in questions} == {"вопрос первый", "вопрос второй"}
    assert {q["conversation_id"] for q in questions} == {"conv-1", "conv-2"}
    assert all(q["employee_id"] == 1 for q in questions)
    assert all(q["created_at"] for q in questions)


@pytest.mark.asyncio
async def test_token_totals(store_factory):
    store = build_filled_store(store_factory)
    service = make_service(store)

    async for _ in service.ask(1, "conv-1", "вопрос"):
        pass

    rows = service.token_totals()
    assert len(rows) == 1
    row = rows[0]
    assert row["employee_id"] == 1
    # токены несут только assistant-сообщения; user-сообщения сохраняются с нулями
    assert row["total_prompt"] == 10
    assert row["total_completion"] == 4
    assert row["count"] == 2  # user + assistant
