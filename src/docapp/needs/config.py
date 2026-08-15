"""Настройки подприложения «Потребности»: env-переменные NEEDS_*.

В духе docapp.config: os.environ + Path, без pydantic. Пути по умолчанию
относительны к текущей рабочей директории (корень проекта при запуске
uv run из каталога docapp), переопределяются NEEDS_DB и NEEDS_CATALOG.
"""

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DB_PATH = "data/needs/needs.db"
DEFAULT_CATALOG_PATH = "data/needs/catalog.yaml"


@dataclass(frozen=True)
class NeedsConfig:
    """Конфигурация «Потребностей»: пути к БД заявок и YAML-каталогу."""

    db_path: Path
    catalog_path: Path


def load_needs_config() -> NeedsConfig:
    """Прочитать NEEDS_DB и NEEDS_CATALOG из окружения и собрать NeedsConfig.

    По умолчанию — data/needs/needs.db и data/needs/catalog.yaml,
    разрешённые относительно текущей рабочей директории (консистентно:
    и env-переопределения, и дефолты проходят через .resolve()).
    Каталоги не создаются — это забота слоя хранения.
    """
    db_override = os.environ.get("NEEDS_DB")
    catalog_override = os.environ.get("NEEDS_CATALOG")
    return NeedsConfig(
        db_path=Path(db_override).resolve() if db_override else Path(DEFAULT_DB_PATH).resolve(),
        catalog_path=(
            Path(catalog_override).resolve()
            if catalog_override
            else Path(DEFAULT_CATALOG_PATH).resolve()
        ),
    )
