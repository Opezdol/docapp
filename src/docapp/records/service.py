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

    # ── сводка по поданным анестезиям (интерфейс для других модулей) ───

    def aggregate(
        self,
        from_date: str,
        to_date: str,
        by: str = "doctor",
        *,
        doctor_id: int | None = None,
        nurse_id: int | None = None,
    ) -> dict:
        """Считать поданные анестезии за период по одному разрезу.

        `by` — разрез сводки:
        - `doctor` / `nurse` — кто подал / с кем работали (ключ — id сотрудника,
          имя подставляет вызывающий: справочник принадлежит модулю `people`);
        - `month` / `day` — динамика по месяцам и дням (ключ — дата).

        Период — по дате подачи, границы включительно. `doctor_id`/`nurse_id`
        ограничивают выборку («только мои»): сам сервис прав не проверяет — это
        делает вызывающий, у которого есть роль пользователя.

        Это интерфейс модуля `records`: «Сводка» читает записи здесь, а не в
        таблице `anesthesia` (ADR-0017).
        """
        keys = {
            "doctor": lambda a: str(a.doctor_id),
            "nurse": lambda a: str(a.nurse_id),
            "month": lambda a: a.date.strftime("%Y-%m"),
            "day": lambda a: a.date.isoformat(),
        }
        if by not in keys:
            raise ValueError(f"Неизвестный разрез сводки: {by!r}")

        records = self._anesthesia.list_range(
            date.fromisoformat(from_date),
            date.fromisoformat(to_date),
            doctor_id=doctor_id,
            nurse_id=nurse_id,
        )
        key_of = keys[by]
        counts: dict[str, int] = {}
        for record in records:
            key = key_of(record)
            counts[key] = counts.get(key, 0) + 1

        # Динамика — по времени (старое сверху), люди — по количеству (кто больше).
        if by in ("month", "day"):
            rows = [{"key": key, "count": counts[key]} for key in sorted(counts)]
        else:
            rows = [
                {"key": key, "count": count}
                for key, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
            ]
        return {
            "from": from_date,
            "to": to_date,
            "by": by,
            "total": len(records),
            "rows": rows,
        }
