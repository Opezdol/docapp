"""Настройки подприложения «Компендиум»: env-переменные WIKI_*.

В духе docapp.config и docapp.needs.config: os.environ + dataclass, без pydantic.
ИИ-клиенты (LLM/vision) берут свои параметры из общего слоя docapp.ai.config
(AI_*); здесь — только пути к БД и папке PDF-источников.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from docapp.config import DATA_DIR

DEFAULT_DB_PATH = DATA_DIR / "wiki" / "wiki.db"
DEFAULT_SOURCES_DIR = DATA_DIR / "wiki" / "sources"


@dataclass(frozen=True)
class WikiConfig:
    """Конфигурация «Компендиума»: путь к БД и папке PDF-источников."""

    db_path: Path
    sources_dir: Path


def load_wiki_config() -> WikiConfig:
    """Прочитать WIKI_DB и WIKI_SOURCES_DIR из окружения, собрать WikiConfig.

    Каталоги не создаются — это забота слоя хранения/загрузки.
    """
    db_override = os.environ.get("WIKI_DB")
    sources_override = os.environ.get("WIKI_SOURCES_DIR")
    return WikiConfig(
        db_path=Path(db_override) if db_override else DEFAULT_DB_PATH,
        sources_dir=Path(sources_override) if sources_override else DEFAULT_SOURCES_DIR,
    )
