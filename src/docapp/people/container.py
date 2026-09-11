"""Контейнер модуля `people` (ADR-0017).

Отдельно от `module.py`, чтобы другие модули могли импортировать тип контейнера,
не создавая цикла: `module.py` собирает контейнер, а он нужен роутерам соседей —
справочник сотрудников спрашивают «Потребности», «Дежурства», «Компендиум».
"""

from __future__ import annotations

from dataclasses import dataclass

from docapp.people.store import SqliteEmployeeStore


@dataclass(frozen=True)
class PeopleContainer:
    """Сотрудники отделения — единственный владелец их таблицы."""

    employees: SqliteEmployeeStore
