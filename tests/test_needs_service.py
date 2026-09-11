"""Тесты сервиса подприложения «Потребности» (NeedsService).

Бизнес-правила: права на создание/правку/отправку (автор или старшая),
закрытые недели, снимки unit/grp из каталога, доска старшей (по разделам),
валидация соответствия строк разделу, закрытие/переоткрытие с предупреждением
о неотправленных точках.

Фикстура: временный YAML-каталог (2 базы × 2 точки, группы с препаратами,
включая «Растворы») + SQLite-БД на tmp_path.
"""

import pytest

from docapp.core import access, period
from docapp.domain.employee import HEAD, HEAD_NURSE, NURSE, Employee
from docapp.needs.catalog import (
    CATEGORY_MEDICAMENTS,
    CATEGORY_SOLUTIONS,
    Catalog,
)
from docapp.needs.service import NeedsClosed, NeedsForbidden, NeedsService
from docapp.needs.store import SqliteNeedsStore
from factories import test_db

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

SOL = CATEGORY_SOLUTIONS
MED = CATEGORY_MEDICAMENTS


def make_line(item, qty):
    """Строка заявки без unit/grp — снимки заполнит сервис."""
    return {"item": item, "qty": qty}


@pytest.fixture
def service(tmp_path):
    """Сервис на временных БД и YAML-каталоге (2 базы × 2 точки)."""
    catalog_path = tmp_path / "catalog.yaml"
    catalog_path.write_text(CATALOG_YAML, encoding="utf-8")
    store = SqliteNeedsStore(test_db(tmp_path))
    yield NeedsService(store, Catalog(catalog_path))
    store.close_conn()


class TestSave:
    def test_new_request_snapshots_and_draft(self, service):
        """b) Медсестра создаёт заявку раздела: unit/grp из каталога, статус draft."""
        med = service.save(7, NURSE, "Ленская", "травма", MED, WEEK, [make_line("Атропин", 10)])
        assert med["status"] == "draft"
        assert med["author_id"] == 7
        assert med["category"] == MED
        by_item = {line["item"]: line for line in med["lines"]}
        assert by_item["Атропин"]["unit"] == "амп"
        assert by_item["Атропин"]["grp"] == "Неспецифика"

        sol = service.save(7, NURSE, "Ленская", "травма", SOL, WEEK, [make_line("Рингер", 4)])
        assert sol["category"] == SOL
        by_item = {line["item"]: line for line in sol["lines"]}
        assert by_item["Рингер"]["unit"] == "фл"
        assert by_item["Рингер"]["grp"] == "Растворы"

    def test_edit_own_keeps_author(self, service):
        """c) Правка своей заявки другим набором строк — автор тот же."""
        service.save(7, NURSE, "Ленская", "травма", MED, WEEK, [make_line("Атропин", 10)])
        req = service.save(
            7, NURSE, "Ленская", "травма", MED, WEEK,
            [make_line("Атропин", 15), make_line("Дексаметазон", 2)],
        )
        assert req["author_id"] == 7
        assert req["status"] == "draft"
        assert {line["item"] for line in req["lines"]} == {"Атропин", "Дексаметазон"}
        assert req["lines"][0]["qty"] == 15

    def test_nurse_cannot_edit_others(self, service):
        """d) Правка чужой заявки медсестрой -> NeedsForbidden."""
        service.save(7, NURSE, "Ленская", "травма", MED, WEEK, [make_line("Атропин", 10)])
        with pytest.raises(NeedsForbidden):
            service.save(8, NURSE, "Ленская", "травма", MED, WEEK, [make_line("Атропин", 20)])

    def test_head_nurse_edit_keeps_original_author(self, service):
        """e) Правка чужой заявки head_nurse: успешно, автор исходный (F9)."""
        service.save(7, NURSE, "Ленская", "травма", MED, WEEK, [make_line("Атропин", 10)])
        req = service.save(
            100, HEAD_NURSE, "Ленская", "травма", MED, WEEK,
            [make_line("Атропин", 30), make_line("Дексаметазон", 5)],
        )
        assert req["author_id"] == 7  # F9: исходный автор сохранён
        assert req["lines"][0]["qty"] == 30

    def test_head_edit_others_request(self, service):
        """e') Заведующий (HEAD) тоже правит чужую заявку, автор исходный."""
        service.save(7, NURSE, "Ленская", "травма", MED, WEEK, [make_line("Атропин", 10)])
        req = service.save(
            200, HEAD, "Ленская", "травма", MED, WEEK, [make_line("Атропин", 12)]
        )
        assert req["author_id"] == 7

    def test_save_closed_week_raises(self, service):
        """f) Save после закрытия раздела базы -> NeedsClosed."""
        service.save(7, NURSE, "Ленская", "травма", MED, WEEK, [make_line("Атропин", 10)])
        service.close(100, HEAD_NURSE, "Ленская", MED, WEEK)
        with pytest.raises(NeedsClosed):
            service.save(7, NURSE, "Ленская", "травма", MED, WEEK, [make_line("Атропин", 20)])

    def test_unknown_item_empty_snapshot(self, service):
        """g) Неизвестный препарат: unit='' и grp='', без исключений (в медикаментах)."""
        req = service.save(
            7, NURSE, "Ленская", "травма", MED, WEEK, [make_line("Несуществующий", 3)]
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
                5, "doctor", "Ленская", "травма", MED, WEEK, [make_line("Атропин", 1)]
            )

    def test_save_solutions_rejects_medicament_line(self, service):
        """Валидация раздела: растворы-заявка не принимает не-растворы."""
        with pytest.raises(ValueError, match="не относится к разделу «Растворы»"):
            service.save(7, NURSE, "Ленская", "травма", SOL, WEEK, [make_line("Атропин", 1)])

    def test_save_medicaments_rejects_solution_line(self, service):
        """Валидация раздела: медикаменты-заявка не принимает растворы."""
        with pytest.raises(ValueError, match="не относится к разделу «Медикаменты»"):
            service.save(7, NURSE, "Ленская", "травма", MED, WEEK, [make_line("Рингер", 1)])

    def test_save_unknown_category_raises(self, service):
        """Неизвестный раздел -> ValueError."""
        with pytest.raises(ValueError, match="Неизвестный раздел"):
            service.save(7, NURSE, "Ленская", "травма", "bogus", WEEK, [make_line("Атропин", 1)])


