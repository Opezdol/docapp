"""Сервис «Распределения»: считает поданные анестезии и подписывает их именами.

Модуль своей таблицы не имеет: записи он берёт у владельца — модуля `records`
(`AnesthesiaService.aggregate`), фамилии — у владельца справочника, модуля
`people`. Прямой запрос к таблице `anesthesia` здесь был бы признаком того, что
каркас не сработал (ADR-0017, ТЗ-каркас §10.3).

Права: раздел целиком принадлежит заведующему (`distribution.manage`,
ADR-0024), поэтому «только свои» здесь не считаются — счёт идёт по всем записям
отделения, а фильтр по сотруднику тут не нужен.
"""

from __future__ import annotations

from typing import Protocol

from docapp.core import period
from docapp.distribution.spread import Spread
from docapp.distribution.spread import spread as spread_vedomost
from docapp.distribution.vedomost import parse
from docapp.people.store import SqliteEmployeeStore

#: Разрезы счёта. Модуль тут не при чём: это группировка одного набора данных.
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
    """То, что «Распределению» нужно от модуля `records` (ADR-0017).

    Именно интерфейс, а не класс: распределению важно, что записи можно посчитать
    за период и что у каждой есть имя пациента и пара «врач + сестра», а как это
    устроено внутри — дело владельца данных. По той же причине модуль проверяется
    подстановкой чужого объекта с нужными методами.
    """

    def aggregate(
        self,
        from_date: str,
        to_date: str,
        by: str = BY_DOCTOR,
    ) -> dict:
        """Считать записи за период в выбранном разрезе."""
        ...

    def list_range(self, from_date: str, to_date: str) -> list:
        """Все записи за период: имя пациента, врач, сестра."""
        ...

    def mark_distributed(self, from_date: str, to_date: str, anesthesia_ids) -> dict:
        """Пересчитать метки «учтена» за период: снять прежние, поставить новые."""
        ...


class DistributionService:
    """Счёт поданных анестезий за период — через интерфейсы соседних модулей."""

    def __init__(self, records: RecordsInterface, employees: SqliteEmployeeStore) -> None:
        self._records = records
        self._employees = employees

    def aggregate(
        self,
        from_date: str,
        to_date: str,
        by: str = BY_DOCTOR,
    ) -> dict:
        """Счёт за период: все записи отделения, разрез `by`."""
        if by not in ALLOWED_BY:
            raise ValueError(f"Неизвестный разрез: {by!r}")

        result = self._records.aggregate(from_date, to_date, by)
        result["rows"] = [self._with_name(row, by) for row in result["rows"]]
        return result

    def _with_name(self, row: dict, by: str) -> dict:
        """Дописать фамилию, когда ключ — сотрудник."""
        if by not in PERSON_KEYS:
            return row
        employee = self._employees.get_by_id(int(row["key"]))
        return {**row, "name": employee.full_name if employee else f"#{row['key']}"}

    # ── разноска ведомости (ADR-0024) ─────────────────────────────────

    def spread(self, month: str, source: bytes) -> Spread:
        """Разнести ведомость за месяц по поданным анестезиям.

        Ничего не сохраняет: ведомость не хранится, файл пришёл — файл и ушёл
        (ADR-0024). Поиск идёт строго внутри месяца по дате подачи записи.
        """
        first, last = period.month_bounds(month)
        records = self._records.list_range(first.isoformat(), last.isoformat())
        return spread_vedomost(month, parse(source), records, self._employees)

    def mark(self, month: str, result: Spread) -> dict:
        """Пересчитать метки «учтена» за месяц — через интерфейс `records`.

        Метку получают записи, чья пара попала в ведомость; неоднозначные и
        ненайденные — нет: «учтена» значит «за неё в этой ведомости начислены
        деньги» (ADR-0024). Прежние метки месяца снимаются, поэтому повторный
        прогон не накапливает устаревшие (ТЗ, шаг 4).

        Вызывается после сборки файла: распределение учтено тогда, когда
        заведующий его получил. Возвращает `{"cleared": снято, "marked": поставлено}`.
        """
        first, last = period.month_bounds(month)
        return self._records.mark_distributed(
            first.isoformat(), last.isoformat(), result.matched_ids
        )

    def sources(self) -> dict:
        """Чем живёт модуль: для страницы и для проверки, что своих таблиц нет."""
        return {
            "records": type(self._records).__name__,
            "employees": type(self._employees).__name__,
            "own_tables": (),
        }
