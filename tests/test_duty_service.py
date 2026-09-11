"""Тесты сервиса «Дежурств»: окно смены, валидация, сохранение/отправка, авто-закрытие."""

from datetime import date, datetime, timezone

import pytest

from docapp.duty.config import DutyConfig
from docapp.duty.service import DutyClosed, DutyService, _offset_minutes
from docapp.duty.store import SqliteDutyStore
from factories import test_db

UTC = timezone.utc


def aware(y, m, d, hour, minute=0):
    return datetime(y, m, d, hour, minute, tzinfo=UTC)


@pytest.fixture
def svc(tmp_path):
    store = SqliteDutyStore(test_db(tmp_path))
    config = DutyConfig(tz=UTC)
    service = DutyService(store, config)
    yield service
    store.close_conn()


class TestOffsetMinutes:
    @pytest.mark.parametrize(
        "hhmm,expected",
        [
            ("16:00", 0),
            ("23:30", 450),
            ("00:00", 480),
            ("08:00", 960),
        ],
    )
    def test_offsets(self, hhmm, expected):
        assert _offset_minutes(hhmm) == expected


class TestShiftDate:
    def test_evening_is_today(self, svc):
        assert svc.shift_date(aware(2026, 8, 31, 18, 0)) == date(2026, 8, 31)

    def test_after_midnight_is_yesterday(self, svc):
        assert svc.shift_date(aware(2026, 9, 1, 2, 0)) == date(2026, 8, 31)

    def test_before_0930_is_yesterday(self, svc):
        assert svc.shift_date(aware(2026, 9, 1, 9, 29)) == date(2026, 8, 31)

    def test_daytime_is_closed(self, svc):
        for hour in (10, 12, 15):
            assert svc.shift_date(aware(2026, 9, 1, hour, 0)) is None

    def test_0930_is_closed(self, svc):
        assert svc.shift_date(aware(2026, 9, 1, 9, 30)) is None

    def test_1600_is_open(self, svc):
        assert svc.shift_date(aware(2026, 8, 31, 16, 0)) == date(2026, 8, 31)


class TestWindowOpenFor:
    def test_open_during_shift(self, svc):
        now = aware(2026, 8, 31, 18, 0)
        assert svc.window_open_for(date(2026, 8, 31), now) is True

    def test_open_after_midnight(self, svc):
        now = aware(2026, 9, 1, 2, 0)
        assert svc.window_open_for(date(2026, 8, 31), now) is True

    def test_closed_at_0930(self, svc):
        now = aware(2026, 9, 1, 9, 30)
        assert svc.window_open_for(date(2026, 8, 31), now) is False


class TestValidation:
    def test_valid_operation(self, svc):
        op = {"operation": "НХО", "start_time": "21:00", "end_time": "21:45"}
        cleaned = svc._clean_operation(op)
        assert cleaned["operation"] == "НХО"
        assert cleaned["start_time"] == "21:00"
        assert cleaned["end_time"] == "21:45"

    def test_trims_name(self, svc):
        op = {"operation": "  НХО  ", "start_time": "21:00", "end_time": "21:45"}
        assert svc._clean_operation(op)["operation"] == "НХО"

    @pytest.mark.parametrize(
        "op",
        [
            {"operation": "", "start_time": "21:00", "end_time": "21:45"},
            {"operation": "   ", "start_time": "21:00", "end_time": "21:45"},
            {"operation": "НХО", "start_time": "2100", "end_time": "21:45"},
            {"operation": "НХО", "start_time": "21:00", "end_time": "25:00"},
            # вне окна дежурства (10:00 — дневное время)
            {"operation": "НХО", "start_time": "10:00", "end_time": "11:00"},
            # начало позже конца
            {"operation": "НХО", "start_time": "22:00", "end_time": "21:00"},
        ],
    )
    def test_invalid_operation_raises(self, svc, op):
        with pytest.raises(ValueError):
            svc._clean_operation(op)


