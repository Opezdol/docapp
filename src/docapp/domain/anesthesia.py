"""Запись о проведённой платной анестезии.

Поля сведены к минимуму (152-ФЗ, задача «минимизация данных»): без номера
истории болезни и без даты процедуры. `date` — дата подачи сведений
(заполняется сервисом автоматически при создании), точный момент — в
`created_at` (UTC).

`accrued_at` — момент, когда запись попала в распределение месяца («учтена»,
ADR-0024). Владелец записи — модуль `records`: ставит и снимает метку только он,
по вызову модуля «Распределение» (правило шва, ADR-0017).
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta


@dataclass(frozen=True)
class Anesthesia:
    date: date
    patient_name: str
    doctor_id: int
    nurse_id: int
    created_at: datetime
    id: int | None = None
    #: Момент, когда запись попала в распределение месяца (ADR-0024). None — не учтена.
    accrued_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.patient_name.strip():
            raise ValueError("patient_name не может быть пустым")
        if self.doctor_id == self.nurse_id:
            raise ValueError("врач и медсестра должны быть разными сотрудниками")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() != timedelta(0):
            raise ValueError("created_at должен быть в UTC")
        if self.accrued_at is not None and self.accrued_at.utcoffset() != timedelta(0):
            raise ValueError("accrued_at должен быть в UTC")
