"""Модуль `records` для реестра приложения (ADR-0017).

Владеет записями об анестезиях и выбором сестры на смену. Здесь же HTTP-адаптер:
главная страница (ввод и список), правка и удаление записи, выбор сестры.

Справочник сотрудников модуль не хранит: он берёт его у владельца — модуля
`people` (`container_of(request, "people", PeopleContainer)`).
"""

from __future__ import annotations

from docapp.core.registry import AppContext, Module
from docapp.records.container import RecordsContainer
from docapp.records.router import router
from docapp.records.service import AnesthesiaService
from docapp.records.store import SCHEMA, SqliteActiveNurseStore, SqliteAnesthesiaStore


def build(context: AppContext) -> RecordsContainer:
    """Собрать контейнер: хранилища записей в общей БД (ADR-0016) и сервис."""
    anesthesia = SqliteAnesthesiaStore(context.db_path)
    active_nurse = SqliteActiveNurseStore(context.db_path)
    return RecordsContainer(
        anesthesia=anesthesia,
        active_nurse=active_nurse,
        service=AnesthesiaService(anesthesia),
    )


MODULE = Module(name="records", schema=SCHEMA, build=build, router=router)
