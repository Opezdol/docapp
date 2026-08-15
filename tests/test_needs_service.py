"""Тесты сервиса подприложения «Потребности» (NeedsService).

Бизнес-правила: права на создание/правку/отправку (автор или старшая),
закрытые недели, снимки unit/grp из каталога, доска старшей,
закрытие/переоткрытие с предупреждением о неотправленных точках.

Фикстура: временный YAML-каталог (2 базы × 2 точки, группы с препаратами,
включая «Растворы») + SQLite-БД на tmp_path.
"""

from datetime import date, timedelta

import pytest

from docapp.domain.employee import HEAD, HEAD_NURSE, NURSE, Employee
from docapp.needs.catalog import Catalog
from docapp.needs.service import (
    ALLOWED_FULL,
    NeedsClosed,
    NeedsForbidden,
    NeedsService,
    monday_of_week,
)
from docapp.needs.store import SqliteNeedsStore

CATALOG_YAML = """\
bases:
  Ленская: [травма, урология]
  Таймырская: [экстренная, гной]

groups:
  Неспецифика:
    Атропин: амп
    Дексаметазон: амп
  Растворы:
    Физ 200/250: фл
    Рингер: фл
"""

WEEK = "2026-08-10"


def make_line(item, qty):
    """Строка заявки без unit/grp — снимки заполнит сервис."""
    return {"item": item, "qty": qty}


@pytest.fixture
def service(tmp_path):
    """Сервис на временных БД и YAML-каталоге (2 базы × 2 точки)."""
    catalog_path = tmp_path / "catalog.yaml"
    catalog_path.write_text(CATALOG_YAML, encoding="utf-8")
    store = SqliteNeedsStore(tmp_path / "needs.db")
    yield NeedsService(store, Catalog(catalog_path))
    store.close_conn()


class TestMondayOfWeek:
    def test_current_week(self):
        """a) Понедельник текущей недели."""
        today = date.today()
        expected = (today - timedelta(days=today.weekday())).isoformat()
        assert monday_of_week() == expected

    def test_given_date(self):
        """a') По конкретной дате: четверг 2026-08-13 -> понедельник 2026-08-10."""
        assert monday_of_week(date(2026, 8, 13)) == "2026-08-10"  # четверг
        assert monday_of_week(date(2026, 8, 10)) == "2026-08-10"  # сам понедельник
        assert monday_of_week(date(2026, 8, 16)) == "2026-08-10"  # воскресенье


class TestSave:
    def test_new_request_snapshots_and_draft(self, service):
        """b) Медсестра создаёт заявку: unit/grp из каталога, статус draft."""
        req = service.save(
            7, NURSE, "Ленская", "травма", WEEK,
            [make_line("Атропин", 10), make_line("Рингер", 4)],
        )
        assert req["status"] == "draft"
        assert req["author_id"] == 7
        by_item = {line["item"]: line for line in req["lines"]}
        assert by_item["Атропин"]["unit"] == "амп"
        assert by_item["Атропин"]["grp"] == "Неспецифика"
        assert by_item["Рингер"]["unit"] == "фл"
        assert by_item["Рингер"]["grp"] == "Растворы"

    def test_edit_own_keeps_author(self, service):
        """c) Правка своей заявки другим набором строк — автор тот же."""
        service.save(7, NURSE, "Ленская", "травма", WEEK, [make_line("Атропин", 10)])
        req = service.save(
            7, NURSE, "Ленская", "травма", WEEK,
            [make_line("Атропин", 15), make_line("Дексаметазон", 2)],
        )
        assert req["author_id"] == 7
        assert req["status"] == "draft"
        assert {line["item"] for line in req["lines"]} == {"Атропин", "Дексаметазон"}
        assert req["lines"][0]["qty"] == 15

    def test_nurse_cannot_edit_others(self, service):
        """d) Правка чужой заявки медсестрой -> NeedsForbidden."""
        service.save(7, NURSE, "Ленская", "травма", WEEK, [make_line("Атропин", 10)])
        with pytest.raises(NeedsForbidden):
            service.save(
                8, NURSE, "Ленская", "травма", WEEK, [make_line("Атропин", 20)]
            )

    def test_head_nurse_edit_keeps_original_author(self, service):
        """e) Правка чужой заявки head_nurse: успешно, автор исходный (F9)."""
        service.save(7, NURSE, "Ленская", "травма", WEEK, [make_line("Атропин", 10)])
        req = service.save(
            100, HEAD_NURSE, "Ленская", "травма", WEEK,
            [make_line("Атропин", 30), make_line("Рингер", 5)],
        )
        assert req["author_id"] == 7  # F9: исходный автор сохранён
        assert req["lines"][0]["qty"] == 30

    def test_head_edit_others_request(self, service):
        """e') Заведующий (HEAD) тоже правит чужую заявку, автор исходный."""
        service.save(7, NURSE, "Ленская", "травма", WEEK, [make_line("Атропин", 10)])
        req = service.save(
            200, HEAD, "Ленская", "травма", WEEK, [make_line("Атропин", 12)]
        )
        assert req["author_id"] == 7

    def test_save_closed_week_raises(self, service):
        """f) Save после закрытия базы -> NeedsClosed."""
        service.save(7, NURSE, "Ленская", "травма", WEEK, [make_line("Атропин", 10)])
        service.close(100, HEAD_NURSE, "Ленская", WEEK)
        with pytest.raises(NeedsClosed):
            service.save(
                7, NURSE, "Ленская", "травма", WEEK, [make_line("Атропин", 20)]
            )

    def test_unknown_item_empty_snapshot(self, service):
        """g) Неизвестный препарат: unit='' и grp='', без исключений."""
        req = service.save(
            7, NURSE, "Ленская", "травма", WEEK, [make_line("Несуществующий", 3)]
        )
        line = req["lines"][0]
        assert line["item"] == "Несуществующий"
        assert line["unit"] == ""
        assert line["grp"] == ""
        assert line["qty"] == 3

    def test_doctor_cannot_create_request(self, service):
        """g') Доктор не создаёт заявки (доступ к «Потребностям» — nurse+)."""
        with pytest.raises(NeedsForbidden):
            service.save(
                5, "doctor", "Ленская", "травма", WEEK, [make_line("Атропин", 1)]
            )


