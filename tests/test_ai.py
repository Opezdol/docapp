"""Тесты общего ИИ-слоя: конфиг, LLM-стриминг, vision-OCR. Без сети — MockTransport."""

import json

import httpx
import pytest

from docapp.ai.config import AIConfig, load_ai_config
from docapp.ai.llm import LLMClient
from docapp.ai.vision import VisionClient


# ── конфиг ───────────────────────────────────────────────────────────

def test_ai_config_defaults():
    config = AIConfig()
    assert config.llm_model == "openai/gpt-5.4-mini"
    assert config.vision_model == "qwen/qwen3.7-flash"
    assert config.base_url == "https://routerai.ru/api/v1"
    assert config.api_key == ""


def test_load_ai_config_env_overrides(monkeypatch):
    monkeypatch.setenv("AI_API_KEY", "k")
    monkeypatch.setenv("AI_BASE_URL", "https://x.test/v1")
    monkeypatch.setenv("AI_LLM_MODEL", "openai/gpt-test")
    monkeypatch.setenv("AI_VISION_MODEL", "qwen/qwen3-vl-test")
    config = load_ai_config()
    assert config.api_key == "k"
    assert config.base_url == "https://x.test/v1"
    assert config.llm_model == "openai/gpt-test"
    assert config.vision_model == "qwen/qwen3-vl-test"


def test_load_ai_config_defaults_without_env(monkeypatch):
    for key in ("AI_API_KEY", "AI_BASE_URL", "AI_LLM_MODEL", "AI_VISION_MODEL"):
        monkeypatch.delenv(key, raising=False)
    config = load_ai_config()
    assert config.api_key == ""
    assert config.llm_model == "openai/gpt-5.4-mini"
    assert config.vision_model == "qwen/qwen3.7-flash"


# ── LLM ──────────────────────────────────────────────────────────────

def make_llm(handler) -> LLMClient:
    config = AIConfig(api_key="key", base_url="https://api.test/v1", llm_model="gpt-test")
    return LLMClient(config, transport=httpx.MockTransport(handler))


def sse_chunk(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def stream_handler(payloads: list[dict], status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        if status >= 400:
            return httpx.Response(status, text='{"error": "unauthorized"}', request=request)
        body = "".join(sse_chunk(p) for p in payloads) + "data: [DONE]\n\n"
        return httpx.Response(200, text=body, request=request)
    return handler


@pytest.mark.asyncio
async def test_llm_stream_tokens_and_usage():
    client = make_llm(stream_handler([
        {"choices": [{"delta": {"content": "От"}}]},
        {"choices": [{"delta": {"content": "вет"}}],
         "usage": {"prompt_tokens": 12, "completion_tokens": 5}},
    ]))
    tokens = [t async for t in client.stream_chat([{"role": "user", "content": "Привет"}])]
    assert "".join(tokens) == "Ответ"
    assert client.last_usage == {"prompt_tokens": 12, "completion_tokens": 5}


@pytest.mark.asyncio
async def test_llm_no_usage():
    client = make_llm(stream_handler([
        {"choices": [{"delta": {"content": "А"}}]},
    ]))
    tokens = [t async for t in client.stream_chat([{"role": "user", "content": "x"}])]
    assert "".join(tokens) == "А"
    assert client.last_usage is None


@pytest.mark.asyncio
async def test_llm_http_error_raises():
    client = make_llm(stream_handler([], status=401))
    with pytest.raises(RuntimeError, match="LLM API ошибка 401"):
        async for _ in client.stream_chat([{"role": "user", "content": "x"}]):
            pass


# ── vision ────────────────────────────────────────────────────────────

def make_vision(handler) -> VisionClient:
    config = AIConfig(api_key="key", base_url="https://api.test/v1", vision_model="qwen-vl")
    return VisionClient(config, transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_vision_ocr_returns_text():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ПРИКАЗ № 1\n\n| A | B |"}}]},
            request=request,
        )

    client = make_vision(handler)
    text = await client.ocr_page(b"PNGDATA")
    assert "ПРИКАЗ № 1" in text
    payload = captured["payload"]
    assert payload["model"] == "qwen-vl"
    content = payload["messages"][0]["content"]
    assert content[0]["type"] == "text"
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


@pytest.mark.asyncio
async def test_vision_http_error_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom", request=request)

    client = make_vision(handler)
    with pytest.raises(RuntimeError, match="Vision API ошибка 500"):
        await client.ocr_page(b"PNGDATA")
