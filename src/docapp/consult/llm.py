"""LLM-клиент (OpenAI-совместимый): стриминговые ответы чата и учёт токенов.

Используется консультантом для генерации ответов (ТЗ, задача T7, требование
F5 — учёт токенов): usage приходит в последнем чанке стрима и сохраняется
в self.last_usage для записи в хранилище.
"""

import json
from collections.abc import AsyncIterator

import httpx

from docapp.consult.config import ConsultConfig


class LLMClient:
    """Клиент к OpenAI-совместимому API (RouterAI) для стримингового чата.

    Только стриминг: POST {base_url}/chat/completions с stream=True.
    transport передаётся для тестов (httpx.MockTransport), как в embed-клиенте.
    """

    def __init__(
        self, config: ConsultConfig, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.config = config
        self.transport = transport
        self.last_usage: dict[str, int] | None = None

    async def stream_chat(
        self, messages: list[dict], temperature: float = 0.1
    ) -> AsyncIterator[str]:
        """Отправить messages в /chat/completions и отдавать токены ответа по мере поступления.

        Перед каждым вызовом self.last_usage сбрасывается в None. OpenAI-совместимые
        API шлют usage в последнем чанке ({"usage": {"prompt_tokens": N,
        "completion_tokens": M}}) — если такой чанк встретился, usage сохраняется
        в self.last_usage, иначе остаётся None.
        """
        self.last_usage = None
        url = f"{self.config.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        payload = {
            "model": self.config.llm_model,
            "messages": messages,
            "stream": True,
            "temperature": temperature,
        }
        async with httpx.AsyncClient(timeout=120.0, transport=self.transport) as client:
            async with client.stream("POST", url, headers=headers, json=payload) as resp:
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode("utf-8", errors="replace")
                    raise RuntimeError(
                        f"LLM API ошибка {resp.status_code}: {body[:200]}"
                    )
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    if line == "data: [DONE]":
                        break
                    if line.startswith("data: "):
                        line = line[6:]
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    usage = chunk.get("usage")
                    if usage:
                        self.last_usage = {
                            "prompt_tokens": usage["prompt_tokens"],
                            "completion_tokens": usage["completion_tokens"],
                        }
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    content = delta.get("content")
                    if content:
                        yield content