class TestSubmit:
    def test_submit_own(self, service):
        """h) Отправка своей заявки раздела -> status 'sent', без предупреждений."""
        service.save(7, NURSE, "Ленская", "травма", MED, WEEK, [make_line("Атропин", 10)])
        result = service.submit(7, NURSE, "Ленская", "травма", MED, WEEK)
        assert result["request"]["status"] == "sent"
        assert result["warnings"] == []

    def test_submit_others_forbidden(self, service):
        """h') Отправка чужой заявки медсестрой -> NeedsForbidden."""
        service.save(7, NURSE, "Ленская", "травма", MED, WEEK, [make_line("Атропин", 10)])
        with pytest.raises(NeedsForbidden):
            service.submit(8, NURSE, "Ленская", "травма", MED, WEEK)

    def test_submit_others_by_head_nurse(self, service):
        """h'') Старшая отправляет чужую заявку."""
        service.save(7, NURSE, "Ленская", "травма", MED, WEEK, [make_line("Атропин", 10)])
        result = service.submit(100, HEAD_NURSE, "Ленская", "травма", MED, WEEK)
        assert result["request"]["status"] == "sent"

    def test_submit_zero_qty_warns_but_sends(self, service):
        """i) qty=0 не блокирует отправку; позиция попадает в warnings."""
        service.save(
            7, NURSE, "Ленская", "травма", MED, WEEK,
            [make_line("Атропин", 10), make_line("Дексаметазон", 0)],
        )
        result = service.submit(7, NURSE, "Ленская", "травма", MED, WEEK)
        assert result["request"]["status"] == "sent"
        assert result["warnings"] == ["Дексаметазон: 0"]

    def test_submit_closed_week_raises(self, service):
        """i') Отправка после закрытия раздела базы -> NeedsClosed."""
        service.save(7, NURSE, "Ленская", "травма", MED, WEEK, [make_line("Атропин", 10)])
        service.close(100, HEAD_NURSE, "Ленская", MED, WEEK)
        with pytest.raises(NeedsClosed):
            service.submit(7, NURSE, "Ленская", "травма", MED, WEEK)

    def test_submit_missing_request_forbidden(self, service):
        """i'') Отправка несуществующей заявки раздела -> NeedsForbidden."""
        with pytest.raises(NeedsForbidden):
            service.submit(7, NURSE, "Ленская", "травма", MED, WEEK)


