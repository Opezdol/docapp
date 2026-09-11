"""Модуль «Потребности» для реестра приложения (ADR-0017).

Собирает контейнер (см. `container.py`) один раз при старте приложения; роутер
берёт его через `registry.container_of(request, "needs", NeedsContainer)`.
"""

from __future__ import annotations

from docapp.core.registry import AppContext, Module
from docapp.needs.catalog import Catalog
from docapp.needs.config import load_needs_config
from docapp.needs.container import NeedsContainer
from docapp.needs.router import router
from docapp.needs.service import NeedsService
from docapp.needs.store import SCHEMA, SqliteNeedsStore


def build(context: AppContext) -> NeedsContainer:
    """Собрать контейнер: БД приложения, каталог расходки, сервис.

    Инициализация дешёвая и без сети: чтение YAML-каталога; БД — общая для
    приложения (ADR-0016), путь приходит из AppContext.
    """
    config = load_needs_config()
    store = SqliteNeedsStore(context.db_path)
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
    build=build,
    router=router,
)
