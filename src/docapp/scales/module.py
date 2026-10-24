"""Модуль «Шкалы» для реестра приложения (ADR-0017, ADR-0028).

Своей схемы у модуля нет и не появится: это калькулятор. Ни таблиц, ни
персональных данных, ни миграций — считать нечего хранить.
"""

from __future__ import annotations

from docapp.core.registry import AppContext, Module
from docapp.scales import calc
from docapp.scales.container import ScalesContainer
from docapp.scales.router import router


def build(context: AppContext) -> ScalesContainer:
    """Собрать контейнер: каталог шкал, объявленный в `calc.py`.

    `AppContext` не нужен — пути к БД у модуля нет. Параметр оставлен ради
    контракта `Module.build`.
    """
    return ScalesContainer(scales=calc.SCALES)


MODULE = Module(name="scales", build=build, router=router)
