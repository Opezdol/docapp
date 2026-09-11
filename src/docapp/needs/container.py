"""Контейнер модуля «Потребности» (ADR-0017).

Отдельно от `module.py`, чтобы роутер мог импортировать тип контейнера, не
создавая цикла: `module.py` собирает контейнер и ссылается на роутер.
"""

from __future__ import annotations

from dataclasses import dataclass

from docapp.needs.catalog import Catalog
from docapp.needs.config import NeedsConfig
from docapp.needs.service import NeedsService
from docapp.needs.store import SqliteNeedsStore


@dataclass(frozen=True)
class NeedsContainer:
    """Всё, чем владеет модуль: конфиг, каталог расходки, хранилище и сервис."""

    config: NeedsConfig
    catalog: Catalog
    store: SqliteNeedsStore
    service: NeedsService
