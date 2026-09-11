"""Каталог расходки в БД (ADR-0019).

Источник правды — таблицы `needs_catalog_*`. Файл `catalog.yaml` остался
первичным seed'ом и выгрузкой (`catalog_yaml`), а не рабочим источником:
каталог правится из интерфейса, и на вопрос «кто убрал позицию из расходки»
теперь отвечает журнал `needs_catalog_audit`.

Интерфейс чтения — тот же, что был у файлового каталога (`bases`, `points`,
`groups`, `all_items`, `search`, `unit_of`, `solutions_items`), поэтому сервис,
аналитика и отчёты о переезде не знают.

Запись всегда идёт вместе с записью в журнал — в одной транзакции: молчаливой
правки каталога быть не должно. Системные действия (первичный seed, перенос
прежних данных) пишутся с пустым `employee_id`.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from docapp.core.db import open_db
from docapp.needs.catalog import SOLUTIONS_GROUP, CatalogItem
from docapp.needs.catalog_yaml import CatalogData
from docapp.needs.store import SCHEMA, now_iso

#: Виды действий в журнале каталога.
ACTION_IMPORT = "import"
ACTION_UPSERT = "upsert"
ACTION_DELETE = "delete"

#: Виды объектов журнала: позиция, точка, база, группа и каталог целиком.
ENTITY_ITEM = "item"
ENTITY_POINT = "point"
ENTITY_BASE = "base"
ENTITY_GROUP = "group"
ENTITY_CATALOG = "catalog"


class CatalogError(ValueError):
    """Правка каталога невозможна (роутер отвечает 400)."""


class SqliteCatalog:
    """Каталог расходки из БД: чтение и правка с журналом."""

    def __init__(self, db_path: str | Path) -> None:
        self._conn = open_db(db_path, SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "SqliteCatalog":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ── чтение ────────────────────────────────────────────────────────

    def bases(self) -> dict[str, list[str]]:
        """Все базы: имя -> точки пополнения, в порядке вывода."""
        result: dict[str, list[str]] = {}
        for base in self._conn.execute(
            "SELECT name FROM needs_catalog_bases ORDER BY position, name"
        ).fetchall():
            result[base["name"]] = self.points(base["name"])
        return result

    def points(self, base: str) -> list[str]:
        """Точки пополнения базы; [] если базы нет."""
        rows = self._conn.execute(
            "SELECT name FROM needs_catalog_points WHERE base = ? ORDER BY position, name",
            (base,),
        ).fetchall()
        return [row["name"] for row in rows]

    def groups(self) -> dict[str, dict[str, str]]:
        """Все группы: название -> {препарат: единица}, в порядке вывода."""
        result: dict[str, dict[str, str]] = {}
        for group in self._conn.execute(
            "SELECT name FROM needs_catalog_groups ORDER BY position, name"
        ).fetchall():
            rows = self._conn.execute(
                "SELECT name, unit FROM needs_catalog_items WHERE grp = ? "
                "ORDER BY position, name",
                (group["name"],),
            ).fetchall()
            result[group["name"]] = {row["name"]: row["unit"] for row in rows}
        return result

    def all_items(self) -> list[CatalogItem]:
        """Все позиции каталога в порядке групп и позиций."""
        return [
            CatalogItem(name=name, unit=unit, group=group)
            for group, items in self.groups().items()
            for name, unit in items.items()
        ]

    def search(self, q: str) -> list[CatalogItem]:
        """Поиск по подстроке названия без учёта регистра; пустой q -> []."""
        needle = q.strip().lower()
        if not needle:
            return []
        return [item for item in self.all_items() if needle in item.name.lower()]

    def unit_of(self, name: str) -> str:
        """Единица измерения препарата; '' если позиции нет."""
        row = self._conn.execute(
            "SELECT unit FROM needs_catalog_items WHERE name = ? ORDER BY position LIMIT 1",
            (name,),
        ).fetchone()
        return row["unit"] if row else ""

    def solutions_items(self) -> list[CatalogItem]:
        """Позиции особой группы «Растворы» (в отчёте аптеки идут поточково)."""
        return [item for item in self.all_items() if item.group == SOLUTIONS_GROUP]

    def is_empty(self) -> bool:
        """Пуст ли каталог (нет ни баз, ни групп) — признак, что нужен seed."""
        bases = self._conn.execute(
            "SELECT COUNT(*) AS c FROM needs_catalog_bases"
        ).fetchone()["c"]
        groups = self._conn.execute(
            "SELECT COUNT(*) AS c FROM needs_catalog_groups"
        ).fetchone()["c"]
        return not bases and not groups

    def audit(self, limit: int = 50) -> list[dict]:
        """Последние записи журнала (кто, когда, что) — свежие сверху."""
        rows = self._conn.execute(
            "SELECT a.id, a.at, a.employee_id, a.action, a.entity, a.entity_key, "
            "a.details, e.last_name, e.first_name, e.middle_name "
            "FROM needs_catalog_audit a LEFT JOIN employees e ON e.id = a.employee_id "
            "ORDER BY a.id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {
                "id": row["id"],
                "at": row["at"],
                "employee_id": row["employee_id"],
                "who": _full_name(row) or "система",
                "action": row["action"],
                "entity": row["entity"],
                "entity_key": row["entity_key"],
                "details": row["details"],
            }
            for row in rows
        ]

    # ── журнал ────────────────────────────────────────────────────────

    def _log(
        self,
        action: str,
        entity: str,
        entity_key: str,
        details: str,
        employee_id: int | None,
    ) -> None:
        """Записать действие в журнал каталога (в той же транзакции)."""
        self._conn.execute(
            "INSERT INTO needs_catalog_audit "
            "(at, employee_id, action, entity, entity_key, details) VALUES (?, ?, ?, ?, ?, ?)",
            (now_iso(), employee_id, action, entity, entity_key, details),
        )

    # ── правка каталога ───────────────────────────────────────────────

    def upsert_item(
        self,
        group: str,
        name: str,
        unit: str,
        employee_id: int | None = None,
    ) -> str:
        """Добавить или изменить позицию; возвращает описание для ответа.

        Группа заводится автоматически, если её ещё нет: старшая сестра заводит
        позицию, а не «сначала группу».
        """
        group, name, unit = group.strip(), name.strip(), unit
        if not group:
            raise CatalogError("Название группы не может быть пустым")
        if not name:
            raise CatalogError("Название позиции не может быть пустым")
        existing = self._conn.execute(
            "SELECT unit, position FROM needs_catalog_items WHERE grp = ? AND name = ?",
            (group, name),
        ).fetchone()
        with self._conn:
            if existing is None:
                self._ensure_group(group)
                position = self._next_position("needs_catalog_items", "grp", group)
                self._conn.execute(
                    "INSERT INTO needs_catalog_items (grp, name, unit, position) "
                    "VALUES (?, ?, ?, ?)",
                    (group, name, unit, position),
                )
                self._log(
                    ACTION_UPSERT, ENTITY_ITEM, f"{group}/{name}",
                    f"новая позиция, единица «{unit}»", employee_id,
                )
                return f"Добавлено: {group} / {name} ({unit})"
            if existing["unit"] == unit:
                return f"Без изменений: {group} / {name} ({unit})"
            old = existing["unit"]
            self._conn.execute(
                "UPDATE needs_catalog_items SET unit = ? WHERE grp = ? AND name = ?",
                (unit, group, name),
            )
            self._log(
                ACTION_UPSERT, ENTITY_ITEM, f"{group}/{name}",
                f"единица «{old}» → «{unit}»", employee_id,
            )
            return f"Изменено: {group} / {name} ({old} → {unit})"

    def delete_item(self, group: str, name: str, employee_id: int | None = None) -> str:
        """Убрать позицию из каталога (история заявок не меняется — там снимки)."""
        row = self._conn.execute(
            "SELECT unit FROM needs_catalog_items WHERE grp = ? AND name = ?",
            (group.strip(), name.strip()),
        ).fetchone()
        if row is None:
            raise CatalogError(f"Позиции «{name}» в группе «{group}» нет")
        with self._conn:
            self._conn.execute(
                "DELETE FROM needs_catalog_items WHERE grp = ? AND name = ?",
                (group.strip(), name.strip()),
            )
            self._log(
                ACTION_DELETE, ENTITY_ITEM, f"{group}/{name}",
                f"позиция удалена (единица «{row['unit']}»)", employee_id,
            )
        return f"Удалено: {group} / {name}"

    def add_point(self, base: str, point: str, employee_id: int | None = None) -> str:
        """Добавить точку пополнения; база заводится автоматически."""
        base, point = base.strip(), point.strip()
        if not base or not point:
            raise CatalogError("База и точка пополнения не могут быть пустыми")
        exists = self._conn.execute(
            "SELECT 1 FROM needs_catalog_points WHERE base = ? AND name = ?", (base, point)
        ).fetchone()
        if exists is not None:
            return f"Без изменений: {base} / {point}"
        with self._conn:
            self._ensure_base(base)
            position = self._next_position("needs_catalog_points", "base", base)
            self._conn.execute(
                "INSERT INTO needs_catalog_points (base, name, position) VALUES (?, ?, ?)",
                (base, point, position),
            )
            self._log(
                ACTION_UPSERT, ENTITY_POINT, f"{base}/{point}", "точка добавлена", employee_id
            )
        return f"Добавлено: {base} / {point}"

    def delete_point(self, base: str, point: str, employee_id: int | None = None) -> str:
        """Убрать точку пополнения."""
        exists = self._conn.execute(
            "SELECT 1 FROM needs_catalog_points WHERE base = ? AND name = ?",
            (base.strip(), point.strip()),
        ).fetchone()
        if exists is None:
            raise CatalogError(f"Точки «{point}» у базы «{base}» нет")
        with self._conn:
            self._conn.execute(
                "DELETE FROM needs_catalog_points WHERE base = ? AND name = ?",
                (base.strip(), point.strip()),
            )
            self._log(
                ACTION_DELETE, ENTITY_POINT, f"{base}/{point}", "точка удалена", employee_id
            )
        return f"Удалено: {base} / {point}"

    def import_catalog(
        self,
        data: CatalogData,
        employee_id: int | None = None,
        *,
        source: str = "",
    ) -> dict:
        """Заменить каталог целиком (seed из YAML или перенос прежних данных).

        Замена, а не слияние: файл описывает каталог целиком, и после импорта в
        БД должно лежать ровно то, что в файле. Одна запись в журнале на импорт.
        """
        counts = {"bases": len(data.bases), "points": 0, "groups": len(data.groups), "items": 0}
        with self._conn:
            self._conn.execute("DELETE FROM needs_catalog_items")
            self._conn.execute("DELETE FROM needs_catalog_points")
            self._conn.execute("DELETE FROM needs_catalog_groups")
            self._conn.execute("DELETE FROM needs_catalog_bases")
            for base_position, (base, points) in enumerate(data.bases.items()):
                self._conn.execute(
                    "INSERT INTO needs_catalog_bases (name, position) VALUES (?, ?)",
                    (base, base_position),
                )
                for point_position, point in enumerate(points):
                    self._conn.execute(
                        "INSERT INTO needs_catalog_points (base, name, position) "
                        "VALUES (?, ?, ?)",
                        (base, point, point_position),
                    )
                    counts["points"] += 1
            for group_position, (group, items) in enumerate(data.groups.items()):
                self._conn.execute(
                    "INSERT INTO needs_catalog_groups (name, position) VALUES (?, ?)",
                    (group, group_position),
                )
                for item_position, (name, unit) in enumerate(items.items()):
                    self._conn.execute(
                        "INSERT INTO needs_catalog_items (grp, name, unit, position) "
                        "VALUES (?, ?, ?, ?)",
                        (group, name, unit, item_position),
                    )
                    counts["items"] += 1
            details = (
                f"источник «{source or 'не указан'}»: баз {counts['bases']}, "
                f"точек {counts['points']}, групп {counts['groups']}, позиций {counts['items']}"
            )
            self._log(ACTION_IMPORT, ENTITY_CATALOG, "", details, employee_id)
        return counts

    def export_data(self) -> CatalogData:
        """Каталог для выгрузки в YAML (порядок как в интерфейсе)."""
        return CatalogData(bases=self.bases(), groups=self.groups())

    # ── служебное ─────────────────────────────────────────────────────

    def _ensure_base(self, name: str) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO needs_catalog_bases (name, position) VALUES (?, ?)",
            (name, self._next_position("needs_catalog_bases", None, None)),
        )

    def _ensure_group(self, name: str) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO needs_catalog_groups (name, position) VALUES (?, ?)",
            (name, self._next_position("needs_catalog_groups", None, None)),
        )

    def _next_position(self, table: str, column: str | None, value: str | None) -> int:
        """Следующая позиция в конце списка (для стабильного порядка вывода)."""
        if column is None:
            row = self._conn.execute(
                f"SELECT COALESCE(MAX(position), -1) + 1 AS p FROM {table}"
            ).fetchone()
        else:
            row = self._conn.execute(
                f"SELECT COALESCE(MAX(position), -1) + 1 AS p FROM {table} WHERE {column} = ?",
                (value,),
            ).fetchone()
        return int(row["p"])


def _full_name(row: sqlite3.Row) -> str:
    """ФИО автора правки из строки журнала (пусто, если сотрудник удалён)."""
    parts = [row["last_name"], row["first_name"], row["middle_name"]]
    return " ".join(part for part in parts if part)