class TestBoard:
    def test_board_statuses_all_points_and_categories(self, service):
        """j) Доска: none/draft/sent по всем точкам обеих баз и обоим разделам."""
        service.save(7, NURSE, "Ленская", "травма", MED, WEEK, [make_line("Атропин", 5)])
        service.save(8, NURSE, "Ленская", "урология", SOL, WEEK, [make_line("Рингер", 2)])
        service.submit(8, NURSE, "Ленская", "урология", SOL, WEEK)

        cells = service.board(WEEK)
        by_key = {(cell["base"], cell["point"], cell["category"]): cell for cell in cells}
        assert len(cells) == 8  # 2 базы × 2 точки × 2 раздела

        # точка/раздел без заявки — 'none' с пустыми полями
        empty = by_key[("Таймырская", "экстренная", MED)]
        assert empty["status"] == "none"
        assert empty["author_id"] is None
        assert empty["request_id"] is None
        assert empty["updated_at"] is None

        # черновик медикаментов
        draft = by_key[("Ленская", "травма", MED)]
        assert draft["status"] == "draft"
        assert draft["author_id"] == 7
        assert draft["request_id"] is not None
        assert draft["updated_at"] is not None

        # отправленные растворы
        sent = by_key[("Ленская", "урология", SOL)]
        assert sent["status"] == "sent"
        assert sent["author_id"] == 8

        # тот же раздел растворов на другой точке — пусто
        assert by_key[("Ленская", "травма", SOL)]["status"] == "none"
        # другой раздел той же отправленной точки — пусто
        assert by_key[("Ленская", "урология", MED)]["status"] == "none"

    def test_board_default_week_is_current(self, service):
        """j') Без week_start доска смотрит на текущую неделю."""
        week = period.week_start()
        service.save(7, NURSE, "Ленская", "травма", MED, week, [make_line("Атропин", 5)])
        cells = service.board()
        assert any(
            cell["point"] == "травма" and cell["category"] == MED and cell["status"] == "draft"
            for cell in cells
        )


class TestCloseReopen:
    def test_close_reports_unsent_points(self, service):
        """k) close: unsent_points для неотправленных (по разделу); is_closed; reopen."""
        service.save(7, NURSE, "Ленская", "травма", MED, WEEK, [make_line("Атропин", 5)])
        service.submit(7, NURSE, "Ленская", "травма", MED, WEEK)
        # урология Ленской не отправлена в разделе медикаментов
        result = service.close(100, HEAD_NURSE, "Ленская", MED, WEEK)
        assert result["closed"] is True
        assert result["unsent_points"] == ["урология"]

        assert service.is_closed("Ленская", MED, WEEK) is True
        # после закрытия раздела правки запрещены даже автору
        with pytest.raises(NeedsClosed):
            service.save(7, NURSE, "Ленская", "травма", MED, WEEK, [make_line("Атропин", 9)])

        reopened = service.reopen(100, HEAD_NURSE, "Ленская", MED, WEEK)
        assert reopened == {"reopened": True}
        assert service.is_closed("Ленская", MED, WEEK) is False
        # после переоткрытия правка снова возможна
        req = service.save(7, NURSE, "Ленская", "травма", MED, WEEK, [make_line("Атропин", 9)])
        assert req["lines"][0]["qty"] == 9

    def test_close_one_section_does_not_close_other(self, service):
        """k') Закрытие растворов не трогает медикаменты той же базы (F7)."""
        service.save(7, NURSE, "Ленская", "травма", MED, WEEK, [make_line("Атропин", 5)])
        service.save(7, NURSE, "Ленская", "травма", SOL, WEEK, [make_line("Рингер", 5)])
        service.close(100, HEAD_NURSE, "Ленская", SOL, WEEK)
        assert service.is_closed("Ленская", SOL, WEEK) is True
        assert service.is_closed("Ленская", MED, WEEK) is False

    def test_close_all_sent_no_unsent(self, service):
        """k'') Все точки раздела отправлены — unsent_points пуст (закрытие по HEAD)."""
        for point in ("травма", "урология"):
            service.save(7, NURSE, "Ленская", point, MED, WEEK, [make_line("Атропин", 5)])
            service.submit(7, NURSE, "Ленская", point, MED, WEEK)
        result = service.close(200, HEAD, "Ленская", MED, WEEK)
        assert result == {"closed": True, "unsent_points": []}

    def test_close_by_nurse_forbidden(self, service):
        """k''') Медсестра не закрывает недели."""
        with pytest.raises(NeedsForbidden):
            service.close(7, NURSE, "Ленская", MED, WEEK)

    def test_reopen_by_nurse_forbidden(self, service):
        """k'''') Медсестра не переоткрывает недели."""
        with pytest.raises(NeedsForbidden):
            service.reopen(7, NURSE, "Ленская", MED, WEEK)

    def test_is_closed_default_week(self, service):
        """k''''') is_closed по умолчанию — текущая неделя."""
        service.close(100, HEAD_NURSE, "Ленская", MED)
        assert service.is_closed("Ленская", MED) is True
        assert service.is_closed("Ленская", SOL) is False
        assert service.is_closed("Таймырская", MED) is False


