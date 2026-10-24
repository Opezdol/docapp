"""Контейнер модуля подписей (ADR-0017).

Один сервис — им владеет модуль, его и берёт роутер через `container_of`.
Хранилище наружу не выносится: соседям (раздел «Кровь») нужна подпись сотрудника,
а не таблица `signatures` — читать её они не должны.
"""

from __future__ import annotations

from dataclasses import dataclass

from docapp.signatures.service import SignatureService


@dataclass(frozen=True)
class SignaturesContainer:
    """Чем владеет модуль подписей: сервис загрузки и выдачи подписей."""

    service: SignatureService
