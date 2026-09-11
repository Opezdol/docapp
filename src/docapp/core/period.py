"""Период: неделя «Потребностей» и смена «Дежурств» (ADR-0018).

Два периода приложения считались по местам: понедельник недели — в
`needs/service.py`, подписи недель — ещё в `needs/report.py` и
`needs/analytics.py`, окно смены 16:00 → 09:30 и минуты от начала дежурства —
в `duty/service.py` и `duty/report.py`. Здесь эта арифметика одна на всех.

Ничего, кроме арифметики: ни базы, ни прав, ни HTTP. Период описывается
границами (понедельник недели, окно смены), а как он называется в интерфейсе —
дело вызывающего.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone, tzinfo

#: Часовой пояс по умолчанию: московское время (отделение в Красноярском крае
#: живёт по нему же — как и в настройках «Дежурств»). Модуль передаёт свой.
DEFAULT_TZ: tzinfo = timezone(timedelta(hours=3), "MSK")

#: Начало суток дежурства. Окно смены: [16:00 даты, 09:30 следующего дня).
SHIFT_START = time(16, 0)
SHIFT_END = time(9, 30)

#: Дней в неделе — воскресенье закрывает неделю, начатую понедельником.
DAYS_IN_WEEK = 7

_DATE_FORMAT = "%d.%m.%Y"


def week_of(day: date | None = None) -> date:
    """Понедельник недели даты (по умолчанию — сегодняшней)."""
    day = day or date.today()
    return day - timedelta(days=day.weekday())


def week_start(day: date | None = None) -> str:
    """Понедельник недели как 'YYYY-MM-DD' — ключ недели во всех модулях."""
    return week_of(day).isoformat()


def week_bounds(week_start_: str) -> tuple[date, date]:
    """Границы недели по её понедельнику: (понедельник, воскресенье)."""
    monday = date.fromisoformat(week_start_)
    return monday, monday + timedelta(days=DAYS_IN_WEEK - 1)


def _mondays(first_week: str, last_week: str) -> tuple[date, date]:
    """Понедельники двух недель в хронологическом порядке."""
    first, last = date.fromisoformat(first_week), date.fromisoformat(last_week)
    if last < first:
        first, last = last, first
    return first, last


def week_range(first_week: str, last_week: str) -> tuple[date, date]:
    """Диапазон недель по их понедельникам: (первый понедельник, последний)."""
    return _mondays(first_week, last_week)


def label(week_start_: str) -> str:
    """Подпись недели: «10.08.2026 – 16.08.2026» (понедельник — воскресенье)."""
    start, end = week_bounds(week_start_)
    return f"{start.strftime(_DATE_FORMAT)} – {end.strftime(_DATE_FORMAT)}"


def month_bounds(month: str) -> tuple[date, date]:
    """Границы месяца «ГГГГ-ММ»: (первое число, последнее число).

    Месяц «Распределения»: заведующий выбирает его вручную, и поиск поданных
    анестезий идёт строго внутри этих границ (ADR-0024).
    """
    try:
        first = date.fromisoformat(f"{str(month).strip()}-01")
    except ValueError:
        raise ValueError(f"Неверный месяц {month!r}: нужно ГГГГ-ММ") from None
    next_month = (first + timedelta(days=31)).replace(day=1)
    return first, next_month - timedelta(days=1)


def range_label(first_week: str, last_week: str) -> str:
    """Подпись периода по неделям: «10.08.2026 – 24.08.2026» (их понедельники).

    Именно так подписывается период в отчётах и аналитике: границы — недели, а
    не дни внутри них. Одна неделя подписывается одной датой («10.08.2026»).
    """
    first, last = _mondays(first_week, last_week)
    if first == last:
        return first.strftime(_DATE_FORMAT)
    return f"{first.strftime(_DATE_FORMAT)} – {last.strftime(_DATE_FORMAT)}"


def parse_hhmm(hhmm: str) -> tuple[int, int]:
    """'HH:MM' → (часы, минуты); неверный формат или диапазон — ValueError."""
    parts = str(hhmm).strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"Неверный формат времени {hhmm!r}: нужно ЧЧ:ММ")
    try:
        hh, mm = int(parts[0]), int(parts[1])
    except ValueError:
        raise ValueError(f"Неверный формат времени {hhmm!r}: нужно ЧЧ:ММ")
    if not (0 <= hh < 24 and 0 <= mm < 60):
        raise ValueError(f"Неверное время {hhmm!r}")
    return hh, mm


def minutes_into_shift(hhmm: str, *, start: time = SHIFT_START) -> int:
    """'HH:MM' → минуты от начала дежурства (16:00 → 0, 08:00 → 960).

    Время до начала дежурства считается после полуночи: дежурство переходит
    через полночь, и 00:30 — это 8 часов 30 минут после 16:00.
    """
    hh, mm = parse_hhmm(hhmm)
    if hh >= start.hour:
        return (hh - start.hour) * 60 + mm
    return (hh + 24 - start.hour) * 60 + mm


@dataclass(frozen=True)
class ShiftWindow:
    """Окно смены: [начало 16:00 даты смены, конец 09:30 следующего дня).

    Границы настраиваемые (модуль передаёт свои из конфига), значения по
    умолчанию — рабочие значения отделения.
    """

    start: time = SHIFT_START
    end: time = SHIFT_END
    tz: tzinfo = DEFAULT_TZ

    def date_of(self, now: datetime | None = None) -> date | None:
        """Дата текущей смены (дата её начала) или None, если окно закрыто.

        - после начала (16:00–23:59) → сегодня;
        - до конца (00:00–09:29) → вчера, это та же смена;
        - между сменами (09:30–15:59) → None.
        """
        now = now or datetime.now(self.tz)
        moment = now.timetz().replace(tzinfo=None) if now.tzinfo else now.time()
        if moment >= self.start:
            return now.date()
        if moment < self.end:
            return now.date() - timedelta(days=1)
        return None

    def is_open(self, now: datetime | None = None) -> bool:
        """Открыто ли окно смены прямо сейчас."""
        return self.date_of(now) is not None

    def bounds(self, shift_date: date) -> tuple[datetime, datetime]:
        """Границы окна конкретной смены в часовом поясе окна."""
        start = datetime.combine(shift_date, self.start, tzinfo=self.tz)
        end = datetime.combine(shift_date + timedelta(days=1), self.end, tzinfo=self.tz)
        return start, end

    def open_for(self, shift_date: date, now: datetime | None = None) -> bool:
        """Открыто ли окно конкретной смены в момент now."""
        now = now or datetime.now(self.tz)
        start, end = self.bounds(shift_date)
        return start <= now < end
