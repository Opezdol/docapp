"""Проверка единой БД: счётчики, версии схем и целостность (шаг 4b).

`docapp check` отвечает на два вопроса: «сколько чего лежит» и «не потерялось ли
что-нибудь при переезде». Сироты (ссылки на несуществующих сотрудников) —
единственный автоматический признак потери: внешние ключи их не пустят при
записи, но старые базы переносились в обход, поэтому проверяем явно.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from docapp.core.db import SCHEMA_TABLE
from docapp.legacy.importer import find_orphans

#: Таблицы по модулям — для счётчиков (порядок как в реестре модулей).
MODULE_TABLES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("people", ("employees",)),
    ("records", ("anesthesia", "active_nurse", "accrual")),
    (
        "wiki",
        ("wiki_sources", "wiki_articles", "wiki_revisions", "wiki_article_links",
         "wiki_messages", "wiki_settings"),
    ),
    (
        "needs",
        ("needs_requests", "needs_request_lines", "needs_closures",
         "needs_catalog_bases", "needs_catalog_points", "needs_catalog_groups",
         "needs_catalog_items", "needs_catalog_audit"),
    ),
    ("duty", ("duty_reports", "duty_operations")),
)


@dataclass
class CheckReport:
    """Итог проверки: счётчики, версии схем, найденные проблемы."""

    db_path: Path
    counts: dict[str, int] = field(default_factory=dict)
    versions: dict[str, int] = field(default_factory=dict)
    orphans: dict[str, list[int]] = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.orphans and not self.issues

    def summary(self) -> str:
        lines = [f"Единая БД: {self.db_path}", ""]
        for module, tables in MODULE_TABLES:
            version = self.versions.get(module)
            header = f"[{module}]" + (f" схема v{version}" if version else " схема: нет")
            lines.append(header)
            for table in tables:
                if table in self.counts:
                    lines.append(f"  {table:<22} {self.counts[table]:>6}")
                else:
                    lines.append(f"  {table:<22}      — (таблицы нет)")
        if self.orphans:
            lines.append("")
            lines.append("СИРОТЫ (ссылки на несуществующих сотрудников):")
            for where, ids in self.orphans.items():
                lines.append(f"  {where}: {ids}")
        if self.issues:
            lines.append("")
            lines.append("ЗАМЕЧАНИЯ:")
            lines.extend(f"  - {issue}" for issue in self.issues)
        lines.append("")
        lines.append("Итог: " + ("всё сходится" if self.ok else "есть замечания"))
        return "\n".join(lines)


def check(db_path: str | Path) -> CheckReport:
    """Собрать счётчики и проверить целостность единой БД."""
    path = Path(db_path)
    report = CheckReport(db_path=path)
    if not path.exists():
        report.issues.append(f"файла нет: {path}")
        return report

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        existing = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        for _, tables in MODULE_TABLES:
            for table in tables:
                if table in existing:
                    report.counts[table] = conn.execute(
                        f"SELECT COUNT(*) AS c FROM {table}"
                    ).fetchone()["c"]

        if SCHEMA_TABLE in existing:
            for row in conn.execute(
                f"SELECT module, version FROM {SCHEMA_TABLE}"
            ).fetchall():
                report.versions[row["module"]] = row["version"]

        report.orphans = find_orphans(conn)

        # строки без своей заявки (при включённых ключах это невозможно, но
        # перенос шёл в обход — проверка стоит копейки)
        if "needs_request_lines" in existing and "needs_requests" in existing:
            orphans = conn.execute(
                "SELECT COUNT(*) AS c FROM needs_request_lines l "
                "LEFT JOIN needs_requests r ON r.id = l.request_id "
                "WHERE r.id IS NULL"
            ).fetchone()["c"]
            if orphans:
                report.issues.append(f"строк заявок без заявки: {orphans}")

        # заявки без строк: это допустимо (пустой черновик), но полезно знать
        if "needs_requests" in existing:
            empty = conn.execute(
                "SELECT COUNT(*) AS c FROM needs_requests r "
                "LEFT JOIN needs_request_lines l ON l.request_id = r.id "
                "WHERE l.id IS NULL"
            ).fetchone()["c"]
            if empty:
                report.issues.append(f"заявок без строк (пустые черновики): {empty}")

        # операции без отчёта (тоже невозможно при ключах, проверяем явно)
        if "duty_operations" in existing and "duty_reports" in existing:
            lost = conn.execute(
                "SELECT COUNT(*) AS c FROM duty_operations o "
                "LEFT JOIN duty_reports r ON r.id = o.report_id "
                "WHERE r.id IS NULL"
            ).fetchone()["c"]
            if lost:
                report.issues.append(f"операций дежурств без отчёта: {lost}")
    finally:
        conn.close()
    return report
