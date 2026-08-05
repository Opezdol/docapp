"""Сервис записей: бизнес-правила ввода и правки анестезий.

Хранилище — тупой слой «запиши/удали», правила живут здесь:
- сестра берётся из «активной сестры» врача (ADR-4);
- дата не может быть в будущем;
- врач видит и правит только свои записи (ADR-5).
"""

from datetime import date, datetime, timezone

from docapp.domain.anesthesia import Anesthesia


class ActiveNurseRequired(ValueError):
    """Врач не выбрал медсестру."""


class FutureDateError(ValueError):
    """Дата не может быть в будущем."""


class NotFoundError(ValueError):
    """Запись не найдена."""


class NotOwnedError(ValueError):
    """Нельзя изменять чужую запись."""


class AnesthesiaService:
    """Бизнес-правила вокруг записей об анестезиях."""

    def __init__(self, anesthesia_store, active_nurse_store) -> None:
        self._anesthesia = anesthesia_store
        self._active_nurse = active_nurse_store

    def create(
        self,
        doctor_id: int,
        procedure_date: date,
        patient_name: str,
        history_number: str,
    ) -> Anesthesia:
        """Создать запись для врача с его активной сестрой."""
        nurse_id = self._active_nurse.get_active_nurse(doctor_id)
        if nurse_id is None:
            raise ActiveNurseRequired("Сначала выберите медсестру")
        self._check_date(procedure_date)

        anesthesia = Anesthesia(
            id=None,
            date=procedure_date,
            patient_name=patient_name,
            history_number=history_number,
            doctor_id=doctor_id,
            nurse_id=nurse_id,
            created_at=datetime.now(timezone.utc),
        )
        return self._anesthesia.add(anesthesia)

    def list_mine(self, doctor_id: int) -> list[Anesthesia]:
        """Все записи врача, свежие сверху."""
        return self._anesthesia.list_by_doctor(doctor_id)

    def update(
        self,
        doctor_id: int,
        anesthesia_id: int,
        procedure_date: date,
        patient_name: str,
        history_number: str,
        nurse_id: int,
    ) -> Anesthesia:
        """Перезаписать свою запись. created_at и id не меняются."""
        existing = self._get_owned(doctor_id, anesthesia_id)
        self._check_date(procedure_date)

        updated = Anesthesia(
            id=existing.id,
            date=procedure_date,
            patient_name=patient_name,
            history_number=history_number,
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

    # ---------- внутренние проверки ----------

    @staticmethod
    def _check_date(procedure_date: date) -> None:
        if procedure_date > date.today():
            raise FutureDateError("Дата не может быть в будущем")

    def _get_owned(self, doctor_id: int, anesthesia_id: int) -> Anesthesia:
        existing = self._anesthesia.get_by_id(anesthesia_id)
        if existing is None:
            raise NotFoundError("Запись не найдена")
        if existing.doctor_id != doctor_id:
            raise NotOwnedError("Нельзя изменять чужую запись")
        return existing