class TestSaveSend:
    def test_save_creates_draft(self, svc, monkeypatch):
        monkeypatch.setattr(svc, "now", lambda: aware(2026, 8, 31, 18, 0))
        saved = svc.save(1, "Ленская", [{"operation": "НХО", "start_time": "21:00", "end_time": "21:45"}])
        assert saved["status"] == "draft"
        assert saved["shift_date"] == "2026-08-31"
        assert saved["base"] == "Ленская"
        assert saved["doctor_id"] == 1
        assert saved["operations"][0]["operation"] == "НХО"

    def test_save_closed_raises(self, svc, monkeypatch):
        monkeypatch.setattr(svc, "now", lambda: aware(2026, 8, 31, 11, 0))
        with pytest.raises(DutyClosed):
            svc.save(1, "Ленская", [{"operation": "НХО", "start_time": "21:00", "end_time": "21:45"}])

    def test_send_empty_raises(self, svc, monkeypatch):
        monkeypatch.setattr(svc, "now", lambda: aware(2026, 8, 31, 18, 0))
        with pytest.raises(ValueError):
            svc.send(1, "Ленская")

    def test_send_finalizes(self, svc, monkeypatch):
        monkeypatch.setattr(svc, "now", lambda: aware(2026, 8, 31, 18, 0))
        svc.save(1, "Ленская", [{"operation": "НХО", "start_time": "21:00", "end_time": "21:45"}])
        sent = svc.send(1, "Ленская")
        assert sent["status"] == "sent"

    def test_save_after_send_reverts_to_draft(self, svc, monkeypatch):
        # отправленный (sent) отчёт можно править — правка возвращает его в draft
        monkeypatch.setattr(svc, "now", lambda: aware(2026, 8, 31, 18, 0))
        svc.save(1, "Ленская", [{"operation": "НХО", "start_time": "21:00", "end_time": "21:45"}])
        svc.send(1, "Ленская")
        saved = svc.save(1, "Ленская", [{"operation": "НХО", "start_time": "22:00", "end_time": "22:30"}])
        assert saved["status"] == "draft"


class TestFinalizeStale:
    def test_finalizes_past_shift(self, svc, monkeypatch):
        # черновик за прошлую смену (30.08), окно которой уже закрыто к 18:00 31.08
        svc._store.save_report(
            "Ленская", "2026-08-30", 1,
            [{"operation": "НХО", "start_time": "21:00", "end_time": "21:45"}],
            status="draft",
        )
        monkeypatch.setattr(svc, "now", lambda: aware(2026, 8, 31, 18, 0))
        svc.finalize_stale()
        report = svc._store.get_report("Ленская", "2026-08-30", 1)
        assert report["status"] == "closed"

    def test_keeps_current_shift_draft(self, svc, monkeypatch):
        # черновик текущей смены (31.08) в 18:00 31.08 — окно ещё открыто
        svc._store.save_report(
            "Ленская", "2026-08-31", 1,
            [{"operation": "НХО", "start_time": "21:00", "end_time": "21:45"}],
            status="draft",
        )
        monkeypatch.setattr(svc, "now", lambda: aware(2026, 8, 31, 18, 0))
        svc.finalize_stale()
        report = svc._store.get_report("Ленская", "2026-08-31", 1)
        assert report["status"] == "draft"


class TestCloseReopen:
    def test_close_report_preserves_operations(self, svc, monkeypatch):
        monkeypatch.setattr(svc, "now", lambda: aware(2026, 8, 31, 18, 0))
        saved = svc.save(1, "Ленская", [{"operation": "НХО", "start_time": "21:00", "end_time": "21:45"}])
        svc.send(1, "Ленская")
        svc.close_report(saved["id"])
        report = svc._store.get_report("Ленская", "2026-08-31", 1)
        assert report["status"] == "closed"
        assert report["operations"][0]["operation"] == "НХО"

    def test_close_shift_closes_all(self, svc, monkeypatch):
        monkeypatch.setattr(svc, "now", lambda: aware(2026, 8, 31, 18, 0))
        svc.save(1, "Ленская", [{"operation": "НХО", "start_time": "21:00", "end_time": "21:45"}])
        svc.save(2, "Таймырская", [{"operation": "Травма", "start_time": "22:00", "end_time": "22:30"}])
        svc.close_shift("2026-08-31")
        assert svc._store.get_report("Ленская", "2026-08-31", 1)["status"] == "closed"
        assert svc._store.get_report("Таймырская", "2026-08-31", 2)["status"] == "closed"

    def test_reopen_report(self, svc, monkeypatch):
        monkeypatch.setattr(svc, "now", lambda: aware(2026, 8, 31, 18, 0))
        saved = svc.save(1, "Ленская", [{"operation": "НХО", "start_time": "21:00", "end_time": "21:45"}])
        svc.send(1, "Ленская")
        svc.close_report(saved["id"])
        svc.reopen_report(saved["id"])
        assert svc._store.get_report("Ленская", "2026-08-31", 1)["status"] == "sent"

    def test_save_blocked_after_close(self, svc, monkeypatch):
        monkeypatch.setattr(svc, "now", lambda: aware(2026, 8, 31, 18, 0))
        saved = svc.save(1, "Ленская", [{"operation": "НХО", "start_time": "21:00", "end_time": "21:45"}])
        svc.close_report(saved["id"])
        with pytest.raises(DutyClosed):
            svc.save(1, "Ленская", [{"operation": "НХО", "start_time": "22:00", "end_time": "22:30"}])

    def test_send_blocked_after_close(self, svc, monkeypatch):
        monkeypatch.setattr(svc, "now", lambda: aware(2026, 8, 31, 18, 0))
        saved = svc.save(1, "Ленская", [{"operation": "НХО", "start_time": "21:00", "end_time": "21:45"}])
        svc.close_report(saved["id"])
        with pytest.raises(DutyClosed):
            svc.send(1, "Ленская")
