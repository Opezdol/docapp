"""Настройки модуля «Потребности»: env-переменная NEEDS_CATALOG.

Путь к БД сюда не входит: база одна на всё приложение (ADR-0016), её передаёт
AppContext. Здесь остаётся то, что принадлежит модулю, — файл-каталог расходки,
который до шага 6 живёт вне БД.
"""

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CATALOG_PATH = "data/needs/catalog.yaml"


@dataclass(frozen=True)
class NeedsConfig:
    """Конфигурация «Потребностей»: путь к YAML-каталогу расходки."""

    catalog_path: Path


def load_needs_config() -> NeedsConfig:
    """Прочитать NEEDS_CATALOG из окружения и собрать NeedsConfig.

    По умолчанию — data/needs/catalog.yaml относительно текущей рабочей
    директории (корень проекта при `uv run`). Каталоги не создаются — это
    забота слоя хранения.
    """
    override = os.environ.get("NEEDS_CATALOG")
    return NeedsConfig(
        catalog_path=(
            Path(override).resolve() if override else Path(DEFAULT_CATALOG_PATH).resolve()
        ),
    )
