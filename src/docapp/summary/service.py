"""Сервис «Сводки»: считает поданные анестезии и подписывает их именами.

Модуль своей таблицы не имеет: записи он берёт у владельца — модуля `records`
(`AnesthesiaService.aggregate`), фамилии — у владельца справочника, модуля
`people`. Прямой запрос к таблице `anesthesia` здесь был бы признаком того, что
каркас не сработал (ADR-0017, ТЗ-каркас §10.3).

Права: у кого есть `records.view_all` — видит всех; у кого только
`records.view_own` — видит свои (врач — поданные им, медсестра — где она
участвовала). Фильтр применяется здесь, потому что здесь есть и роль, и данные.
"""

from __future__ import annotations

from typing import Protocol

from docapp.core import access
from docapp.domain.employee import Employee
from docapp.records.service import AnesthesiaService
from docapp.people.store import SqliteEmployeeStore

#: Разрезы сводки. «Подприложение» тут не при чём: это группировка одного набора
#: данных, поэтому и права — от записей, а не свои.
BY_DOCTOR = "doctor"
BY_NURSE = "nurse"
BY_MONTH = "month"
BY_DAY = "day"
#: Разрезы, где ключ — сотрудник: к нему нужна фамилия из справочника.
PERSON_KEYS = (BY_DOCTOR, BY_NURSE)
ALLOWED_BY = (BY_DOCTOR, BY_NURSE, BY_MONTH, BY_DAY)

#: Русские подписи разрезов — для страницы и сообщений.
BY_LABELS = {
    BY_DOCTOR: "Врач",
    BY_NURSE: "Медсестра",
    BY_MONTH: "Месяц",
    BY_DAY: "День",
}


class RecordsInterface(Protocol):
    """То, что «Сводке» нужно от модуля `records` (ADR-0017).

    Именно интерфейс, а не класс: сводке важно, что записи можно посчитать за
    период, а как это устроено внутри — дело владельца данных. По той же причине
    сводка проверяется подстановкой чужого объекта с методом `aggregate`.
    """

    def aggregate(
        self,
        from_date: str,
        to_date: str,
        by: str = BY_DOCTOR,
        *,
        doctor_id: int | None = None,
        nurse_id: int | None = None,
    ) -> dict:
        """Считать записи за период в выбранном разрезе."""


class SummaryService:
    """Сводка по анестезиям за период — через интерфейсы соседних модулей."""

    def __init__(self, records: RecordsInterface, employees: SqliteEmployeeStore) -> None:
        self._records = records
        self._employees = employees

    def aggregate(
        self,
        user: Employee,
        from_date: str,
        to_date: str,
        by: str = BY_DOCTOR,
    ) -> dict:
        """Сводка за период с учётом прав: `view_all` — всех, иначе только свои."""
        if by not in ALLOWED_BY:
            raise ValueError(f"Неизвестный разрез сводки: {by!r}")

        mine_only = not access.has(user.role, access.RECORDS_VIEW_ALL)
        doctor_id = user.id if mine_only and self._is_doctor(user) else None
        nurse_id = user.id if mine_only and not self._is_doctor(user) else None

        result = self._records.aggregate(
            from_date,
            to_date,
            by,
            doctor_id=doctor_id,
            nurse_id=nurse_id,
        )
        result["scope"] = "own" if mine_only else "all"
        result["rows"] = [self._with_name(row, by) for row in result["rows"]]
        return result

    def _with_name(self, row: dict, by: str) -> dict:
        """Дописать фамилию, когда ключ сводки — сотрудник."""
        if by not in PERSON_KEYS:
            return row
        employee = self._employees.get_by_id(int(row["key"]))
        return {**row, "name": employee.full_name if employee else f"#{row['key']}"}

    @staticmethod
    def _is_doctor(user: Employee) -> bool:
        """Врач (и редактор — он тоже врач) подал записи сам; медсестра — нет."""
        return access.has(user.role, access.RECORDS_EDIT)

    def sources(self) -> dict:
        """Чем живёт модуль: для страницы и для проверки, что своих таблиц нет."""
        return {
            "records": type(self._records).__name__,
            "employees": type(self._employees).__name__,
            "own_tables": (),
        }
