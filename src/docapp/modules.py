"""Реестр модулей приложения: что подключено и чем владеет (ADR-0017).

Единственное место, где перечислены модули приложения. Добавить раздел — добавить
сюда одну строку и файл `module.py` в его пакете.

Порядок значим: модуль может взять интерфейс соседа, объявленного раньше
(`context.containers`). Поэтому `people` (справочник сотрудников) идёт первым,
`records` (записи анестезий) — до «Сводки», которая их читает.

База данных одна на все модули (ADR-0016): путь приходит из AppContext, ни один
модуль его не ищет сам. Версия схемы ведётся на модуль — у каждого владельца своя.
"""

from __future__ import annotations

from docapp.core.registry import Module
from docapp.duty.module import MODULE as DUTY
from docapp.needs.module import MODULE as NEEDS
from docapp.people.module import MODULE as PEOPLE
from docapp.records.module import MODULE as RECORDS
from docapp.summary.module import MODULE as SUMMARY
from docapp.wiki.module import MODULE as COMPENDIUM

#: Модули приложения в порядке подключения (порядок = порядок сборки).
MODULES: tuple[Module, ...] = (PEOPLE, RECORDS, COMPENDIUM, NEEDS, DUTY, SUMMARY)
