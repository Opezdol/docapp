"""Тесты общего механизма периода и статусов (ADR-0018, шаг 5).

Три вещи сразу: арифметика недели, окно смены 16:00 → 09:30 и таблицы переходов
статусов у «Потребностей» и «Дежурств». Здесь же проверяется, что права на
переход берутся из `core/access` — своего списка ролей у механизма нет.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from docapp.core import access, period
from docapp.core.statuses import CLOSED, DRAFT, NONE, OPEN, SENT, Rule, Transitions
from docapp.domain.employee import DOCTOR, EDITOR, HEAD, HEAD_NURSE, NURSE
from docapp.duty import statuses as duty_statuses
from docapp.needs import statuses as needs_statuses

UTC = timezone.utc


def aware(y, m, d, hour, minute=0):
    """Момент времени в UTC — так тесты не зависят от часового пояса машины."""
    return datetime(y, m, d, hour, minute, tzinfo=UTC)


# ── неделя ────────────────────────────────────────────────────────────


class TestWeek:
    def test_current_week_starts_on_monday(self):
        today = date.today()
        expected = (today - timedelta(days=today.weekday())).isoformat()
        assert period.week_start() == expected

    def test_week_of_any_day(self):
        """Четверг, сам понедельник и воскресенье дают один и тот же понедельник."""
        assert period.week_start(date(2026, 8, 13)) == "2026-08-10"  # четверг
        assert period.week_start(date(2026, 8, 10)) == "2026-08-10"  # понедельник
        assert period.week_start(date(2026, 8, 16)) == "2026-08-10"  # воскресенье
        assert period.week_start(date(2026, 8, 17)) == "2026-08-17"  # следующий

    def test_week_bounds(self):
        monday, sunday = period.week_bounds("2026-08-10")
        assert monday == date(2026, 8, 10)
        assert sunday == date(2026, 8, 16)
        assert sunday - monday == timedelta(days=6)

    def test_week_label_is_monday_to_sunday(self):
        assert period.label("2026-08-10") == "10.08.2026 – 16.08.2026"

    def test_range_label_uses_mondays(self):
        """Период подписывается понедельниками недель, одна неделя — одной датой."""
        assert period.range_label("2026-08-10", "2026-08-24") == "10.08.2026 – 24.08.2026"
        assert period.range_label("2026-08-10", "2026-08-10") == "10.08.2026"
        assert period.range_label("2026-08-24", "2026-08-10") == "10.08.2026 – 24.08.2026"

    def test_week_range_orders_weeks(self):
        assert period.week_range("2026-08-24", "2026-08-10") == (
            date(2026, 8, 10),
            date(2026, 8, 24),
        )


# ── время дежурства ───────────────────────────────────────────────────


class TestShiftTime:
    @pytest.mark.parametrize(
        "hhmm,expected",
        [("16:00", 0), ("23:30", 450), ("00:00", 480), ("08:00", 960)],
    )
    def test_minutes_into_shift(self, hhmm, expected):
        assert period.minutes_into_shift(hhmm) == expected

    def test_minutes_into_shift_with_custom_start(self):
        """Начало дежурства настраивается: 20:00 → 0, 08:00 → 720."""
        from datetime import time

        assert period.minutes_into_shift("20:00", start=time(20, 0)) == 0
        assert period.minutes_into_shift("08:00", start=time(20, 0)) == 720

    @pytest.mark.parametrize("bad", ["9", "09:60", "24:00", "утро", "", "9:0:0"])
    def test_parse_hhmm_rejects_garbage(self, bad):
        with pytest.raises(ValueError):
            period.parse_hhmm(bad)

    def test_parse_hhmm_accepts_zero_padded(self):
        assert period.parse_hhmm("06:05") == (6, 5)


class TestShiftWindow:
    """Окно смены: [16:00 даты смены, 09:30 следующего дня)."""

    window = period.ShiftWindow(tz=UTC)

    def test_shift_date_evening_is_today(self):
        assert self.window.date_of(aware(2026, 8, 31, 18, 0)) == date(2026, 8, 31)

    def test_shift_date_at_start(self):
        assert self.window.date_of(aware(2026, 8, 31, 16, 0)) == date(2026, 8, 31)

    def test_shift_date_after_midnight_is_yesterday(self):
        assert self.window.date_of(aware(2026, 9, 1, 0, 0)) == date(2026, 8, 31)
        assert self.window.date_of(aware(2026, 9, 1, 9, 29)) == date(2026, 8, 31)

    def test_shift_date_between_shifts_is_none(self):
        assert self.window.date_of(aware(2026, 9, 1, 9, 30)) is None
        assert self.window.date_of(aware(2026, 9, 1, 15, 59)) is None

    def test_is_open_follows_shift_date(self):
        assert self.window.is_open(aware(2026, 8, 31, 20, 0)) is True
        assert self.window.is_open(aware(2026, 9, 1, 12, 0)) is False

    def test_open_for_past_shift(self):
        """Окно прошлой смены закрыто, даже если сейчас идёт следующая."""
        assert self.window.open_for(date(2026, 8, 31), aware(2026, 9, 1, 8, 0)) is True
        assert self.window.open_for(date(2026, 8, 31), aware(2026, 9, 1, 9, 30)) is False
        assert self.window.open_for(date(2026, 8, 30), aware(2026, 9, 1, 8, 0)) is False

    def test_bounds_are_timezone_aware(self):
        start, end = self.window.bounds(date(2026, 8, 31))
        assert start == aware(2026, 8, 31, 16, 0)
        assert end == aware(2026, 9, 1, 9, 30)
        assert start.tzinfo is not None and end.tzinfo is not None

    def test_window_bounds_are_configurable(self):
        """Модуль подставляет свои границы — механизм их не навязывает."""
        from datetime import time

        window = period.ShiftWindow(start=time(20, 0), end=time(6, 0), tz=UTC)
        assert window.date_of(aware(2026, 8, 31, 22, 0)) == date(2026, 8, 31)
        assert window.date_of(aware(2026, 9, 1, 2, 0)) == date(2026, 8, 31)
        assert window.date_of(aware(2026, 9, 1, 6, 30)) is None


# ── таблица переходов сама по себе ────────────────────────────────────


class TestTransitionsMechanics:
    def test_status_constants_are_the_api_contract(self):
        """Те же строки уходят в интерфейс — переименование сломало бы JS."""
        assert (DRAFT, SENT, CLOSED, NONE, OPEN) == ("draft", "sent", "closed", "none", "open")

    def test_unknown_action_is_refused(self):
        table = Transitions((Rule("edit", (DRAFT,), SENT),))
        assert table.can(DRAFT, "нет-такого-действия") is False
        assert table.next_status(DRAFT, "нет-такого-действия") is None

    def test_status_outside_rule_is_refused(self):
        table = Transitions((Rule("edit", (DRAFT,), SENT),))
        assert table.can(SENT, "edit") is False

    def test_duplicate_action_is_an_error(self):
        with pytest.raises(ValueError):
            Transitions((Rule("edit", (DRAFT,), SENT), Rule("edit", (SENT,), DRAFT)))

    def test_role_none_checks_status_only(self):
        table = Transitions((Rule("edit", (DRAFT,), SENT, (access.NEEDS_MANAGE,)),))
        assert table.can(DRAFT, "edit") is True
        assert table.can(DRAFT, "edit", NURSE) is False

    def test_allowed_actions(self):
        table = Transitions(
            (Rule("edit", (DRAFT,), DRAFT), Rule("submit", (DRAFT,), SENT))
        )
        assert table.allowed_actions(DRAFT) == ("edit", "submit")
        assert table.allowed_actions(SENT) == ()

    def test_next_status_returns_target(self):
        table = Transitions((Rule("reopen", (CLOSED,), SENT, (access.DUTY_MANAGE,)),))
        assert table.next_status(CLOSED, "reopen", HEAD) == SENT
        assert table.next_status(CLOSED, "reopen", DOCTOR) is None


# ── «Потребности» ─────────────────────────────────────────────────────


class TestNeedsStatuses:
    def test_edit_allowed_in_all_working_statuses(self):
        for status in (NONE, DRAFT, SENT):
            assert needs_statuses.REQUEST.can(status, needs_statuses.EDIT, NURSE) is True

    def test_edit_requires_permission(self):
        """Врач к «Потребностям» отношения не имеет — перехода нет."""
        assert needs_statuses.REQUEST.can(DRAFT, needs_statuses.EDIT, DOCTOR) is False

    def test_edit_goes_back_to_draft(self):
        """После правки отправленная заявка снова черновик."""
        assert (
            needs_statuses.REQUEST.next_status(SENT, needs_statuses.EDIT, NURSE) == DRAFT
        )

    def test_submit_from_draft_and_sent(self):
        assert needs_statuses.REQUEST.can(DRAFT, needs_statuses.SUBMIT, NURSE) is True
        assert needs_statuses.REQUEST.can(SENT, needs_statuses.SUBMIT, NURSE) is True

    def test_submit_from_empty_status_is_not_a_transition(self):
        """Отправлять пустую точку нечего — это не переход, а отсутствие заявки."""
        assert needs_statuses.REQUEST.can(NONE, needs_statuses.SUBMIT, NURSE) is False

    def test_head_nurse_has_full_rights(self):
        for action in (needs_statuses.EDIT, needs_statuses.SUBMIT):
            assert needs_statuses.REQUEST.can(DRAFT, action, HEAD_NURSE) is True
        assert needs_statuses.WEEK.can(OPEN, needs_statuses.CLOSE, HEAD_NURSE) is True

    def test_week_close_and_reopen_only_for_managers(self):
        assert needs_statuses.WEEK.can(OPEN, needs_statuses.CLOSE, HEAD) is True
        assert needs_statuses.WEEK.can(CLOSED, needs_statuses.REOPEN, HEAD) is True
        assert needs_statuses.WEEK.can(OPEN, needs_statuses.CLOSE, NURSE) is False
        assert needs_statuses.WEEK.can(CLOSED, needs_statuses.REOPEN, NURSE) is False

    def test_reopen_returns_open(self):
        assert (
            needs_statuses.WEEK.next_status(CLOSED, needs_statuses.REOPEN, HEAD_NURSE)
            == OPEN
        )


# ── «Дежурства» ───────────────────────────────────────────────────────


class TestDutyStatuses:
    def test_doctor_edits_only_while_not_closed(self):
        assert duty_statuses.REPORT.can(DRAFT, duty_statuses.EDIT, DOCTOR) is True
        assert duty_statuses.REPORT.can(SENT, duty_statuses.EDIT, DOCTOR) is True
        assert duty_statuses.REPORT.can(CLOSED, duty_statuses.EDIT, DOCTOR) is False

    def test_editor_is_a_doctor_with_curation(self):
        """`editor` — врач с правом курирования: в дежурствах ведёт себя как врач."""
        assert duty_statuses.REPORT.can(DRAFT, duty_statuses.EDIT, EDITOR) is True

    def test_head_does_not_edit_reports(self):
        """Заведующий видит и закрывает, но чужой отчёт не вводит."""
        assert duty_statuses.REPORT.can(DRAFT, duty_statuses.EDIT, HEAD) is False
        assert duty_statuses.REPORT.can(DRAFT, duty_statuses.SUBMIT, HEAD) is False

    def test_close_and_reopen_only_for_head(self):
        assert duty_statuses.REPORT.can(DRAFT, duty_statuses.CLOSE, HEAD) is True
        assert duty_statuses.REPORT.can(SENT, duty_statuses.CLOSE, HEAD) is True
        assert duty_statuses.REPORT.can(DRAFT, duty_statuses.CLOSE, DOCTOR) is False
        assert duty_statuses.REPORT.can(CLOSED, duty_statuses.REOPEN, HEAD) is True
        assert duty_statuses.REPORT.can(CLOSED, duty_statuses.REOPEN, DOCTOR) is False

    def test_closed_report_has_no_way_back_except_reopen(self):
        """Из «закрыт» есть только переоткрытие — правки и отправка недоступны."""
        assert duty_statuses.REPORT.allowed_actions(CLOSED, HEAD) == (duty_statuses.REOPEN,)
        assert duty_statuses.REPORT.allowed_actions(CLOSED, DOCTOR) == ()

    def test_send_twice_keeps_sent(self):
        """Повторная отправка ничего не меняет — статус тот же."""
        assert duty_statuses.REPORT.next_status(DRAFT, duty_statuses.SUBMIT, DOCTOR) == SENT
        assert duty_statuses.REPORT.next_status(SENT, duty_statuses.SUBMIT, DOCTOR) == SENT

    def test_reopen_returns_to_sent(self):
        assert (
            duty_statuses.REPORT.next_status(CLOSED, duty_statuses.REOPEN, HEAD) == SENT
        )

    def test_nurse_has_no_duty_transitions(self):
        for status in (DRAFT, SENT, CLOSED):
            assert duty_statuses.REPORT.allowed_actions(status, NURSE) == ()
