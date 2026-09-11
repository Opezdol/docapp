"""Реестр модулей приложения: что подключено и чем владеет (ADR-0017).

Единственное место, где перечислены модули приложения. Добавить раздел — добавить
сюда одну строку и файл `module.py` в его пакете.

База данных одна на все модули (ADR-0016): путь приходит из AppContext, ни один
модуль его не ищет сам. Ядро (`docapp`) объявлено модулем только как владелец
схемы: своих HTTP-маршрутов у него пока нет (они живут в `web/app.py`).
"""

from __future__ import annotations

from docapp.core.registry import Module
from docapp.duty.module import MODULE as DUTY
from docapp.needs.module import MODULE as NEEDS
from docapp.storage.sqlite_store import SCHEMA as CORE_SCHEMA
from docapp.wiki.module import MODULE as COMPENDIUM

#: Ядро: сотрудники и анестезии (своей HTTP-части в этом модуле нет).
CORE = Module(name="docapp", schema=CORE_SCHEMA)

#: Модули приложения в порядке подключения.
MODULES: tuple[Module, ...] = (CORE, COMPENDIUM, NEEDS, DUTY)
