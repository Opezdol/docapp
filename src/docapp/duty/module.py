"""Модуль «Дежурства» для реестра приложения (ADR-0017).

Собирает контейнер (см. `container.py`) один раз при старте; роутер берёт его
через `registry.container_of(request, "duty", DutyContainer)`.
"""

from __future__ import annotations

from docapp.core.registry import Module
from docapp.duty.config import load_duty_config
from docapp.duty.container import DutyContainer
from docapp.duty.router import router
from docapp.duty.service import DutyService
from docapp.duty.store import SCHEMA, SqliteDutyStore


def build() -> DutyContainer:
    """Собрать контейнер: конфиг, БД отчётов, сервис.

    Инициализация дешёвая и без сети: создаётся SQLite-файл отчётов.
    """
    config = load_duty_config()
    config.db_path.parent.mkdir(parents=True, exist_ok=True)
    store = SqliteDutyStore(config.db_path)
    return DutyContainer(
        config=config,
        store=store,
        service=DutyService(store, config),
    )


MODULE = Module(
    name="duty",
    schema=SCHEMA,
    db_path=lambda: load_duty_config().db_path,
    build=build,
    router=router,
)
