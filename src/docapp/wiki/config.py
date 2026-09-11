"""Настройки модуля «Компендиум»: env WIKI_SOURCES_DIR.

Путь к БД сюда не входит: база одна на всё приложение (ADR-0016), её передаёт
AppContext. Здесь остаётся папка PDF-источников — по ADR-0020 источники хранятся
файлами, а не BLOB-ом в БД. ИИ-клиенты берут параметры из общего слоя
`docapp.ai.config` (AI_*).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from docapp.config import DATA_DIR

DEFAULT_SOURCES_DIR = DATA_DIR / "wiki" / "sources"


@dataclass(frozen=True)
class WikiConfig:
    """Конфигурация «Компендиума»: папка PDF-источников."""

    sources_dir: Path


def load_wiki_config() -> WikiConfig:
    """Прочитать WIKI_SOURCES_DIR из окружения и собрать WikiConfig.

    Каталог не создаётся — это забота слоя загрузки источников.
    """
    override = os.environ.get("WIKI_SOURCES_DIR")
    return WikiConfig(
        sources_dir=Path(override) if override else DEFAULT_SOURCES_DIR,
    )
