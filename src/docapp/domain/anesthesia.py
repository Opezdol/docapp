"""Запись о проведённой платной анестезии."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta


@dataclass(frozen=True)
class Anesthesia:
    date: date
    patient_name: str
    history_number: str
    doctor_id: int
    nurse_id: int
    created_at: datetime
    id: int | None = None

    def __post_init__(self) -> None:
        if not self.patient_name.strip():
            raise ValueError("patient_name не может быть пустым")
        if not self.history_number.strip():
            raise ValueError("history_number не может быть пустой")
        if self.doctor_id == self.nurse_id:
            raise ValueError("врач и медсестра должны быть разными сотрудниками")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() != timedelta(0):
            raise ValueError("created_at должен быть в UTC")
