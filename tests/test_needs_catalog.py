"""Тесты каталога потребностей: структура, поиск, авто-перечитывание.

Реальные файлы не трогаем — временные YAML на tmp_path фикстурах.
Единственное исключение — структурный тест seed-файла data/needs/catalog.yaml
(без жёстких количеств позиций: файл может легально меняться).
"""

from pathlib import Path

import pytest

from docapp.needs.catalog import SOLUTIONS_GROUP, Catalog, CatalogItem

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


def write_catalog(path: Path, text: str) -> None:
    """Перезаписать временный YAML-файл каталога."""
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def catalog_path(tmp_path):
    """Путь к временному YAML-файлу каталога (ещё не создан)."""
    return tmp_path / "catalog.yaml"


@pytest.fixture
def filled_catalog(catalog_path):
    """Каталог, загруженный из валидного YAML."""
    write_catalog(catalog_path, VALID_YAML)
    return Catalog(catalog_path)


def test_valid_catalog_structure(filled_catalog):
    """a) Валидный YAML: базы, точки, группы, позиции и единицы корректны."""
    c = filled_catalog
    assert c.bases()["Ленская"] == ["травма", "урология"]
    assert c.bases()["Таймырская"] == ["экстренная", "гной"]
    assert c.points("Ленская") == ["травма", "урология"]
    assert c.points("Нет такой базы") == []
    assert c.groups()["Неспецифика"] == {"Атропин": "амп", "Дексаметазон": "амп"}
    items = c.all_items()
    assert len(items) == 4
    assert CatalogItem("Атропин", "амп", "Неспецифика") in items
    assert CatalogItem("Рингер", "фл", "Растворы") in items
    assert c.unit_of("Атропин") == "амп"


def test_search_case_insensitive(filled_catalog):
    """b) search('атро') находит «Атропин»; неизвестное и пустой q -> []."""
    c = filled_catalog
    hits = c.search("атро")
    assert [item.name for item in hits] == ["Атропин"]
    assert [item.name for item in c.search("АТРОПИН")] == ["Атропин"]
    assert c.search("несуществующий-препарат") == []
    assert c.search("") == []
    assert c.search("   ") == []


def test_missing_file_empty_catalog(catalog_path):
    """c) Файла нет — пустой каталог, исключений нет."""
    c = Catalog(catalog_path)
    assert c.bases() == {}
    assert c.groups() == {}
    assert c.all_items() == []
    assert c.points("Ленская") == []
    assert c.unit_of("Атропин") == ""
    assert c.search("атро") == []


def test_broken_yaml_keeps_last_good(filled_catalog, catalog_path):
    """d) Битый YAML после валидного: остаётся последняя рабочая версия."""
    c = filled_catalog
    before = c.all_items()
    # Незакрытая flow-последовательность — гарантированная ошибка парсинга YAML.
    write_catalog(catalog_path, "bases: [unclosed\n")
    assert c.all_items() == before
    assert c.bases()["Ленская"] == ["травма", "урология"]
    assert c.unit_of("Атропин") == "амп"


def test_broken_structure_keeps_last_good(filled_catalog, catalog_path):
    """d') Валидный YAML с невалидной структурой: тоже последняя рабочая версия."""
    c = filled_catalog
    write_catalog(catalog_path, "bases: [1, 2]\ngroups: 'а не словарь'\n")
    assert c.groups()["Растворы"] == {"Физ 200/250": "фл", "Рингер": "фл"}
    assert c.points("Ленская") == ["травма", "урология"]


def test_auto_reload_on_change(filled_catalog, catalog_path):
    """e) Изменение файла подхватывается автоматически (mtime)."""
    c = filled_catalog
    assert c.unit_of("Новокаин") == ""
    write_catalog(catalog_path, VALID_YAML + "  Медикаменты:\n    Новокаин: амп\n")
    assert c.unit_of("Новокаин") == "амп"
    assert any(item.name == "Новокаин" for item in c.all_items())


def test_unit_of_known_and_unknown(filled_catalog):
    """f) unit_of: известная единица и '' для неизвестного препарата."""
    assert filled_catalog.unit_of("Атропин") == "амп"
    assert filled_catalog.unit_of("Рингер") == "фл"
    assert filled_catalog.unit_of("Неизвестный") == ""


def test_solutions_group_constant(filled_catalog):
    """g) SOLUTIONS_GROUP == 'Растворы'; solutions_items отдают только её."""
    assert SOLUTIONS_GROUP == "Растворы"
    sol = filled_catalog.solutions_items()
    assert {item.name for item in sol} == {"Физ 200/250", "Рингер"}
    assert all(item.group == SOLUTIONS_GROUP for item in sol)
    assert all(item.unit == "фл" for item in sol)


def test_seed_catalog_structure():
    """h) Структурный тест реального seed-файла каталога.

    Обе базы по 6 точек, все группы непустые. Количества позиций
    не проверяем — файл может легально меняться.
    """
    seed = Path(__file__).resolve().parents[1] / "data" / "needs" / "catalog.yaml"
    c = Catalog(seed)
    bases = c.bases()
    assert set(bases) == {"Ленская", "Таймырская"}
    assert len(bases["Ленская"]) == 6
    assert len(bases["Таймырская"]) == 6
    groups = c.groups()
    assert groups, "в seed-каталоге должны быть группы"
    for group, items in groups.items():
        assert group.strip(), "имя группы не может быть пустым"
        assert items, f"группа «{group}» не должна быть пустой"
        for name, unit in items.items():
            assert name.strip(), f"в группе «{group}» есть препарат с пустым именем"
            assert isinstance(unit, str)


def test_invalid_initial_catalog_raises(catalog_path):
    """Первичная загрузка невалидной структуры бросает ValueError."""
    write_catalog(catalog_path, "bases: {}\ngroups: 123\n")
    with pytest.raises(ValueError, match="groups"):
        Catalog(catalog_path)


def test_empty_base_points_invalid(catalog_path):
    """Пустой список точек пополнения у базы — ValueError."""
    write_catalog(catalog_path, "bases:\n  Ленская: []\ngroups: {}\n")
    with pytest.raises(ValueError, match="Ленская"):
        Catalog(catalog_path)
