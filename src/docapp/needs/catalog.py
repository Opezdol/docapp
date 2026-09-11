"""Каталог расходки: что считают «раствором», «медикаментом» и как называются позиции.

Здесь остались только понятия каталога — разделы, группы, позиция. Сам каталог
живёт в БД (`catalog_store.SqliteCatalog`, ADR-0019), а YAML — в
`catalog_yaml` (первичный seed и выгрузка глазами).

Разделы «Потребностей» (ТЗ-растворы-медикаменты): граница выводится из каталога —
группа «Растворы» = раздел «растворы», все остальные группы = «медикаменты».
Внутренние значения английские (хранятся в БД и API).
"""

from dataclasses import dataclass

#: Особая группа: в отчёте для аптеки идёт поточково — НЕ переименовывать.
SOLUTIONS_GROUP = "Растворы"

#: Разделы «Потребностей».
CATEGORY_SOLUTIONS = "solutions"
CATEGORY_MEDICAMENTS = "medicaments"

#: Русские подписи разделов для UI, имён файлов и сообщений.
CATEGORY_LABELS = {
    CATEGORY_SOLUTIONS: "Растворы",
    CATEGORY_MEDICAMENTS: "Медикаменты",
}


def category_of(grp: str) -> str:
    """Раздел по названию группы: «Растворы» → solutions, иначе medicaments."""
    return CATEGORY_SOLUTIONS if grp == SOLUTIONS_GROUP else CATEGORY_MEDICAMENTS


@dataclass(frozen=True)
class CatalogItem:
    """Позиция каталога: препарат, единица измерения, группа."""

    name: str
    unit: str
    group: str
