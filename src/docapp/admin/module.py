"""Модуль `admin` в реестре приложения (ADR-0030, ADR-0017).

Панель стоит в `MODULES` после `people`: сотрудники берутся у владельца их
данных через контейнер, а не запросом к чужой таблице. Своей схемы у модуля
пока нет — таблица выключателя разделов появится шагом 1.4 ТЗ панели.
"""

from __future__ import annotations

from docapp.admin.container import AdminContainer
from docapp.admin.router import router
from docapp.admin.service import AdminService
from docapp.core.registry import AppContext, Module
from docapp.people.container import PeopleContainer


def build(context: AppContext) -> AdminContainer:
    """Собрать панель поверх сотрудников, объявленных в реестре раньше."""
    people = context.containers.get("people")
    if not isinstance(people, PeopleContainer):
        raise RuntimeError("Панели заведующего нужен модуль «people»")
    return AdminContainer(service=AdminService(people.employees))


MODULE = Module(name="admin", build=build, router=router)
