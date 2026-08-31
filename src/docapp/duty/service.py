"""Сервис «Дежурств»: окно смены, дата смены, сохранение/отправка, авто-закрытие.

Правила:
- вкладка доступна только в окне смены: 16:00 → 09:30 следующего дня;
- дата смены определяется автоматически — это дата, когда было 16:00
  (в 00:00–09:29 смена относится к вчерашней дате);
- операции вводятся в пределах рабочего времени 16:00–08:00;
- до отправки отчёт — черновик (редактируется), отправка или 09:30 (авто,
  лениво) финализируют его (status → 'sent').

Ошибки окна поднимаются как DutyClosed (роутер отвечает 409).
"""

from datetime import date, datetime, timedelta

from docapp.duty.config import OPERATION_WINDOW_MINUTES, WINDOW_END, WINDOW_START


class DutyClosed(ValueError):
    """Смена закрыта — ввод/правка недоступны (роутер отвечает 409)."""


def _parse_hhmm(hhmm: str) -> tuple[int, int]:
    """'HH:MM' → (часы, минуты); неверный формат/диапазон — ValueError."""
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


def _offset_minutes(hhmm: str) -> int:
    """'HH:MM' → минуты от начала дежурства 16:00 (16:00=0 … 08:00=960)."""
    hh, mm = _parse_hhmm(hhmm)
    if hh >= 16:  # 16..23 — вечер
        return (hh - 16) * 60 + mm
    return (hh + 8) * 60 + mm  # 00..08 — после полуночи


class DutyService:
    """Бизнес-правила подприложения «Дежурства».

    store — SQLite-хранилище (SqliteDutyStore), config — DutyConfig (tz).
    Сервис ничего не знает про HTTP: окно и права — исключениями/значениями.
    """

    def __init__(self, store, config) -> None:
        self._store = store
        self._config = config

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
        now = now or self.now()
        t = now.time()
        if t >= WINDOW_START:
            return now.date()
        if t < WINDOW_END:
            return now.date() - timedelta(days=1)
        return None

    def is_open(self, now: datetime | None = None) -> bool:
        """Открыто ли окно смены прямо сейчас."""
        return self.shift_date(now) is not None

    def window_open_for(self, shift_date: date, now: datetime | None = None) -> bool:
        """Открыто ли окно конкретной смены: [16:00 даты, 09:30 следующего дня)."""
        now = now or self.now()
        start = datetime.combine(shift_date, WINDOW_START, tzinfo=self._config.tz)
        end = datetime.combine(shift_date + timedelta(days=1), WINDOW_END, tzinfo=self._config.tz)
        return start <= now < end

    # ── валидация операций ────────────────────────────────────────────

    def _clean_operation(self, op: dict) -> dict:
        """Проверить и нормализовать одну операцию; ValueError при нарушении."""
        operation = str(op.get("operation") or "").strip()
        if not operation:
            raise ValueError("Название операции не может быть пустым")
        start = str(op.get("start_time") or "").strip()
        end = str(op.get("end_time") or "").strip()
        start_off = _offset_minutes(start)
        end_off = _offset_minutes(end)
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
        if existing is not None and existing["status"] == "sent":
            raise DutyClosed("Отчёт уже отправлен — редактирование недоступно")
        cleaned = self._clean_operations(operations)
        self._store.save_report(base, sd.isoformat(), doctor_id, cleaned, status="draft")
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
        if report["status"] == "sent":
            return report
        self._store.set_status(report["id"], "sent")
        sent = self._store.get_report(base, sd.isoformat(), doctor_id)
        assert sent is not None
        return sent

    # ── заведующий ────────────────────────────────────────────────────

    def finalize_stale(self) -> None:
        """Авто-закрыть черновики, чьё окно смены уже прошло (в 09:30).

        Лениво: вызывается при обращении к доске/выгрузке — на shared-хостинге
        нет планировщика, поэтому финализация «догоняет» при первом чтении.
        """
        now = self.now()
        for report in self._store.list_drafts():
            sd = date.fromisoformat(report["shift_date"])
            if not self.window_open_for(sd, now):
                self._store.set_status(report["id"], "sent")

    def board(self, from_date: str, to_date: str, base: str | None = None) -> list[dict]:
        """Отчёты за диапазон дат (черновики прошлых смен финализируются)."""
        self.finalize_stale()
        return self._store.list_reports(from_date, to_date, base)
