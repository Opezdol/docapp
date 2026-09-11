"""Модуль «Потребности» для реестра приложения (ADR-0017).

Собирает контейнер (см. `container.py`) один раз при старте приложения; роутер
берёт его через `registry.container_of(request, "needs", NeedsContainer)`.
"""

from __future__ import annotations

from docapp.core.registry import Module
from docapp.needs.catalog import Catalog
from docapp.needs.config import load_needs_config
from docapp.needs.container import NeedsContainer
from docapp.needs.router import router
from docapp.needs.service import NeedsService
from docapp.needs.store import SCHEMA, SqliteNeedsStore


def build() -> NeedsContainer:
    """Собрать контейнер: конфиг, БД заявок, каталог, сервис.

    Инициализация дешёвая и без сети: чтение YAML-каталога и создание
    SQLite-файла заявок.
    """
    config = load_needs_config()
    config.db_path.parent.mkdir(parents=True, exist_ok=True)
    store = SqliteNeedsStore(config.db_path)
    catalog = Catalog(config.catalog_path)
    return NeedsContainer(
        config=config,
        catalog=catalog,
        store=store,
        service=NeedsService(store, catalog),
    )


MODULE = Module(
    name="needs",
    schema=SCHEMA,
    db_path=lambda: load_needs_config().db_path,
    build=build,
    router=router,
)
