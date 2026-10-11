"""Настройки модуля «Распределение»: env DISTRIBUTION_DIR.

Путь к БД сюда не входит: база одна на всё приложение (ADR-0016), её передаёт
AppContext. Здесь — только папка прогона ведомости (ADR-0027): прогон живёт
файлами между «Проверить» и «Опубликовать», потому что публикация не должна
требовать повторной загрузки того же файла.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from docapp.config import DATA_DIR

DEFAULT_RUN_DIR = DATA_DIR / "distribution"


@dataclass(frozen=True)
class DistributionConfig:
    """Конфигурация «Распределения»: папка прогона."""

    run_dir: Path


def load_distribution_config() -> DistributionConfig:
    """Прочитать DISTRIBUTION_DIR из окружения и собрать DistributionConfig.

    Каталог не создаётся — это забота хранилища прогона: до первого прогона
    папки может не быть вовсе.
    """
    override = os.environ.get("DISTRIBUTION_DIR")
    return DistributionConfig(
        run_dir=Path(override) if override else DEFAULT_RUN_DIR,
    )
