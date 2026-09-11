"""Модуль «Потребности» для реестра приложения (ADR-0017).

Собирает контейнер (см. `container.py`) один раз при старте приложения; роутер
берёт его через `registry.container_of(request, "needs", NeedsContainer)`.
"""

from __future__ import annotations

import logging
from pathlib import Path

from docapp.core.registry import AppContext, Module
from docapp.needs.catalog_store import SqliteCatalog
from docapp.needs.catalog_yaml import parse_file
from docapp.needs.config import load_needs_config
from docapp.needs.container import NeedsContainer
from docapp.needs.router import router
from docapp.needs.service import NeedsService
from docapp.needs.store import SCHEMA, SqliteNeedsStore

logger = logging.getLogger(__name__)


def build(context: AppContext) -> NeedsContainer:
    """Собрать контейнер: БД приложения, каталог расходки, сервис.

    Каталог живёт в БД (ADR-0019). Пустая база (свежий клон, новый сервер)
    наполняется seed-файлом при первом старте: иначе раздел открывается пустым,
    и расходку некуда вводить. Дальше источник правды — только БД.
    """
    config = load_needs_config()
    store = SqliteNeedsStore(context.db_path)
    catalog = SqliteCatalog(context.db_path)
    _seed_catalog_if_empty(catalog, config.catalog_path)
    return NeedsContainer(
        config=config,
        catalog=catalog,
        store=store,
        service=NeedsService(store, catalog),
    )


def _seed_catalog_if_empty(catalog: SqliteCatalog, seed_path) -> None:
    """Залить каталог из файла, если в БД его ещё нет (Q17: первый запуск)."""
    if not catalog.is_empty():
        return
    if not Path(seed_path).exists():
        logger.warning(
            "Каталог расходки пуст, а seed-файла %s нет — «Потребности» открылись "
            "без каталога",
            seed_path,
        )
        return
    data = parse_file(seed_path)
    counts = catalog.import_catalog(data, None, source=str(seed_path))
    logger.info(
        "Каталог расходки залит из %s: баз %s, точек %s, групп %s, позиций %s",
        seed_path, counts["bases"], counts["points"], counts["groups"], counts["items"],
    )


MODULE = Module(
    name="needs",
    schema=SCHEMA,
    build=build,
    router=router,
)
