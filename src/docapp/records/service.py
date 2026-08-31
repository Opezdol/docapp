"""Сервис записей: бизнес-правила ввода и правки анестезий.

Хранилище — тупой слой «запиши/удали», правила живут здесь:
- дата подачи проставляется автоматически (не вводится врачом);
- врач видит и правит только свои записи (ADR-5).

Медсестра передаётся явно из формы (поле выбора в форме анестезии);
«активная сестра» (ADR-4) осталась только как предвыбор селектора
при следующем входе и здесь не участвует.
"""

from datetime import date, datetime, timezone

from docapp.domain.anesthesia import Anesthesia


class NotFoundError(ValueError):
    """Запись не найдена."""


class NotOwnedError(ValueError):
    """Нельзя изменять чужую запись."""


class AnesthesiaService:
    """Бизнес-правила вокруг записей об анестезиях."""

    def __init__(self, anesthesia_store) -> None:
        self._anesthesia = anesthesia_store

    def create(
        self,
        doctor_id: int,
        nurse_id: int,
        patient_name: str,
    ) -> Anesthesia:
        """Создать запись: дата подачи — сегодня, момент — created_at (UTC)."""
        if not nurse_id:
            raise ValueError("Сначала выберите медсестру")

        anesthesia = Anesthesia(
            id=None,
            date=date.today(),
            patient_name=patient_name,
            doctor_id=doctor_id,
            nurse_id=nurse_id,
            created_at=datetime.now(timezone.utc),
        )
        return self._anesthesia.add(anesthesia)

    def list_mine(self, doctor_id: int) -> list[Anesthesia]:
        """Все записи врача, свежие сверху."""
        return self._anesthesia.list_by_doctor(doctor_id)

    def list_for_nurse(self, nurse_id: int) -> list[Anesthesia]:
        """Все записи, где участвовала медсестра, свежие сверху."""
        return self._anesthesia.list_by_nurse(nurse_id)

    def update(
        self,
        doctor_id: int,
        anesthesia_id: int,
        patient_name: str,
        nurse_id: int,
    ) -> Anesthesia:
        """Перезаписать свою запись. date и created_at не меняются."""
        existing = self._get_owned(doctor_id, anesthesia_id)
        if not nurse_id:
            raise ValueError("Сначала выберите медсестру")

        updated = Anesthesia(
            id=existing.id,
            date=existing.date,
            patient_name=patient_name,
            doctor_id=existing.doctor_id,
            nurse_id=nurse_id,
            created_at=existing.created_at,
        )
        self._anesthesia.update(updated)
        return updated

    def delete(self, doctor_id: int, anesthesia_id: int) -> None:
        """Удалить свою запись."""
        self._get_owned(doctor_id, anesthesia_id)
        self._anesthesia.delete(anesthesia_id)

    def _get_owned(self, doctor_id: int, anesthesia_id: int) -> Anesthesia:
        existing = self._anesthesia.get_by_id(anesthesia_id)
        if existing is None:
            raise NotFoundError("Запись не найдена")
        if existing.doctor_id != doctor_id:
            raise NotOwnedError("Нельзя изменять чужую запись")
        return existing
