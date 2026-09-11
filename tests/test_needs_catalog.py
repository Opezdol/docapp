"""Тесты каталога расходки: таблицы в БД, журнал правок и YAML (ADR-0019).

Каталог переехал из файла в БД. Файл остался двумя ролями, и обе проверяются
здесь: первичный seed (пустая БД наполняется из `data/needs/catalog.yaml`) и
выгрузка «резервная копия глазами». Чтение каталога обязано вести себя как
прежний файловый каталог — на этом интерфейсе стоят сервис, отчёты и аналитика.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from docapp.core import access
from docapp.domain.employee import HEAD, HEAD_NURSE, NURSE
from docapp.needs import catalog_yaml
from docapp.needs.catalog import (
    CATEGORY_LABELS,
    CATEGORY_MEDICAMENTS,
    CATEGORY_SOLUTIONS,
    SOLUTIONS_GROUP,
    CatalogItem,
    category_of,
)
from docapp.needs.catalog_store import (
    ACTION_DELETE,
    ACTION_IMPORT,
    ACTION_UPSERT,
    ENTITY_CATALOG,
    ENTITY_ITEM,
    CatalogError,
    SqliteCatalog,
)
from docapp.needs.service import NeedsForbidden, NeedsService
from docapp.needs.store import SqliteNeedsStore
from factories import catalog as catalog_with_seed
from factories import test_db

VALID_YAML = """\
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

SEED_PATH = Path(__file__).resolve().parents[1] / "data" / "needs" / "catalog.yaml"


@pytest.fixture
def cat(tmp_path):
    """Каталог в БД, наполненный VALID_YAML (как после первого старта)."""
    c = catalog_with_seed(test_db(tmp_path), VALID_YAML)
    yield c
    c.close()


@pytest.fixture
def empty_cat(tmp_path):
    """Каталог в пустой БД — до seed-импорта."""
    c = SqliteCatalog(test_db(tmp_path))
    yield c
    c.close()


# ── YAML: разбор ──────────────────────────────────────────────────────


class TestYamlParse:
    def test_valid(self):
        data = catalog_yaml.parse(VALID_YAML)
        assert data.bases == {"Ленская": ["травма", "урология"], "Таймырская": ["экстренная", "гной"]}
        assert data.groups["Растворы"] == {"Физ 200/250": "фл", "Рингер": "фл"}

    def test_order_is_kept(self):
        """Порядок баз, групп и позиций значим — в нём каталог показывается."""
        data = catalog_yaml.parse(VALID_YAML)
        assert list(data.bases) == ["Ленская", "Таймырская"]
        assert list(data.groups) == ["Неспецифика", "Растворы"]
        assert list(data.groups["Неспецифика"]) == ["Атропин", "Дексаметазон"]

    def test_empty_file_is_empty_catalog(self):
        data = catalog_yaml.parse("")
        assert data.bases == {} and data.groups == {}

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("groups: 123\n", "groups"),
            ("bases: [1, 2]\n", "bases"),
            ("bases:\n  Ленская: []\n", "Ленская"),
            ("groups:\n  Неспецифика:\n    Атропин: 5\n", "Атропин"),
            ("bases: [unclosed\n", "неверный YAML"),
        ],
    )
    def test_invalid_structures_are_rejected(self, text, expected):
        with pytest.raises(ValueError, match=expected):
            catalog_yaml.parse(text)

    def test_error_mentions_source(self):
        """В сообщении видно, какой файл правили."""
        with pytest.raises(ValueError, match="catalog.yaml"):
            catalog_yaml.parse("groups: 123\n", source="catalog.yaml")

    def test_seed_file_is_valid(self):
        """Реальный seed-файл: обе базы по 6 точек, все группы непустые."""
        data = catalog_yaml.parse_file(SEED_PATH)
        assert set(data.bases) == {"Ленская", "Таймырская"}
        assert len(data.bases["Ленская"]) == 6
        assert len(data.bases["Таймырская"]) == 6
        assert data.groups, "в seed-каталоге должны быть группы"
        for group, items in data.groups.items():
            assert group.strip() and items, f"группа «{group}» должна быть непустой"
            for name, unit in items.items():
                assert name.strip() and isinstance(unit, str)


# ── YAML: выгрузка ────────────────────────────────────────────────────


class TestYamlDump:
    def test_round_trip(self, cat):
        """Выгруженный YAML читается обратно в тот же каталог."""
        text = catalog_yaml.dump(cat.export_data())
        data = catalog_yaml.parse(text)
        assert data.bases == cat.bases()
        assert data.groups == cat.groups()

    def test_dump_has_header_warning(self, cat):
        text = catalog_yaml.dump(cat.export_data())
        assert "Источник правды" in text
        assert SOLUTIONS_GROUP in text and "НЕ переименовывать" in text


