"""Проверка единой БД: счётчики, версии схем и целостность (шаг 4b).

`docapp check` отвечает на три вопроса: «сколько чего лежит», «не потерялось ли
что-нибудь при переезде» и «не отстала ли схема файла от кода». Сироты (ссылки на
несуществующих сотрудников) — единственный автоматический признак потери: внешние
ключи их не пустят при записи, но старые базы переносились в обход, поэтому
проверяем явно.

Перечень таблиц и ожидаемые версии берутся из реестра модулей (ADR-0016,
ADR-0017), а не из списка в этом файле: ручной список разошёлся с кодом — модуль
`signatures` в него не попал, и проверка о нём молчала.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from docapp.core.db import SCHEMA_TABLE, declared_tables, shape_problems
from docapp.legacy.importer import find_orphans


def _registry() -> tuple[tuple[str, int, tuple[str, ...]], ...]:
    """Модуль, версия его схемы и объявленные таблицы — по реестру модулей.

    Модуль без схемы (`scales`, `distribution`) в список не попадает: у него нет
    ни таблиц, ни версии. Импорт `docapp.modules` внутри функции — чтобы проверка
    не тянула за собой все роутеры при импорте `docapp.legacy`.
    """
    from docapp.modules import MODULES

    return tuple(
        (module.name, module.schema.version, tuple(declared_tables(module.schema)))
        for module in MODULES
        if module.schema is not None
    )


@dataclass
class CheckReport:
    """Итог проверки: счётчики, версии схем, найденные проблемы."""

    db_path: Path
    tables: tuple[tuple[str, tuple[str, ...]], ...] = ()
    expected: dict[str, int] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    versions: dict[str, int] = field(default_factory=dict)
    orphans: dict[str, list[int]] = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.orphans and not self.issues

    def summary(self) -> str:
        lines = [f"Единая БД: {self.db_path}", ""]
        for module, tables in self.tables:
            version = self.versions.get(module)
            expected = self.expected.get(module)
            if version is None:
                # Строки версии нет: модуль к этому файлу ещё не применялся.
                header = f"[{module}] схема не применялась"
                if expected:
                    header += f" (в коде v{expected})"
            elif expected and version != expected:
                header = f"[{module}] схема v{version}, в коде v{expected}"
            else:
                header = f"[{module}] схема v{version}"
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


def shape_issues(conn: sqlite3.Connection) -> list[str]:
    """Таблицы, форма которых осталась от прежней схемы.

    `CREATE TABLE IF NOT EXISTS` такую таблицу не заменит, а запись в неё
    упадёт (обычно на старой колонке NOT NULL, которую код уже не заполняет).
    Проверка ловит это до того, как пользователь нажмёт «Сохранить».
    """
    from docapp.modules import MODULES

    issues: list[str] = []
    for module in MODULES:
        if module.schema is None:
            continue
        for problem in shape_problems(conn, module.schema):
            issues.append(f"[{module.name}] {problem}")
    return issues


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
        registry = _registry()
        report.tables = tuple((name, tables) for name, _, tables in registry)
        report.expected = {name: version for name, version, _ in registry}
        for _, _, tables in registry:
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

        # Версия файла против версии кода: отставшая база — это не «всё
        # сходится», а не пройденные миграции, и узнать об этом надо до первой
        # записи, а не по 500 на форме.
        for name, expected, tables in registry:
            actual = report.versions.get(name)
            if actual is None:
                # Модуль к файлу ещё не применялся (новая база до первого
                # запуска) — об этом говорит заголовок в сводке, не замечание.
                continue
            if actual < expected:
                report.issues.append(
                    f"[{name}] схема v{actual}, в коде v{expected} — нужен docapp migrate"
                )
            elif actual > expected:
                report.issues.append(
                    f"[{name}] схема v{actual} выше кода v{expected} — "
                    "код откатили, база новее"
                )
            missing = [table for table in tables if table not in existing]
            if missing:
                report.issues.append(
                    f"[{name}] нет объявленных таблиц ({', '.join(missing)}), "
                    f"хотя схема v{actual} записана — лечится docapp migrate"
                )

        report.orphans = find_orphans(conn)
        report.issues.extend(shape_issues(conn))

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
