"""Контейнер модуля `records` (ADR-0017).

Хранилища записей и сервис ввода анестезий. Тип контейнера импортирует роутер и
«Сводка»: она берёт у него интерфейс записей, а не читает таблицу `anesthesia`.
"""

from __future__ import annotations

from dataclasses import dataclass

from docapp.records.service import AnesthesiaService
from docapp.records.store import SqliteActiveNurseStore, SqliteAnesthesiaStore


@dataclass(frozen=True)
class RecordsContainer:
    """Всё, чем владеет модуль записей: записи, «активная сестра», сервис."""

    anesthesia: SqliteAnesthesiaStore
    active_nurse: SqliteActiveNurseStore
    service: AnesthesiaService
