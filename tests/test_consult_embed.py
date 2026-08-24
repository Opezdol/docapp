"""Тесты клиента эмбеддингов RouterAI: httpx.MockTransport, без сети."""

import json

import httpx
import pytest

from docapp.consult.config import ConsultConfig
from docapp.consult.embed import EmbeddingClient


def make_config(**overrides) -> ConsultConfig:
    """ConsultConfig для тестов: фиктивные ключ и URL, реальная сеть не нужна."""
    kwargs: dict = dict(
        api_key="key",
        base_url="https://routerai.ru/api/v1",
        embed_model="openai/text-embedding-3-small",
    )
    kwargs.update(overrides)
    return ConsultConfig(**kwargs)


def make_client(handler, **overrides) -> EmbeddingClient:
    """Клиент с инъекцией httpx.MockTransport — точка входа всех тестов."""
    transport = httpx.MockTransport(handler)
    return EmbeddingClient(make_config(**overrides), transport=transport)


def embedding(index: int) -> list[float]:
    """Вектор, однозначно связанный со своим index: первый элемент == index."""
    return [float(index), 0.0, 0.0]


def ok_response(payload: dict) -> httpx.Response:
    """Ответ в форме {"data": [{"index": ..., "embedding": [...]}...]}."""
    n = len(payload["input"])
    data = [{"index": i, "embedding": embedding(i)} for i in range(n)]
    return httpx.Response(200, json={"data": data})


def test_batch_splits_requests():
    """250 текстов при batch_size=100 → ровно 3 запроса по [100, 100, 50]."""
    bodies: list[list[str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        bodies.append(payload["input"])
        return ok_response(payload)

    client = make_client(handler)
    texts = [f"текст {i}" for i in range(250)]
    vectors = client.embed(texts)
    assert len(bodies) == 3
    assert [len(b) for b in bodies] == [100, 100, 50]
    assert len(vectors) == 250


def test_request_shape():
    """URL оканчивается на /embeddings, заголовок и тело запроса корректны."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("Authorization")
        seen["payload"] = json.loads(request.content)
        return ok_response(seen["payload"])

    client = make_client(handler)
    client.embed(["привет"])
    assert seen["url"].endswith("/embeddings")
    assert seen["auth"] == "Bearer key"
    assert seen["payload"]["model"] == "openai/text-embedding-3-small"
    assert seen["payload"]["input"] == ["привет"]


def test_vectors_in_input_order():
    """Ответ в перемешанном порядке с "index" — векторы в порядке входных."""
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        n = len(payload["input"])
        # Отдаём data в обратном порядке индексов — клиент обязан отсортировать.
        data = [{"index": i, "embedding": embedding(i)} for i in range(n - 1, -1, -1)]
        return httpx.Response(200, json={"data": data})

    client = make_client(handler)
    texts = [f"текст {i}" for i in range(10)]
    vectors = client.embed(texts)
    assert [v[0] for v in vectors] == [float(i) for i in range(10)]


def test_retry_on_500():
    """Первый ответ 500, второй 200 — итог успех, запросов ровно 2."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(500, text="internal error")
        return ok_response(json.loads(request.content))

    client = make_client(handler)
    vectors = client.embed(["привет"])
    assert len(calls) == 2
    assert vectors == [[0.0, 0.0, 0.0]]


def test_fails_after_retries():
    """Всегда 500 → RuntimeError после исчерпания попыток."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(500, text="internal error")

    client = make_client(handler)
    with pytest.raises(RuntimeError, match="Не удалось получить эмбеддинги"):
        client.embed(["привет"])
    assert len(calls) == 3


def test_embed_query_single():
    """Один текст → один запрос, возвращается единственный вектор."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        payload = json.loads(request.content)
        assert payload["input"] == ["какой график?"]
        return ok_response(payload)

    client = make_client(handler)
    vector = client.embed_query("какой график?")
    assert vector == [0.0, 0.0, 0.0]
    assert len(calls) == 1
