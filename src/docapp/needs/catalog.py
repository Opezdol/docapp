"""Каталог потребностей: базы, точки пополнения, препараты по группам.

YAML-файл каталога правится вручную (без рестарта сервера): каждый
публичный метод проверяет mtime файла и перечитывает его при изменении.
Битый файл при перечитывании не роняет приложение — остаётся последняя
рабочая версия (warning в лог). Отсутствующий файл даёт пустой каталог.
"""

import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

#: Особая группа: в отчёте для аптеки идёт поточково — НЕ переименовывать.
SOLUTIONS_GROUP = "Растворы"


@dataclass(frozen=True)
class CatalogItem:
    """Позиция каталога: препарат, единица измерения, группа."""

    name: str
    unit: str
    group: str


class Catalog:
    """Каталог потребностей из YAML-файла с авто-перечитыванием по mtime.

    Структура файла (seed: data/needs/catalog.yaml):
      bases:  база -> список точек пополнения
      groups: группа -> {название препарата: единица измерения}

    Первичная загрузка невалидной структуры бросает ValueError; при
    перечитывании ошибки не бросаются — остаётся последняя рабочая версия.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._bases: dict[str, list[str]] = {}
        self._groups: dict[str, dict[str, str]] = {}
        #: mtime (ns) последней успешной загрузки; None — файла нет/не загружался.
        self._mtime_ns: int | None = None
        if path.exists():
            self._bases, self._groups = self._parse(path)
            self._mtime_ns = path.stat().st_mtime_ns
        else:
            logger.warning("Каталог потребностей %s не найден — каталог пуст", path)

    @staticmethod
    def _parse(path: Path) -> tuple[dict[str, list[str]], dict[str, dict[str, str]]]:
        """Прочитать и провалидировать каталог; невалидно — ValueError."""
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise ValueError(f"Каталог потребностей {path}: неверный YAML: {exc}") from exc
        if raw is None:
            raw = {}
        if not isinstance(raw, dict):
            raise ValueError(
                f"Каталог потребностей {path}: корень YAML должен быть словарём "
                f"с ключами bases и groups, получено {type(raw).__name__}"
            )
        bases_raw = raw.get("bases", {})
        groups_raw = raw.get("groups", {})
        if not isinstance(bases_raw, dict):
            raise ValueError(
                f"Каталог потребностей {path}: bases должен быть словарём "
                f"база -> список точек, получено {type(bases_raw).__name__}"
            )
        if not isinstance(groups_raw, dict):
            raise ValueError(
                f"Каталог потребностей {path}: groups должен быть словарём "
                f"группа -> препараты, получено {type(groups_raw).__name__}"
            )
        bases: dict[str, list[str]] = {}
        for base_name, points_raw in bases_raw.items():
            name = base_name.strip()
            if not name:
                raise ValueError(f"Каталог потребностей {path}: имя базы не может быть пустым")
            if not isinstance(points_raw, list) or not points_raw:
                raise ValueError(
                    f"Каталог потребностей {path}: у базы «{name}» список точек "
                    f"пополнения пуст или не является списком"
                )
            if not all(isinstance(point, str) for point in points_raw):
                raise ValueError(
                    f"Каталог потребностей {path}: точки пополнения базы "
                    f"«{name}» должны быть строками"
                )
            bases[name] = list(points_raw)
        groups: dict[str, dict[str, str]] = {}
        for group_name, items_raw in groups_raw.items():
            gname = group_name.strip()
            if not gname:
                raise ValueError(f"Каталог потребностей {path}: имя группы не может быть пустым")
            if not isinstance(items_raw, dict):
                raise ValueError(
                    f"Каталог потребностей {path}: группа «{gname}» должна быть "
                    f"словарём препарат -> единица, получено {type(items_raw).__name__}"
                )
            items: dict[str, str] = {}
            for item_name, unit in items_raw.items():
                iname = item_name.strip()
                if not iname:
                    raise ValueError(
                        f"Каталог потребностей {path}: название препарата в группе "
                        f"«{gname}» не может быть пустым"
                    )
                if not isinstance(unit, str):
                    raise ValueError(
                        f"Каталог потребностей {path}: единица препарата «{iname}» "
                        f"должна быть строкой (пустая допустима), получено {type(unit).__name__}"
                    )
                items[iname] = unit
            groups[gname] = items
        return bases, groups

    def _refresh(self) -> None:
        """Перечитать файл, если он изменился (по mtime); ошибки не бросает.

        Файл отсутствует/недоступен — каталог очищается (warning в лог).
        Ошибка парсинга или валидации при перечитывании — остаётся
        последняя рабочая версия (warning в лог, без исключений).
        """
        try:
            mtime_ns = self._path.stat().st_mtime_ns
        except OSError:
            mtime_ns = None
        if mtime_ns is None:
            if self._mtime_ns is not None:
                logger.warning(
                    "Каталог потребностей %s недоступен (файл отсутствует) — каталог пуст",
                    self._path,
                )
                self._bases, self._groups = {}, {}
                self._mtime_ns = None
            return
        if mtime_ns == self._mtime_ns:
            return
        try:
            bases, groups = self._parse(self._path)
        except ValueError as exc:
            logger.warning(
                "Каталог потребностей %s не перечитан (%s) — остаётся последняя рабочая версия",
                self._path,
                exc,
            )
            return
        self._bases, self._groups = bases, groups
        self._mtime_ns = mtime_ns

    def bases(self) -> dict[str, list[str]]:
        """Все базы: имя -> список точек пополнения (копия)."""
        self._refresh()
        return dict(self._bases)

    def points(self, base: str) -> list[str]:
        """Точки пополнения базы; [] если базы нет."""
        self._refresh()
        return list(self._bases.get(base, []))

    def groups(self) -> dict[str, dict[str, str]]:
        """Все группы: название -> {препарат: единица} (копия)."""
        self._refresh()
        return {group: dict(items) for group, items in self._groups.items()}

    def all_items(self) -> list[CatalogItem]:
        """Все позиции каталога в порядке файла."""
        self._refresh()
        return [
            CatalogItem(name=name, unit=unit, group=group)
            for group, items in self._groups.items()
            for name, unit in items.items()
        ]

    def search(self, q: str) -> list[CatalogItem]:
        """Поиск по подстроке названия без учёта регистра; пустой q -> []."""
        self._refresh()
        needle = q.strip().lower()
        if not needle:
            return []
        return [
            item
            for item in self.all_items()
            if needle in item.name.lower()
        ]

    def unit_of(self, name: str) -> str:
        """Единица измерения препарата (точное совпадение названия); '' если не найдено."""
        self._refresh()
        for items in self._groups.values():
            if name in items:
                return items[name]
        return ""

    def solutions_items(self) -> list[CatalogItem]:
        """Позиции особой группы «Растворы» (в отчёте аптеки идут поточково)."""
        self._refresh()
        return [
            CatalogItem(name=name, unit=unit, group=group)
            for group, items in self._groups.items()
            if group == SOLUTIONS_GROUP
            for name, unit in items.items()
        ]
