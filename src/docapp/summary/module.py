"""Модуль «Сводка» для реестра приложения (ADR-0017).

Своей схемы у модуля нет — и это не мелочь: он доказывает, что каркас модулей
сработал. Записи анестезий «Сводка» берёт через интерфейс модуля `records`,
фамилии — через справочник модуля `people`. Если однажды понадобится читать
таблицу `anesthesia` напрямую, это будет означать, что каркас не сработал, и
править надо каркас, а не обходить его (ТЗ-каркас §10.3).

Модуль объявлен в реестре последним: он зависит от обоих соседей.
"""

from __future__ import annotations

from docapp.core.registry import AppContext, Module
from docapp.people.container import PeopleContainer
from docapp.records.container import RecordsContainer
from docapp.summary.container import SummaryContainer
from docapp.summary.router import router
from docapp.summary.service import SummaryService


def build(context: AppContext) -> SummaryContainer:
    """Собрать сервис сводки поверх интерфейсов уже собранных модулей."""
    records = context.containers.get("records")
    people = context.containers.get("people")
    if not isinstance(records, RecordsContainer) or not isinstance(people, PeopleContainer):
        raise RuntimeError(
            "«Сводке» нужны модули «records» и «people»: она читает записи через "
            "их интерфейсы, а своей таблицы не имеет"
        )
    return SummaryContainer(
        service=SummaryService(records.service, people.employees)
    )


MODULE = Module(name="summary", build=build, router=router)
