"""Контейнер модуля «Распределение» (ADR-0017).

Своих хранилищ у модуля нет вовсе: он держит интерфейсы соседей — записи
анестезий от модуля `records` и справочник сотрудников от модуля `people`.
"""

from __future__ import annotations

from dataclasses import dataclass

from docapp.distribution.service import DistributionService


@dataclass(frozen=True)
class DistributionContainer:
    """Единственное, чем владеет «Распределение»: сервис поверх чужих интерфейсов."""

    service: DistributionService
