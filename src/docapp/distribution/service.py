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

from decimal import Decimal
from typing import Protocol

from docapp.core import period
from docapp.distribution import xlsx
from docapp.distribution.run import Run, RunStore, human, now
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

    def __init__(
        self,
        records: RecordsInterface,
        employees: SqliteEmployeeStore,
        runs: RunStore,
    ) -> None:
        self._records = records
        self._employees = employees
        self._runs = runs

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

    # ── разноска ведомости (ADR-0024, ADR-0027) ───────────────────────

    def spread(self, month: str, source: bytes) -> Spread:
        """Разнести ведомость за месяц по поданным анестезиям.

        Чистая операция: ни меток, ни файлов. Поиск идёт строго внутри месяца
        по дате подачи записи (ADR-0024), ведомость не хранится.
        """
        first, last = period.month_bounds(month)
        records = self._records.list_range(first.isoformat(), last.isoformat())
        return spread_vedomost(month, parse(source), records, self._employees)

    def check(self, month: str, source: bytes, source_name: str = "") -> Run:
        """Прогон: разнести ведомость, собрать файл результата и сохранить его.

        Меток не ставит — месяц закрывает отдельное действие `publish`
        (ADR-0027). Сохранённый прогон — это то, что заведующий видит на
        странице, скачивает кнопкой и публикует, не загружая файл вторично.
        """
        result = self.spread(month, source)
        payload = xlsx.build(source, result)
        run = Run(
            month=month,
            created_at=now(),
            source_name=source_name,
            matched_ids=result.matched_ids,
            counters=_counters(result),
            tables=_tables(result),
        )
        self._runs.save(run, payload)
        return run

    def current_run(self) -> Run | None:
        """Последний прогон раздела или None, если его ещё не было."""
        return self._runs.load()

    def download(self) -> tuple[bytes, str]:
        """Файл результата последнего прогона и имя для скачивания."""
        run = self._require_run()
        return self._runs.payload(), xlsx.filename(run.month)

    def publish(self) -> tuple[dict, Run]:
        """Опубликовать прогон: поставить метки «учтена» — и только здесь.

        Метку ставит владелец записей по id записей прогона; прежние метки
        месяца снимаются, поэтому повторная публикация не накапливает
        устаревшие. Возвращает `({"cleared": снято, "marked": поставлено}, run)`.
        """
        run = self._require_run()
        marks = self.mark(run.month, run.matched_ids)
        return marks, self._runs.published(run, now())

    def mark(self, month: str, ids) -> dict:
        """Пересчитать метки «учтена» за месяц — через интерфейс `records`.

        Метку получают записи, чья пара попала в ведомость; неоднозначные и
        ненайденные — нет: «учтена» значит «за неё в этой ведомости начислены
        деньги» (ADR-0024). Вызывается только из `publish`.
        """
        first, last = period.month_bounds(month)
        return self._records.mark_distributed(
            first.isoformat(), last.isoformat(), ids
        )

    def _require_run(self) -> Run:
        """Прогон обязателен: без него нечего ни скачивать, ни публиковать."""
        run = self._runs.load()
        if run is None:
            raise ValueError(
                "Прогона нет: сначала проверьте ведомость — выберите месяц "
                "и загрузите файл"
            )
        return run

    def sources(self) -> dict:
        """Чем живёт модуль: для страницы и для проверки, что своих таблиц нет."""
        return {
            "records": type(self._records).__name__,
            "employees": type(self._employees).__name__,
            "runs": type(self._runs).__name__,
            "own_tables": (),
        }


# ── прогон для страницы: счётчики и таблицы ───────────────────────────


def _counters(spread: Spread) -> dict[str, int]:
    """Счётчики прогона: сколько строк, сколько разнеслось и сколько записей было.

    «Записей за месяц» — ключ к разгадке пустого прогона: если их нет, все
    строки остались без пары не из-за ошибки сопоставления, а потому что
    отделение ещё не подало анестезии через приложение.
    """
    return {
        "rows": len(spread.rows),
        "assigned": spread.assigned_rows,
        "attention": spread.attention_rows,
        "unmatched": spread.unmatched_rows,
        "records": spread.records_count,
    }


def _tables(spread: Spread) -> dict[str, list]:
    """Таблицы прогона для страницы: люди, строки «Внимание», кто без ID."""
    def person(item) -> dict:
        return {
            "name": item.employee.full_name,
            "buh_id": item.employee.buh_id or "",
            "amount": _money(item.amount),
        }

    return {
        "doctors": [person(item) for item in spread.doctors],
        "nurses": [person(item) for item in spread.nurses],
        "attention": [
            {"row": row.source_row, "patient": row.patient, "service": row.service}
            for row in spread.rows
            if row.attention
        ],
        "without_buh_id": [employee.full_name for employee in spread.without_buh_id()],
    }


def _money(amount: Decimal) -> str:
    """Сумма для страницы: «521 458,63 ₽» — разделители как принято по-русски."""
    text = f"{amount:,.2f}".replace(",", "\u00a0").replace(".", ",")
    return f"{text} ₽"
