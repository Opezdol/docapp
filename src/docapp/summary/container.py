"""Контейнер модуля «Сводка» (ADR-0017).

Своих хранилищ у модуля нет вовсе: он держит интерфейсы соседей — записи
анестезий от модуля `records` и справочник сотрудников от модуля `people`.
"""

from __future__ import annotations

from dataclasses import dataclass

from docapp.summary.service import SummaryService


@dataclass(frozen=True)
class SummaryContainer:
    """Единственное, чем владеет «Сводка»: её сервис поверх чужих интерфейсов."""

    service: SummaryService