class TestSubmit:
    def test_submit_own(self, service):
        """h) Отправка своей заявки -> status 'sent', без предупреждений."""
        service.save(7, NURSE, "Ленская", "травма", WEEK, [make_line("Атропин", 10)])
        result = service.submit(7, NURSE, "Ленская", "травма", WEEK)
        assert result["request"]["status"] == "sent"
        assert result["warnings"] == []

    def test_submit_others_forbidden(self, service):
        """h') Отправка чужой заявки медсестрой -> NeedsForbidden."""
        service.save(7, NURSE, "Ленская", "травма", WEEK, [make_line("Атропин", 10)])
        with pytest.raises(NeedsForbidden):
            service.submit(8, NURSE, "Ленская", "травма", WEEK)

    def test_submit_others_by_head_nurse(self, service):
        """h'') Старшая отправляет чужую заявку."""
        service.save(7, NURSE, "Ленская", "травма", WEEK, [make_line("Атропин", 10)])
        result = service.submit(100, HEAD_NURSE, "Ленская", "травма", WEEK)
        assert result["request"]["status"] == "sent"

    def test_submit_zero_qty_warns_but_sends(self, service):
        """i) qty=0 не блокирует отправку; позиция попадает в warnings."""
        service.save(
            7, NURSE, "Ленская", "травма", WEEK,
            [make_line("Атропин", 10), make_line("Рингер", 0)],
        )
        result = service.submit(7, NURSE, "Ленская", "травма", WEEK)
        assert result["request"]["status"] == "sent"
        assert result["warnings"] == ["Рингер: 0"]

    def test_submit_closed_week_raises(self, service):
        """i') Отправка после закрытия базы -> NeedsClosed."""
        service.save(7, NURSE, "Ленская", "травма", WEEK, [make_line("Атропин", 10)])
        service.close(100, HEAD_NURSE, "Ленская", WEEK)
        with pytest.raises(NeedsClosed):
            service.submit(7, NURSE, "Ленская", "травма", WEEK)

    def test_submit_missing_request_forbidden(self, service):
        """i'') Отправка несуществующей заявки -> NeedsForbidden."""
        with pytest.raises(NeedsForbidden):
            service.submit(7, NURSE, "Ленская", "травма", WEEK)


class TestBoard:
    def test_board_statuses_all_points(self, service):
        """j) Доска: none/draft/sent по всем точкам обеих баз."""
        service.save(7, NURSE, "Ленская", "травма", WEEK, [make_line("Атропин", 5)])
        service.save(8, NURSE, "Ленская", "урология", WEEK, [make_line("Рингер", 2)])
        service.submit(8, NURSE, "Ленская", "урология", WEEK)

        cells = service.board(WEEK)
        by_key = {(cell["base"], cell["point"]): cell for cell in cells}
        assert len(cells) == 4  # 2 базы × 2 точки

        # точка без заявки — 'none' с пустыми полями
        empty = by_key[("Таймырская", "экстренная")]
        assert empty["status"] == "none"
        assert empty["author_id"] is None
        assert empty["request_id"] is None
        assert empty["updated_at"] is None

        # черновик
        draft = by_key[("Ленская", "травма")]
        assert draft["status"] == "draft"
        assert draft["author_id"] == 7
        assert draft["request_id"] is not None
        assert draft["updated_at"] is not None

        # отправленная
        sent = by_key[("Ленская", "урология")]
        assert sent["status"] == "sent"
        assert sent["author_id"] == 8

        # остальные точки без заявок
        assert by_key[("Таймырская", "гной")]["status"] == "none"

    def test_board_default_week_is_current(self, service):
        """j') Без week_start доска смотрит на текущую неделю."""
        week = monday_of_week()
        service.save(7, NURSE, "Ленская", "травма", week, [make_line("Атропин", 5)])
        cells = service.board()
        assert any(
            cell["point"] == "травма" and cell["status"] == "draft" for cell in cells
        )


