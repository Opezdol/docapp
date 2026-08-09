"""Настройки подприложения «Приказы» (ИИ-консультант): env-переменные CONSULT_*.

В духе docapp.config: os.environ + Path, без pydantic. Ключ API — только
из окружения (в .env, который в .gitignore), в git не попадает.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from docapp.config import DATA_DIR


@dataclass(frozen=True)
class ConsultConfig:
    """Конфигурация консультанта: RouterAI, модели и каталоги индекса и приказов."""

    api_key: str = ""
    base_url: str = "https://routerai.ru/api/v1"
    llm_model: str = "gpt-4o-mini"
    embed_model: str = "text-embedding-3-small"
    index_dir: Path = DATA_DIR / "consult"
    docs_dir: Path = DATA_DIR / "consult" / "documents"


def load_consult_config() -> ConsultConfig:
    """Прочитать CONSULT_* из окружения и собрать ConsultConfig.

    index_dir по умолчанию — data/consult/ (относительно корня проекта),
    переопределяется CONSULT_INDEX_DIR; каталог создаётся при необходимости.
    docs_dir по умолчанию — data/consult/documents/ (статичная папка приказов,
    F9 ТЗ), переопределяется CONSULT_DOCS_DIR; каталог тоже создаётся.
    """
    override = os.environ.get("CONSULT_INDEX_DIR")
    index_dir = Path(override) if override else DATA_DIR / "consult"
    index_dir.mkdir(exist_ok=True)
    docs_override = os.environ.get("CONSULT_DOCS_DIR")
    docs_dir = Path(docs_override) if docs_override else DATA_DIR / "consult" / "documents"
    docs_dir.mkdir(exist_ok=True)
    return ConsultConfig(
        api_key=os.environ.get("CONSULT_API_KEY", ""),
        base_url=os.environ.get("CONSULT_BASE_URL", "https://routerai.ru/api/v1"),
        llm_model=os.environ.get("CONSULT_LLM_MODEL", "gpt-4o-mini"),
        embed_model=os.environ.get("CONSULT_EMBED_MODEL", "text-embedding-3-small"),
        index_dir=index_dir,
        docs_dir=docs_dir,
    )
