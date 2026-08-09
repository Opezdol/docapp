"""Тесты LLM-клиента консультанта: стриминг токенов, usage, заголовки, ошибки.

Все запросы идут через httpx.MockTransport — сеть не используется.
"""

import json

import httpx
import pytest

from docapp.consult.config import ConsultConfig
from docapp.consult.llm import LLMClient


def make_client(handler) -> LLMClient:
    config = ConsultConfig(
        api_key="key", base_url="https://api.test/v1", llm_model="gpt-test"
    )
    return LLMClient(config, transport=httpx.MockTransport(handler))


def sse_chunk(payload: dict) -> str:
    """Один чанк SSE: data: {json}."""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def stream_handler(payloads: list[dict], status: int = 200):
    """Хендлер MockTransport: SSE-ответ из чанков payloads + [DONE]."""

    def handler(request: httpx.Request) -> httpx.Response:
        if status >= 400:
            return httpx.Response(status, text='{"error": "unauthorized"}', request=request)
        body = "".join(sse_chunk(p) for p in payloads) + "data: [DONE]\n\n"
        return httpx.Response(200, text=body, request=request)

    return handler


@pytest.mark.asyncio
async def test_stream_tokens_and_usage():
    client = make_client(
        stream_handler(
            [
                {"choices": [{"delta": {"content": "Ответ"}}]},
                {"choices": [{"delta": {}}], "usage": {"prompt_tokens": 12, "completion_tokens": 5}},
            ]
        )
    )
    tokens = [t async for t in client.stream_chat([{"role": "user", "content": "Привет"}])]
    assert "".join(tokens) == "Ответ"
    assert client.last_usage == {"prompt_tokens": 12, "completion_tokens": 5}


@pytest.mark.asyncio
async def test_no_usage():
    client = make_client(
        stream_handler(
            [
                {"choices": [{"delta": {"content": "А"}}]},
                {"choices": [{"delta": {"content": "Б"}}]},
            ]
        )
    )
    tokens = [t async for t in client.stream_chat([{"role": "user", "content": "x"}])]
    assert "".join(tokens) == "АБ"
    assert client.last_usage is None


@pytest.mark.asyncio
async def test_authorization_header():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("Authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, text="data: [DONE]\n\n", request=request)

    client = make_client(handler)
    async for _ in client.stream_chat([{"role": "user", "content": "вопрос"}]):
        pass
    assert captured["auth"] == "Bearer key"
    assert captured["body"]["model"] == "gpt-test"
    assert captured["body"]["stream"] is True
    assert captured["body"]["temperature"] == 0.1


@pytest.mark.asyncio
async def test_http_error_raises():
    client = make_client(stream_handler([], status=401))
    with pytest.raises(RuntimeError, match="LLM API ошибка 401"):
        async for _ in client.stream_chat([{"role": "user", "content": "x"}]):
            pass


@pytest.mark.asyncio
async def test_last_usage_reset():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            payloads = [
                {"choices": [{"delta": {"content": "да"}}], "usage": {"prompt_tokens": 3, "completion_tokens": 1}}
            ]
        else:
            payloads = [{"choices": [{"delta": {"content": "нет"}}]}]
        body = "".join(sse_chunk(p) for p in payloads) + "data: [DONE]\n\n"
        return httpx.Response(200, text=body, request=request)

    client = make_client(handler)
    async for _ in client.stream_chat([{"role": "user", "content": "q"}]):
        pass
    assert client.last_usage == {"prompt_tokens": 3, "completion_tokens": 1}
    async for _ in client.stream_chat([{"role": "user", "content": "q"}]):
        pass
    assert client.last_usage is None
