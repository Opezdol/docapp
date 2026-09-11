"""Модуль «Дежурства» для реестра приложения (ADR-0017).

Собирает контейнер (см. `container.py`) один раз при старте; роутер берёт его
через `registry.container_of(request, "duty", DutyContainer)`.
"""

from __future__ import annotations

from docapp.core.registry import AppContext, Module
from docapp.duty.config import load_duty_config
from docapp.duty.container import DutyContainer
from docapp.duty.router import router
from docapp.duty.service import DutyService
from docapp.duty.store import SCHEMA, SqliteDutyStore


def build(context: AppContext) -> DutyContainer:
    """Собрать контейнер: конфиг смены и сервис отчётов.

    БД — общая для приложения (ADR-0016), путь приходит из AppContext.
    """
    config = load_duty_config()
    store = SqliteDutyStore(context.db_path)
    return DutyContainer(
        config=config,
        store=store,
        service=DutyService(store, config),
    )


MODULE = Module(
    name="duty",
    schema=SCHEMA,
    build=build,
    router=router,
)