# ── чтение из БД ──────────────────────────────────────────────────────


class TestRead:
    def test_seeded_catalog_reads_like_before(self, cat):
        assert cat.bases()["Ленская"] == ["травма", "урология"]
        assert cat.points("Ленская") == ["травма", "урология"]
        assert cat.points("Нет такой базы") == []
        assert cat.groups()["Неспецифика"] == {"Атропин": "амп", "Дексаметазон": "амп"}
        items = cat.all_items()
        assert len(items) == 4
        assert CatalogItem("Атропин", "амп", "Неспецифика") in items
        assert CatalogItem("Рингер", "фл", "Растворы") in items
        assert cat.unit_of("Атропин") == "амп"
        assert cat.unit_of("Неизвестный") == ""

    def test_order_survives_the_move_to_db(self, cat):
        assert list(cat.bases()) == ["Ленская", "Таймырская"]
        assert list(cat.groups()) == ["Неспецифика", "Растворы"]
        assert [item.name for item in cat.all_items()] == [
            "Атропин", "Дексаметазон", "Физ 200/250", "Рингер",
        ]

    def test_search_is_case_insensitive(self, cat):
        assert [item.name for item in cat.search("атро")] == ["Атропин"]
        assert [item.name for item in cat.search("АТРОПИН")] == ["Атропин"]
        assert cat.search("несуществующий-препарат") == []
        assert cat.search("") == [] and cat.search("   ") == []

    def test_solutions_items(self, cat):
        sol = cat.solutions_items()
        assert {item.name for item in sol} == {"Физ 200/250", "Рингер"}
        assert all(item.group == SOLUTIONS_GROUP for item in sol)

    def test_empty_catalog(self, empty_cat):
        assert empty_cat.is_empty() is True
        assert empty_cat.bases() == {} and empty_cat.groups() == {}
        assert empty_cat.all_items() == [] and empty_cat.search("атро") == []

    def test_seeded_catalog_is_not_empty(self, cat):
        assert cat.is_empty() is False


# ── правка каталога и журнал ──────────────────────────────────────────


class TestWrites:
    def test_upsert_new_item_creates_group(self, cat):
        message = cat.upsert_item("Новая группа", "Новокаин", "амп", employee_id=3)
        assert "Добавлено" in message
        assert cat.groups()["Новая группа"] == {"Новокаин": "амп"}
        assert cat.unit_of("Новокаин") == "амп"

    def test_upsert_existing_item_changes_unit(self, cat):
        message = cat.upsert_item("Неспецифика", "Атропин", "фл", employee_id=3)
        assert message == "Изменено: Неспецифика / Атропин (амп → фл)"
        assert cat.unit_of("Атропин") == "фл"
        entry = cat.audit()[0]
        assert entry["action"] == ACTION_UPSERT
        assert entry["entity"] == ENTITY_ITEM
        assert entry["entity_key"] == "Неспецифика/Атропин"
        assert entry["details"] == "единица «амп» → «фл»"
        assert entry["employee_id"] == 3 and entry["who"] == "Тестов3 Тест"

    def test_noop_upsert_writes_nothing(self, cat):
        before = len(cat.audit())
        message = cat.upsert_item("Неспецифика", "Атропин", "амп", employee_id=3)
        assert "Без изменений" in message
        assert len(cat.audit()) == before

    def test_delete_item_is_logged(self, cat):
        message = cat.delete_item("Растворы", "Рингер", employee_id=3)
        assert "Удалено" in message
        assert cat.unit_of("Рингер") == ""
        entry = cat.audit()[0]
        assert entry["action"] == ACTION_DELETE
        assert entry["entity_key"] == "Растворы/Рингер"

    def test_delete_unknown_item_raises(self, cat):
        with pytest.raises(CatalogError, match="Нет такого"):
            cat.delete_item("Растворы", "Нет такого", employee_id=3)

    def test_point_add_and_delete(self, cat):
        assert "Добавлено" in cat.add_point("Ленская", "новое место", employee_id=3)
        assert cat.points("Ленская") == ["травма", "урология", "новое место"]
        assert "Удалено" in cat.delete_point("Ленская", "новое место", employee_id=3)
        assert cat.points("Ленская") == ["травма", "урология"]
        assert [e["entity"] for e in cat.audit()[:2]] == ["point", "point"]

    def test_point_for_new_base_creates_base(self, cat):
        cat.add_point("Новая база", "точка", employee_id=3)
        assert list(cat.bases()) == ["Ленская", "Таймырская", "Новая база"]

    def test_empty_names_rejected(self, cat):
        with pytest.raises(CatalogError):
            cat.upsert_item("", "Новокаин", "амп")
        with pytest.raises(CatalogError):
            cat.upsert_item("Группа", "  ", "амп")
        with pytest.raises(CatalogError):
            cat.add_point("Ленская", "")

    def test_audit_shows_system_actions(self, cat):
        """Импорт без человека (seed, перенос) — в журнале «система»."""
        entry = cat.audit()[-1]
        assert entry["action"] == ACTION_IMPORT
        assert entry["entity"] == ENTITY_CATALOG
        assert entry["who"] == "система"
        assert entry["employee_id"] is None


