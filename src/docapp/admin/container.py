"""Контейнер модуля `admin`: чем панель владеет (ADR-0030)."""

from __future__ import annotations

from dataclasses import dataclass

from docapp.admin.service import AdminService


@dataclass(frozen=True)
class AdminContainer:
    """Панель заведующего. Своих таблиц у неё пока нет — только сотрудники.

    Таблица состояния разделов появится шагом 1.4 ТЗ панели; сотрудники
    остаются данными модуля `people` (ADR-0017).
    """

    service: AdminService
