"""Модуль «Компендиум» для реестра приложения (ADR-0017).

Собирает контейнер (см. `container.py`) один раз при старте; роутер берёт его
через `registry.container_of(request, "wiki", WikiContainer)`.
"""

from __future__ import annotations

import logging

from docapp.ai.config import load_ai_config
from docapp.ai.llm import LLMClient
from docapp.ai.vision import VisionClient
from docapp.core.registry import Module
from docapp.wiki.config import load_wiki_config
from docapp.wiki.container import WikiContainer
from docapp.wiki.router import router
from docapp.wiki.service import WikiService
from docapp.wiki.store import SCHEMA, SqliteWikiStore

logger = logging.getLogger(__name__)


def build() -> WikiContainer:
    """Собрать контейнер: конфиг, БД статей, клиенты ИИ, сервис.

    Инициализация дешёвая и без сети: LLMClient/VisionClient только создают
    HTTP-транспорт, WikiService строит индекс из пустой БД.
    """
    ai_config = load_ai_config()
    if not ai_config.api_key:
        logger.warning(
            "AI_API_KEY не задан: «Компендиум» будет возвращать ошибки "
            "до его настройки в .env"
        )
    config = load_wiki_config()
    config.db_path.parent.mkdir(parents=True, exist_ok=True)
    llm = LLMClient(ai_config)
    vision = VisionClient(ai_config)
    return WikiContainer(
        config=config,
        ai_config=ai_config,
        llm=llm,
        vision=vision,
        service=WikiService(SqliteWikiStore(config.db_path), llm, vision),
    )


MODULE = Module(
    name="wiki",
    schema=SCHEMA,
    db_path=lambda: load_wiki_config().db_path,
    build=build,
    router=router,
)
