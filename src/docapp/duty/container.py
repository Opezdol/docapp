"""Контейнер модуля «Дежурства» (ADR-0017).

Отдельно от `module.py`, чтобы роутер импортировал тип контейнера без цикла.
"""

from __future__ import annotations

from dataclasses import dataclass

from docapp.duty.config import DutyConfig
from docapp.duty.service import DutyService
from docapp.duty.store import SqliteDutyStore


@dataclass(frozen=True)
class DutyContainer:
    """Всё, чем владеет модуль: конфиг (в т.ч. часовой пояс смены) и сервис."""

    config: DutyConfig
    store: SqliteDutyStore
    service: DutyService