class TestImport:
    def test_import_replaces_catalog(self, cat):
        """Файл описывает каталог целиком: после импорта в БД ровно то, что в нём."""
        data = catalog_yaml.parse("bases:\n  Одна: [т1]\ngroups:\n  Группа:\n    Позиция: шт\n")
        counts = cat.import_catalog(data, None, source="тест.yaml")

        assert counts == {"bases": 1, "points": 1, "groups": 1, "items": 1}
        assert list(cat.bases()) == ["Одна"]
        assert cat.unit_of("Атропин") == ""          # прежних позиций нет
        entry = cat.audit()[0]
        assert entry["action"] == ACTION_IMPORT
        assert "тест.yaml" in entry["details"]

    def test_import_is_repeatable(self, cat):
        data = catalog_yaml.parse(VALID_YAML)
        cat.import_catalog(data, None, source="тест.yaml")
        cat.import_catalog(data, None, source="тест.yaml")

        assert len(cat.all_items()) == 4            # не удвоилось
        assert len(cat.audit()) == 3                # seed + два импорта

    def test_seed_import_fills_empty_db(self, empty_cat):
        data = catalog_yaml.parse_file(SEED_PATH)
        counts = empty_cat.import_catalog(data, None, source=str(SEED_PATH))
        assert counts["bases"] == 2 and counts["points"] == 12
        assert empty_cat.is_empty() is False
        assert empty_cat.groups()[SOLUTIONS_GROUP]


# ── сервис: права и защита особой группы ──────────────────────────────


class TestServiceCatalog:
    @pytest.fixture
    def service(self, tmp_path):
        db = test_db(tmp_path)
        store = SqliteNeedsStore(db)
        yield NeedsService(store, catalog_with_seed(db, VALID_YAML))
        store.close_conn()

    def test_head_nurse_and_head_may_edit(self, service):
        assert "Добавлено" in service.catalog_upsert(100, HEAD_NURSE, "Группа", "Позиция", "шт")
        assert service.catalog_upsert is not None
        assert access.has(HEAD, access.NEEDS_CATALOG)
        assert "Изменено" in service.catalog_upsert(200, HEAD, "Группа", "Позиция", "пач")

    def test_nurse_may_not_edit(self, service):
        with pytest.raises(NeedsForbidden):
            service.catalog_upsert(7, NURSE, "Группа", "Позиция", "шт")
        with pytest.raises(NeedsForbidden):
            service.catalog_delete_item(7, NURSE, "Неспецифика", "Атропин")
        with pytest.raises(NeedsForbidden):
            service.catalog_audit(NURSE)

    def test_solutions_group_name_is_protected(self, service):
        """«растворы» другим написанием завело бы вторую группу и сломало раздел."""
        with pytest.raises(ValueError, match=SOLUTIONS_GROUP):
            service.catalog_upsert(100, HEAD_NURSE, "растворы", "Физ 200/250", "фл")
        assert len(service._catalog.groups()) == 2

    def test_export_from_service_is_yaml(self, service):
        text = service.catalog_export()
        assert catalog_yaml.parse(text).bases == service._catalog.bases()

    def test_audit_sees_who_changed(self, service):
        service.catalog_upsert(100, HEAD_NURSE, "Неспецифика", "Атропин", "фл")
        entry = service.catalog_audit(HEAD_NURSE)[0]
        assert entry["who"] == "Тестов100 Тест"


# ── понятия каталога (не изменились переездом) ────────────────────────


def test_category_constants():
    assert CATEGORY_SOLUTIONS == "solutions"
    assert CATEGORY_MEDICAMENTS == "medicaments"
    assert CATEGORY_LABELS == {
        CATEGORY_SOLUTIONS: "Растворы",
        CATEGORY_MEDICAMENTS: "Медикаменты",
    }


def test_category_of():
    assert category_of("Растворы") == CATEGORY_SOLUTIONS
    assert category_of("Неспецифика") == CATEGORY_MEDICAMENTS
    assert category_of("Медикаменты") == CATEGORY_MEDICAMENTS
    assert category_of("") == CATEGORY_MEDICAMENTS
