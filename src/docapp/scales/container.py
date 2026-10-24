"""Контейнер модуля «Шкалы» (ADR-0017).

Своих хранилищ у модуля нет: он держит только каталог шкал, чтобы роутер брал
его через `container_of`, как у любого другого модуля. Ни БД, ни прогона, ни
конфигурации — состоянию здесь взяться неоткуда (ADR-0028).
"""

from __future__ import annotations

from dataclasses import dataclass

from docapp.scales.calc import Scale


@dataclass(frozen=True)
class ScalesContainer:
    """Единственное, чем владеет «Шкалы»: объявленный каталог шкал."""

    scales: tuple[Scale, ...]