class TestGetForUser:
    def test_visibility_rules(self, service):
        """l) Своя/чужая sent/чужой draft/полные роли — по разделу."""
        service.save(7, NURSE, "Ленская", "травма", MED, WEEK, [make_line("Атропин", 5)])
        service.submit(7, NURSE, "Ленская", "травма", MED, WEEK)  # для 8 — чужая sent
        service.save(9, NURSE, "Ленская", "урология", MED, WEEK, [make_line("Атропин", 2)])
        # для 8 — чужой draft
        service.save(8, NURSE, "Таймырская", "экстренная", MED, WEEK, [make_line("Атропин", 1)])

        # своя заявка (draft) — видна как есть
        own = service.get_for_user(8, NURSE, "Таймырская", "экстренная", MED, WEEK)
        assert own is not None
        assert own["author_id"] == 8

        # чужая отправленная — видна
        sent = service.get_for_user(8, NURSE, "Ленская", "травма", MED, WEEK)
        assert sent is not None
        assert sent["status"] == "sent"

        # чужой черновик — скрыт
        assert service.get_for_user(8, NURSE, "Ленская", "урология", MED, WEEK) is None

        # head_nurse видит всё, включая чужой черновик
        hn = service.get_for_user(100, HEAD_NURSE, "Ленская", "урология", MED, WEEK)
        assert hn is not None
        assert hn["author_id"] == 9

        # head видит всё
        head = service.get_for_user(200, HEAD, "Ленская", "урология", MED, WEEK)
        assert head is not None
        assert head["author_id"] == 9

        # заявки нет — None (и в другом разделе тоже)
        assert service.get_for_user(8, NURSE, "Таймырская", "гной", MED, WEEK) is None
        assert service.get_for_user(8, NURSE, "Ленская", "травма", SOL, WEEK) is None


class TestRolesAndExceptions:
    def test_exceptions_are_value_errors(self):
        """Исключения сервиса — подклассы ValueError (роутер ловит как 4xx)."""
        assert issubclass(NeedsForbidden, ValueError)
        assert issubclass(NeedsClosed, ValueError)

    def test_full_rights_come_from_access_table(self):
        """Полные права — из таблицы core/access, а не из своего кортежа ролей."""
        roles = {role for role, allowed in access.ALLOWED.items() if access.NEEDS_MANAGE in allowed}
        assert roles == {HEAD_NURSE, HEAD}
        assert HEAD_NURSE == "head_nurse"

    def test_head_nurse_role_in_domain(self):
        """Правка domain: HEAD_NURSE объявлена и принята Employee."""
        emp = Employee(last_name="Петрова", first_name="Елена", role=HEAD_NURSE)
        assert emp.role == HEAD_NURSE == "head_nurse"
