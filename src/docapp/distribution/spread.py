"""Разноска ведомости: поиск врача и сестры по поданным анестезиям.

Правила (ADR-0024, `docs/ТЗ-распределение.md`):

- месяц задаёт заведующий, и поиск идёт строго внутри него;
- пациент ведомости ищется по фамилии **и инициалам**: совпадения одной фамилии
  мало — это финансовый документ, и «Иванов А.» не должен получить деньги за
  «Иванова П.»;
- найденная пара «врач + сестра» проставляется **во все строки этого пациента**
  (одна заявка врача закрывает и осмотр, и анестезию);
- не нашли — пусто, а если фамилия в месяце есть, но инициалы расходятся или
  подходящих записей несколько — «Внимание» и пара не ставится: угадывать
  нельзя, это деньги.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable, Sequence

from docapp.distribution.vedomost import Row, Vedomost
from docapp.domain.anesthesia import Anesthesia
from docapp.domain.employee import Employee


def normalize(value: str) -> str:
    """Регистр, ё → е, прочь всё, кроме букв и пробелов."""
    text = str(value or "").strip().lower().replace("ё", "е")
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def person_key(fio: str) -> tuple[str, str]:
    """ФИО → (фамилия, инициалы).

    «ФАМИЛИЯ И.О.» из ведомости и «Фамилия Имя Отчество» из записи дают один
    ключ: фамилия — первое слово, инициалы — первые буквы второго и третьего.
    """
    parts = normalize(fio).split()
    if not parts:
        return "", ""
    initials = "".join(part[:1] for part in parts[1:3])
    return parts[0], initials


def _initials_close(needed: str, found: str) -> bool:
    """Инициалы совпадают — на общем префиксе.

    В ведомости всегда «Фамилия И.О.», а врач мог ввести только имя: «пс» и «п» —
    это одно лицо (инициалы просто короче), «пс» и «пи» — разные люди. Пустые
    инициалы (в ведомости или в записи нечего сравнивать) совпадением не считаем:
    решает вызывающий — он смотрит на число оставшихся записей.
    """
    if not needed or not found:
        return False
    return needed[: len(found)] == found or found[: len(needed)] == needed


@dataclass(frozen=True)
class Match:
    """Итог поиска пациента в поданных анестезиях."""

    record: Anesthesia | None = None
    ambiguous: bool = False


def match_patients(
    patients: Iterable[str], records: Sequence[Anesthesia]
) -> dict[str, Match]:
    """Для каждого пациента ведомости — его запись, «Внимание» или пусто.

    Пара ставится, только если совпали **и фамилия, и инициалы** и такая запись
    одна. Фамилия совпала, а инициалы нет — значит, в ведомости другой человек
    (или врач ошибся в ФИО): денег не начисляем и помечаем строку.
    """
    by_surname: dict[str, list[Anesthesia]] = defaultdict(list)
    for record in records:
        by_surname[person_key(record.patient_name)[0]].append(record)

    result: dict[str, Match] = {}
    for patient in patients:
        surname, initials = person_key(patient)
        candidates = by_surname.get(surname, [])
        if not candidates:
            result[normalize(patient)] = Match()          # такого пациента нет
            continue
        if not initials:
            # В ведомости нет инициалов: сравнивать нечего — годится только
            # единственная запись с этой фамилией, иначе разбирать руками.
            result[normalize(patient)] = (
                Match(record=candidates[0]) if len(candidates) == 1 else Match(ambiguous=True)
            )
            continue
        close = [
            record for record in candidates
            if _initials_close(initials, person_key(record.patient_name)[1])
        ]
        if len(close) == 1:
            result[normalize(patient)] = Match(record=close[0])
        else:
            # Либо несколько подходящих записей, либо фамилия есть, а инициалы
            # расходятся — в обоих случаях пара не ставится.
            result[normalize(patient)] = Match(ambiguous=True)
    return result


@dataclass(frozen=True)
class SpreadRow:
    """Строка ведомости с проставленной парой."""

    source_row: int
    service_date: date | None
    patient: str
    service: str
    doctor: Employee | None
    nurse: Employee | None
    doctor_amount: Decimal
    nurse_amount: Decimal
    attention: bool


@dataclass(frozen=True)
class Person:
    """Сотрудник и сумма, которая ему причитается по ведомости."""

    employee: Employee
    amount: Decimal


@dataclass(frozen=True)
class Spread:
    """Результат разноски: построчно, итоги по людям и счётчики."""

    month: str
    source_sheet: str
    rows: tuple[SpreadRow, ...]
    doctors: tuple[Person, ...]
    nurses: tuple[Person, ...]
    #: id записей, попавших в ведомость: их метит «учтена» владелец записей.
    matched_ids: tuple[int, ...] = ()

    @property
    def assigned_rows(self) -> int:
        return sum(1 for row in self.rows if row.doctor and row.nurse)

    @property
    def attention_rows(self) -> int:
        return sum(1 for row in self.rows if row.attention)

    @property
    def unmatched_rows(self) -> int:
        return sum(1 for row in self.rows if not row.attention and not row.doctor)

    def without_buh_id(self) -> tuple[Employee, ...]:
        """Кто попал в итоги, но без ID в бухгалтерии — бухгалтерия споткнётся."""
        seen: dict[int, Employee] = {}
        for person in (*self.doctors, *self.nurses):
            if not (person.employee.buh_id or "").strip():
                seen.setdefault(person.employee.id or 0, person.employee)
        return tuple(seen.values())


def spread(month: str, vedomost: Vedomost, records: Sequence[Anesthesia],
           employees) -> Spread:
    """Разнести ведомость по поданным за месяц анестезиям.

    `employees` — справочник сотрудников (`people`): по id записи отдаёт ФИО и
    номер в бухгалтерии.
    """
    matches = match_patients((row.patient for row in vedomost.rows), records)

    rows: list[SpreadRow] = []
    by_doctor: dict[int, Decimal] = defaultdict(Decimal)
    by_nurse: dict[int, Decimal] = defaultdict(Decimal)
    people: dict[int, Employee] = {}

    matched: dict[int, None] = {}       # id записей, попавших в ведомость, по порядку
    for row in vedomost.rows:
        match = matches[normalize(row.patient)]
        doctor = employee(employees, match.record.doctor_id) if match.record else None
        nurse = employee(employees, match.record.nurse_id) if match.record else None
        if match.record is not None and match.record.id is not None:
            matched[match.record.id] = None     # одну запись метим один раз
        if doctor is not None:
            by_doctor[_id(doctor)] += row.doctor_amount
            people[_id(doctor)] = doctor
        if nurse is not None:
            by_nurse[_id(nurse)] += row.nurse_amount
            people[_id(nurse)] = nurse
        rows.append(
            SpreadRow(
                source_row=row.number,
                service_date=row.service_date,
                patient=row.patient,
                service=row.service,
                doctor=doctor,
                nurse=nurse,
                doctor_amount=row.doctor_amount,
                nurse_amount=row.nurse_amount,
                attention=match.ambiguous,
            )
        )

    def totals(amounts: dict[int, Decimal]) -> tuple[Person, ...]:
        persons = [Person(employee=people[key], amount=amount) for key, amount in amounts.items()]
        return tuple(sorted(persons, key=lambda person: (-person.amount, person.employee.full_name)))

    return Spread(
        month=month,
        source_sheet=vedomost.sheet_name,
        rows=tuple(rows),
        doctors=totals(by_doctor),
        nurses=totals(by_nurse),
        matched_ids=tuple(matched),
    )


def employee(employees, employee_id: int | None) -> Employee | None:
    """Сотрудник из справочника; нет такого — None (запись не подпишется)."""
    if not employee_id:
        return None
    return employees.get_by_id(int(employee_id))


def _id(employee: Employee) -> int:
    """id сотрудника: у пришедших из справочника он всегда есть."""
    if employee.id is None:
        raise RuntimeError("У сотрудника из справочника нет id")
    return employee.id
