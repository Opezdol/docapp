"""Контейнер модуля `admin`: чем панель владеет (ADR-0030)."""

from __future__ import annotations

from dataclasses import dataclass

from docapp.admin.service import AdminService


@dataclass(frozen=True)
class AdminContainer:
    """Панель заведующего: сотрудники (данные `people`) и состояние разделов."""

    service: AdminService
