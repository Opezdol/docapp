"""Контейнер модуля «Компендиум» (ADR-0017).

Отдельно от `module.py`, чтобы роутер импортировал тип контейнера без цикла.
"""

from __future__ import annotations

from dataclasses import dataclass

from docapp.ai.config import AIConfig
from docapp.ai.llm import LLMClient
from docapp.ai.vision import VisionClient
from docapp.wiki.config import WikiConfig
from docapp.wiki.service import WikiService


@dataclass(frozen=True)
class WikiContainer:
    """Всё, чем владеет модуль: конфиг, клиенты ИИ и сервис статей."""

    config: WikiConfig
    ai_config: AIConfig
    llm: LLMClient
    vision: VisionClient
    service: WikiService
