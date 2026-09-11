"""Модуль `people` для реестра приложения (ADR-0017).

Владеет справочником сотрудников: их таблица и хранилище. Своей HTTP-части у
модуля нет — сотрудников заводят командой `docapp user`/`nurse`, а читают их через
контейнер этого модуля (`container_of(request, "people", PeopleContainer)`).

Модуль объявлен в реестре раньше остальных: соседи берут справочник при сборке,
значит он должен быть собран первым.
"""

from __future__ import annotations

from docapp.core.registry import AppContext, Module
from docapp.people.container import PeopleContainer
from docapp.people.store import SCHEMA, SqliteEmployeeStore


def build(context: AppContext) -> PeopleContainer:
    """Собрать контейнер: хранилище сотрудников в общей БД (ADR-0016)."""
    return PeopleContainer(employees=SqliteEmployeeStore(context.db_path))


MODULE = Module(name="people", schema=SCHEMA, build=build)
