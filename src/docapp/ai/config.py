"""Настройки общего ИИ-слоя: env-переменные AI_*.

В духе docapp.config и docapp.needs.config: os.environ + dataclass,
без pydantic. Ключ API — только из окружения (в .env, который в .gitignore).

RouterAI использует полные идентификаторы моделей вида «провайдер/модель»;
голое имя без префикса провайдера RouterAI не принимает (400).
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class AIConfig:
    """Конфигурация ИИ-слоя: RouterAI, модели ответов и vision (OCR)."""

    api_key: str = ""
    base_url: str = "https://routerai.ru/api/v1"
    llm_model: str = "openai/gpt-5.4-mini"
    vision_model: str = "qwen/qwen3.7-flash"


def load_ai_config() -> AIConfig:
    """Прочитать AI_* из окружения и собрать AIConfig.

    AI_API_KEY — ключ RouterAI; AI_BASE_URL — базовый URL; AI_LLM_MODEL —
    модель ответов на вопросы; AI_VISION_MODEL — мультимодальная модель
    для OCR сканов (текст + таблицы).
    """
    return AIConfig(
        api_key=os.environ.get("AI_API_KEY", ""),
        base_url=os.environ.get("AI_BASE_URL", "https://routerai.ru/api/v1"),
        llm_model=os.environ.get("AI_LLM_MODEL", "openai/gpt-5.4-mini"),
        vision_model=os.environ.get("AI_VISION_MODEL", "qwen/qwen3.7-flash"),
    )
