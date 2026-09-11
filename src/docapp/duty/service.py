"""Сервис «Дежурств»: окно смены, дата смены, сохранение/отправка, авто-закрытие.

Правила:
- вкладка доступна только в окне смены: 16:00 → 09:30 следующего дня;
- дата смены определяется автоматически — это дата, когда было 16:00
  (в 00:00–09:29 смена относится к вчерашней дате);
- операции вводятся в пределах рабочего времени 16:00–08:00;
- до отправки отчёт — черновик (редактируется);
- статусы: draft (черновик), sent (отправлен — правки возможны до закрытия),
  closed (закрыт — только чтение); таблица переходов — duty/statuses.py
  (общий механизм core/statuses, ADR-0018);
- закрывает смену заведующий (вручную) либо окно 09:30 (авто, лениво);
- операции при закрытии сохраняются.

Ошибки окна поднимаются как DutyClosed (роутер отвечает 409).
"""

from datetime import date, datetime

from docapp.core import period
from docapp.duty import statuses
from docapp.duty.config import OPERATION_WINDOW_MINUTES, WINDOW_END, WINDOW_START


class DutyClosed(ValueError):
    """Смена закрыта — ввод/правка недоступны (роутер отвечает 409)."""


class DutyService:
    """Бизнес-правила модуля `duty` («Дежурства»).

    store — SQLite-хранилище (SqliteDutyStore), config — DutyConfig (tz).
    Сервис ничего не знает про HTTP: окно и права — исключениями/значениями.
    """

    def __init__(self, store, config) -> None:
        self._store = store
        self._config = config
        # Окно смены — из общего механизма периода (core/period, ADR-0018);
        # границы и часовой пояс приходят из настроек модуля.
        self._window = period.ShiftWindow(
            start=WINDOW_START, end=WINDOW_END, tz=config.tz
        )

    def now(self) -> datetime:
        """Текущее время в часовом поясе смены."""
        return datetime.now(self._config.tz)

    # ── окно и дата смены ─────────────────────────────────────────────

    def shift_date(self, now: datetime | None = None) -> date | None:
        """Дата текущей смены (дата 16:00) или None, если окно закрыто.

        - 16:00–23:59 → сегодня;
        - 00:00–09:29 → вчера (та же смена);
        - 09:30–15:59 → None (между сменами, вкладка закрыта).
        """
        return self._window.date_of(now or self.now())

    def is_open(self, now: datetime | None = None) -> bool:
        """Открыто ли окно смены прямо сейчас."""
        return self._window.is_open(now or self.now())

    def window_open_for(self, shift_date: date, now: datetime | None = None) -> bool:
        """Открыто ли окно конкретной смены: [16:00 даты, 09:30 следующего дня)."""
        return self._window.open_for(shift_date, now or self.now())

    # ── валидация операций ────────────────────────────────────────────

    def _clean_operation(self, op: dict) -> dict:
        """Проверить и нормализовать одну операцию; ValueError при нарушении."""
        operation = str(op.get("operation") or "").strip()
        if not operation:
            raise ValueError("Название операции не может быть пустым")
        start = str(op.get("start_time") or "").strip()
        end = str(op.get("end_time") or "").strip()
        start_off = period.minutes_into_shift(start, start=WINDOW_START)
        end_off = period.minutes_into_shift(end, start=WINDOW_START)
        if not (0 <= start_off < OPERATION_WINDOW_MINUTES):
            raise ValueError(f"Время начала {start} вне окна дежурства (16:00–08:00)")
        if not (0 < end_off <= OPERATION_WINDOW_MINUTES):
            raise ValueError(f"Время конца {end} вне окна дежурства (16:00–08:00)")
        if start_off >= end_off:
            raise ValueError(f"Время начала {start} должно быть раньше конца {end}")
        return {"operation": operation, "start_time": start, "end_time": end}

    def _clean_operations(self, operations: list[dict]) -> list[dict]:
        return [self._clean_operation(op) for op in (operations or [])]

    # ── сценарии врача ────────────────────────────────────────────────

    def get_for_doctor(self, doctor_id: int, base: str) -> dict:
        """Состояние текущей смены врача: shift_date, is_open, report."""
        sd = self.shift_date()
        if sd is None:
            return {"shift_date": None, "is_open": False, "report": None}
        report = self._store.get_report(base, sd.isoformat(), doctor_id)
        return {"shift_date": sd.isoformat(), "is_open": True, "report": report}

    def save(self, doctor_id: int, base: str, operations: list[dict]) -> dict:
        """Сохранить черновик текущей смены; возвращает полный отчёт."""
        sd = self.shift_date()
        if sd is None:
            raise DutyClosed("Смена закрыта — ввод доступен с 16:00 до 09:30")
        existing = self._store.get_report(base, sd.isoformat(), doctor_id)
        # Статус решает таблица переходов (duty/statuses): из «закрыт» правки
        # невозможны. Право на ввод отчёта проверяет роутер — только врачу.
        if existing is not None and not statuses.REPORT.can(existing["status"], statuses.EDIT):
            raise DutyClosed("Отчёт закрыт — редактирование недоступно")
        cleaned = self._clean_operations(operations)
        self._store.save_report(
            base, sd.isoformat(), doctor_id, cleaned, status=statuses.DRAFT
        )
        saved = self._store.get_report(base, sd.isoformat(), doctor_id)
        assert saved is not None  # только что сохранён
        return saved

    def send(self, doctor_id: int, base: str) -> dict:
        """Отправить отчёт текущей смены (status → 'sent'); возвращает отчёт."""
        sd = self.shift_date()
        if sd is None:
            raise DutyClosed("Смена закрыта — ввод доступен с 16:00 до 09:30")
        report = self._store.get_report(base, sd.isoformat(), doctor_id)
        if report is None or not report["operations"]:
            raise ValueError("Отчёт пуст — нечего отправлять")
        if not statuses.REPORT.can(report["status"], statuses.SUBMIT):
            raise DutyClosed("Отчёт закрыт — отправка недоступна")
        if report["status"] == statuses.SENT:
            return report  # уже отправлен: повторная отправка ничего не меняет
        self._store.set_status(report["id"], statuses.SENT)
        sent = self._store.get_report(base, sd.isoformat(), doctor_id)
        assert sent is not None
        return sent

    # ── заведующий ────────────────────────────────────────────────────

    def finalize_stale(self) -> None:
        """Авто-закрыть незакрытые отчёты, чьё окно смены уже прошло (в 09:30).

        Лениво: вызывается при обращении к доске/выгрузке — на shared-хостинге
        нет планировщика, поэтому финализация «догоняет» при первом чтении.
        """
        now = self.now()
        for report in self._store.list_open():
            sd = date.fromisoformat(report["shift_date"])
            if not self.window_open_for(sd, now):
                self._store.set_status(report["id"], statuses.CLOSED)

    def close_report(self, report_id: int) -> None:
        """Закрыть один отчёт заведующим (status → 'closed'); операции сохраняются."""
        self._store.set_status(report_id, statuses.CLOSED)

    def close_shift(self, shift_date: str) -> None:
        """Закрыть все отчёты за смену (status → 'closed')."""
        self._store.close_shift(shift_date)

    def reopen_report(self, report_id: int) -> None:
        """Переоткрыть ошибочно закрытый отчёт: новый статус даёт таблица переходов."""
        reopened = statuses.REPORT.next_status(statuses.CLOSED, statuses.REOPEN)
        assert reopened is not None  # переход объявлен в таблице
        self._store.set_status(report_id, reopened)

    def board(self, from_date: str, to_date: str, base: str | None = None) -> list[dict]:
        """Отчёты за диапазон дат (черновики прошлых смен финализируются)."""
        self.finalize_stale()
        return self._store.list_reports(from_date, to_date, base)
