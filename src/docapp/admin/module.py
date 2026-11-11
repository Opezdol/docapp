"""Модуль `admin` в реестре приложения (ADR-0030, ADR-0017).

Панель стоит в `MODULES` после `people`: сотрудники берутся у владельца их
данных через контейнер, а не запросом к чужой таблице. Своя схема — одна
таблица состояния разделов (`admin_sections`).
"""

from __future__ import annotations

from docapp.admin.container import AdminContainer
from docapp.admin.router import router
from docapp.admin.service import AdminService
from docapp.admin.store import SCHEMA, SqliteSectionStore
from docapp.core.registry import AppContext, Module
from docapp.people.container import PeopleContainer


def build(context: AppContext) -> AdminContainer:
    """Собрать панель поверх сотрудников и собственной таблицы разделов."""
    people = context.containers.get("people")
    if not isinstance(people, PeopleContainer):
        raise RuntimeError("Панели заведующего нужен модуль «people»")
    return AdminContainer(
        service=AdminService(
            employees=people.employees,
            sections=SqliteSectionStore(context.db_path),
        )
    )


MODULE = Module(name="admin", schema=SCHEMA, build=build, router=router)
