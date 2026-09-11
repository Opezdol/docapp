"""YAML каталога расходки: первичный seed и выгрузка глазами (ADR-0019).

Источник правды — таблицы `needs_catalog_*` в БД. YAML остался для двух дел:

- **seed**: пустая БД (свежий клон, новый сервер) наполняется из
  `data/needs/catalog.yaml` при первом старте — иначе «Потребности» пусты, пока
  кто-то не заведёт расходку руками;
- **выгрузка**: `GET /needs/api/catalog/export` отдаёт текущий каталог файлом —
  резервная копия, которую можно прочитать глазами и сравнить.

Структура файла:

    bases:  база -> список точек пополнения
    groups: группа -> {название препарата: единица измерения}

Порядок баз, точек, групп и позиций значим (в этом порядке каталог показывается),
поэтому он сохраняется и в БД (`position`), и в выгрузке.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

#: Шапка выгрузки — предупреждения, которые в файле нужны человеку.
HEADER = """\
# Каталог «Потребностей». Источник правды — база данных (ADR-0019);
# этот файл — выгрузка для чтения глазами или первичный seed.
#
# ВАЖНО: группа «Растворы» особая (раздел «растворы», в отчёте для аптеки идёт
# поточково) — НЕ переименовывать.
# Единицы: растворы — «кор» (коробки), медикаменты — «пач» (пачки).
"""


@dataclass
class CatalogData:
    """Каталог целиком: базы с точками и группы с позициями, в порядке вывода."""

    bases: dict[str, list[str]] = field(default_factory=dict)
    groups: dict[str, dict[str, str]] = field(default_factory=dict)


def parse(text: str, *, source: str = "каталог") -> CatalogData:
    """Прочитать YAML каталога; невалидная структура — ValueError.

    `source` — откуда взят текст (путь или имя): попадает в сообщение об ошибке,
    чтобы человек понял, какой именно файл правил.
    """
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(f"Каталог {source}: неверный YAML: {exc}") from exc
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError(
            f"Каталог {source}: корень YAML должен быть словарём "
            f"с ключами bases и groups, получено {type(raw).__name__}"
        )
    bases_raw = raw.get("bases", {})
    groups_raw = raw.get("groups", {})
    if not isinstance(bases_raw, dict):
        raise ValueError(
            f"Каталог {source}: bases должен быть словарём "
            f"база -> список точек, получено {type(bases_raw).__name__}"
        )
    if not isinstance(groups_raw, dict):
        raise ValueError(
            f"Каталог {source}: groups должен быть словарём "
            f"группа -> препараты, получено {type(groups_raw).__name__}"
        )

    bases: dict[str, list[str]] = {}
    for base_name, points_raw in bases_raw.items():
        name = str(base_name).strip()
        if not name:
            raise ValueError(f"Каталог {source}: имя базы не может быть пустым")
        if not isinstance(points_raw, list) or not points_raw:
            raise ValueError(
                f"Каталог {source}: у базы «{name}» список точек "
                f"пополнения пуст или не является списком"
            )
        if not all(isinstance(point, str) for point in points_raw):
            raise ValueError(
                f"Каталог {source}: точки пополнения базы «{name}» должны быть строками"
            )
        bases[name] = [point.strip() for point in points_raw]

    groups: dict[str, dict[str, str]] = {}
    for group_name, items_raw in groups_raw.items():
        gname = str(group_name).strip()
        if not gname:
            raise ValueError(f"Каталог {source}: имя группы не может быть пустым")
        if not isinstance(items_raw, dict):
            raise ValueError(
                f"Каталог {source}: группа «{gname}» должна быть "
                f"словарём препарат -> единица, получено {type(items_raw).__name__}"
            )
        items: dict[str, str] = {}
        for item_name, unit in items_raw.items():
            iname = str(item_name).strip()
            if not iname:
                raise ValueError(
                    f"Каталог {source}: название препарата в группе «{gname}» "
                    f"не может быть пустым"
                )
            if not isinstance(unit, str):
                raise ValueError(
                    f"Каталог {source}: единица препарата «{iname}» должна быть "
                    f"строкой (пустая допустима), получено {type(unit).__name__}"
                )
            items[iname] = unit
        groups[gname] = items
    return CatalogData(bases=bases, groups=groups)


def parse_file(path: str | Path) -> CatalogData:
    """Прочитать каталог из файла (seed или выгруженный)."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"Каталог {path}: файл недоступен: {exc}") from exc
    return parse(text, source=str(path))


def dump(data: CatalogData) -> str:
    """Собрать YAML каталога для выгрузки: шапка + данные в порядке вывода."""
    body = yaml.safe_dump(
        {
            "bases": {base: list(points) for base, points in data.bases.items()},
            "groups": {
                group: {name: unit for name, unit in items.items()}
                for group, items in data.groups.items()
            },
        },
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    )
    return HEADER + "\n" + body