class TestCloseReopen:
    def test_close_reports_unsent_points(self, service):
        """k) close: unsent_points для неотправленных; is_closed; reopen."""
        service.save(7, NURSE, "Ленская", "травма", WEEK, [make_line("Атропин", 5)])
        service.submit(7, NURSE, "Ленская", "травма", WEEK)
        # урология Ленской не отправлена, Таймырская целиком не отправлена
        result = service.close(100, HEAD_NURSE, "Ленская", WEEK)
        assert result["closed"] is True
        assert result["unsent_points"] == ["урология"]

        assert service.is_closed("Ленская", WEEK) is True
        # после закрытия правки запрещены даже автору
        with pytest.raises(NeedsClosed):
            service.save(
                7, NURSE, "Ленская", "травма", WEEK, [make_line("Атропин", 9)]
            )

        reopened = service.reopen(100, HEAD_NURSE, "Ленская", WEEK)
        assert reopened == {"reopened": True}
        assert service.is_closed("Ленская", WEEK) is False
        # после переоткрытия правка снова возможна
        req = service.save(
            7, NURSE, "Ленская", "травма", WEEK, [make_line("Атропин", 9)]
        )
        assert req["lines"][0]["qty"] == 9

    def test_close_all_sent_no_unsent(self, service):
        """k') Все точки отправлены — unsent_points пуст (закрытие по HEAD)."""
        for point in ("травма", "урология"):
            service.save(7, NURSE, "Ленская", point, WEEK, [make_line("Атропин", 5)])
            service.submit(7, NURSE, "Ленская", point, WEEK)
        result = service.close(200, HEAD, "Ленская", WEEK)
        assert result == {"closed": True, "unsent_points": []}

    def test_close_by_nurse_forbidden(self, service):
        """k'') Медсестра не закрывает недели."""
        with pytest.raises(NeedsForbidden):
            service.close(7, NURSE, "Ленская", WEEK)

    def test_reopen_by_nurse_forbidden(self, service):
        """k''') Медсестра не переоткрывает недели."""
        with pytest.raises(NeedsForbidden):
            service.reopen(7, NURSE, "Ленская", WEEK)

    def test_is_closed_default_week(self, service):
        """k''''') is_closed по умолчанию — текущая неделя."""
        service.close(100, HEAD_NURSE, "Ленская")
        assert service.is_closed("Ленская") is True
        assert service.is_closed("Таймырская") is False


class TestGetForUser:
    def test_visibility_rules(self, service):
        """l) Своя/чужая sent/чужой draft/полные роли."""
        service.save(7, NURSE, "Ленская", "травма", WEEK, [make_line("Атропин", 5)])
        service.submit(7, NURSE, "Ленская", "травма", WEEK)  # для 8 — чужая sent
        service.save(9, NURSE, "Ленская", "урология", WEEK, [make_line("Рингер", 2)])
        # для 8 — чужой draft
        service.save(8, NURSE, "Таймырская", "экстренная", WEEK, [make_line("Атропин", 1)])

        # своя заявка (draft) — видна как есть
        own = service.get_for_user(8, NURSE, "Таймырская", "экстренная", WEEK)
        assert own is not None
        assert own["author_id"] == 8

        # чужая отправленная — видна
        sent = service.get_for_user(8, NURSE, "Ленская", "травма", WEEK)
        assert sent is not None
        assert sent["status"] == "sent"

        # чужой черновик — скрыт
        assert service.get_for_user(8, NURSE, "Ленская", "урология", WEEK) is None

        # head_nurse видит всё, включая чужой черновик
        hn = service.get_for_user(100, HEAD_NURSE, "Ленская", "урология", WEEK)
        assert hn is not None
        assert hn["author_id"] == 9

        # head видит всё
        head = service.get_for_user(200, HEAD, "Ленская", "урология", WEEK)
        assert head is not None
        assert head["author_id"] == 9

        # заявки нет — None
        assert service.get_for_user(8, NURSE, "Таймырская", "гной", WEEK) is None


class TestRolesAndExceptions:
    def test_exceptions_are_value_errors(self):
        """Исключения сервиса — подклассы ValueError (роутер ловит как 4xx)."""
        assert issubclass(NeedsForbidden, ValueError)
        assert issubclass(NeedsClosed, ValueError)

    def test_allowed_full_roles(self):
        """ALLOWED_FULL — head_nurse и head (полные права)."""
        assert set(ALLOWED_FULL) == {HEAD_NURSE, HEAD}
        assert HEAD_NURSE == "head_nurse"

    def test_head_nurse_role_in_domain(self):
        """Правка domain: HEAD_NURSE объявлена и принята Employee."""
        emp = Employee(last_name="Петрова", first_name="Елена", role=HEAD_NURSE)
        assert emp.role == HEAD_NURSE == "head_nurse"
