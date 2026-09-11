"""Одноразовый перенос данных с прежних баз на единую (ADR-0016, шаг 4b).

До переезда данные лежали в четырёх файлах и под другими именами:

    data/docapp.db          → employees, anesthesia, active_nurse
    data/needs/needs.db     → requests, request_lines, closures
    data/wiki/wiki.db       → sources, articles, revisions, article_links, messages, settings
    data/duty/duty.db       → duty_report, duty_operation

Здесь они читаются и складываются в единую БД под целевыми именами
(`needs_requests`, `wiki_sources`, `duty_reports`, …) с сохранением id: на id
ссылаются строки-дети, и менять их при переносе нельзя.

Особенности прежних баз, из-за которых переносчик читает колонки динамически:

- основная БД встречается с колонкой `history_number` (номер истории болезни),
  хотя схема её уже не содержит — колонка молча пропускается (минимизация, ADR-14);
- «Потребности» встречаются и версии v1 (без колонки `category`), и v2: если
  колонки нет, раздел выводится по строкам заявки — есть строка группы
  «Растворы» → solutions, иначе medicaments; закрытия тогда дублируются на оба
  раздела (то же правило, что было в миграции v2 модуля);
- у строк заявок нет `position` — он проставляется по порядку id (порядок формы).

Перенос идемпотентен: повторный прогон не плодит копии (upsert по ключу) и не
каскадит детей (ON DELETE не срабатывает, строки обновляются на месте).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from docapp.core.db import connect, open_db
from docapp.modules import MODULES

#: Имя раздела «растворы» в прежних данных (группа строки заявки).
SOLUTIONS_GROUP = "Растворы"
CATEGORY_SOLUTIONS = "solutions"
CATEGORY_MEDICAMENTS = "medicaments"

#: Ссылки на сотрудников: (таблица, колонка) — по ним ищутся сироты.
EMPLOYEE_REFERENCES: tuple[tuple[str, str], ...] = (
    ("anesthesia", "doctor_id"),
    ("anesthesia", "nurse_id"),
    ("active_nurse", "doctor_id"),
    ("active_nurse", "nurse_id"),
    ("accrual", "employee_id"),
    ("needs_requests", "author_id"),
    ("needs_closures", "closed_by"),
    ("duty_reports", "doctor_id"),
    ("wiki_sources", "uploaded_by"),
    ("wiki_articles", "created_by"),
    ("wiki_revisions", "edited_by"),
    ("wiki_messages", "employee_id"),
)


class LegacyImportError(RuntimeError):
    """Перенос невозможен: в прежних данных есть ссылки на несуществующих людей."""


@dataclass
class TableCount:
    """Сколько строк было в источнике и сколько стало в целевой таблице."""

    table: str
    source: int
    imported: int
    note: str = ""


@dataclass
class ImportReport:
    """Итог переноса: по таблицам и то, что пришлось обойти."""

    tables: list[TableCount] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    orphans: dict[str, list[int]] = field(default_factory=dict)

    def add(self, table: str, source: int, imported: int, note: str = "") -> None:
        self.tables.append(TableCount(table, source, imported, note))

    @property
    def total_source(self) -> int:
        return sum(t.source for t in self.tables)

    @property
    def total_imported(self) -> int:
        return sum(t.imported for t in self.tables)

    def summary(self) -> str:
        lines = [
            f"{'таблица':<26} {'было':>6} {'перенесено':>11}  примечание",
        ]
        for t in self.tables:
            lines.append(f"{t.table:<26} {t.source:>6} {t.imported:>11}  {t.note}")
        lines.append(
            f"{'ИТОГО':<26} {self.total_source:>6} {self.total_imported:>11}"
        )
        if self.skipped:
            lines.append("")
            lines.append("Пропущено:")
            lines.extend(f"  - {item}" for item in self.skipped)
        if self.orphans:
            lines.append("")
            lines.append("Ссылки на несуществующих сотрудников:")
            for where, ids in self.orphans.items():
                lines.append(f"  {where}: {ids}")
        return "\n".join(lines)


# ── низкоуровневые помощники ──────────────────────────────────────────


def _open_source(path: Path) -> sqlite3.Connection:
    """Открыть прежнюю базу для чтения.

    Соединение обычное, а не `mode=ro`: базы велись в WAL, а такую базу SQLite
    не открывает только для чтения без файла `-shm` («unable to open database
    file»). Пишем при этом только в целевую БД.
    """
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    """Колонки таблицы; [] если таблицы нет (прежние базы бывают разными)."""
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [row["name"] for row in rows]


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?", (table,)
    ).fetchone()
    return row is not None


def _rows(conn: sqlite3.Connection, table: str, columns: list[str]) -> list[sqlite3.Row]:
    """Строки таблицы только по существующим колонкам, в порядке id."""
    present = [c for c in columns if c in _columns(conn, table)]
    order = "id" if "id" in present else present[0]
    sql = f"SELECT {', '.join(present)} FROM {table} ORDER BY {order}"
    return conn.execute(sql).fetchall()


def _upsert(
    conn: sqlite3.Connection,
    table: str,
    columns: list[str],
    rows: list[tuple],
    key: tuple[str, ...],
) -> None:
    """Идемпотентная вставка: повторный прогон обновляет строки на месте.

    ON CONFLICT DO UPDATE (а не INSERT OR REPLACE) — чтобы не удалять строку:
    на неё могут ссылаться дети, а у них ON DELETE CASCADE.
    """
    if not rows:
        return
    placeholders = ", ".join("?" * len(columns))
    conflict = ", ".join(key)
    updatable = [c for c in columns if c not in key]
    sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"
    if updatable:
        sql += " ON CONFLICT({}) DO UPDATE SET {}".format(
            conflict, ", ".join(f"{c} = excluded.{c}" for c in updatable)
        )
    else:
        sql += f" ON CONFLICT({conflict}) DO NOTHING"
    conn.executemany(sql, rows)


def _copy_simple(
    src: sqlite3.Connection,
    dst: sqlite3.Connection,
    source_table: str,
    target_table: str,
    report: ImportReport,
    *,
    key: tuple[str, ...] = ("id",),
    note: str = "",
) -> None:
    """Перенести таблицу «как есть», пропуская колонки, которых нет в источнике.

    Колонки читаются динамически: прежние базы отличались от текущей схемы (в
    основной БД, например, оставался `history_number`). Колонка, которую
    источник не может заполнить и которая объявлена обязательной без значения по
    умолчанию, — остановка с внятным сообщением, а не тихая потеря данных.
    """
    if not _table_exists(src, source_table):
        report.add(target_table, 0, 0, "нет таблицы в источнике")
        return

    target_columns = _columns(dst, target_table)
    source_columns = _columns(src, source_table)
    columns = [c for c in target_columns if c in source_columns]

    unfillable = sorted(_required_columns(dst, target_table) - set(source_columns))
    if unfillable:
        raise LegacyImportError(
            f"{source_table} → {target_table}: в источнике нет обязательных колонок "
            f"{unfillable}. Перенос без них положил бы пустые значения."
        )

    rows = _rows(src, source_table, columns)
    payload = [tuple(row[column] for column in columns) for row in rows]

    before = dst.execute(f"SELECT COUNT(*) AS c FROM {target_table}").fetchone()["c"]
    _upsert(dst, target_table, columns, payload, key)
    dst.commit()

    dropped = [c for c in source_columns if c not in target_columns]
    note_text = note
    if dropped:
        dropped_note = f"пропущены колонки: {', '.join(dropped)}"
        note_text = f"{note}; {dropped_note}" if note else dropped_note
    report.add(target_table, len(rows), len(payload), note_text)


def _required_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    """Колонки, обязательные к заполнению: NOT NULL без значения по умолчанию."""
    return {
        row["name"]
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        if row["notnull"] and row["dflt_value"] is None
    }


# ── сироты ────────────────────────────────────────────────────────────


def find_orphans(conn: sqlite3.Connection) -> dict[str, list[int]]:
    """Ссылки на сотрудников, которых нет в `employees` (по целевым таблицам)."""
    employees = {r["id"] for r in conn.execute("SELECT id FROM employees").fetchall()}
    orphans: dict[str, list[int]] = {}
    for table, column in EMPLOYEE_REFERENCES:
        if not _table_exists(conn, table):
            continue
        ids = {
            r[0]
            for r in conn.execute(
                f"SELECT DISTINCT {column} FROM {table} WHERE {column} IS NOT NULL"
            ).fetchall()
        }
        missing = sorted(ids - employees)
        if missing:
            orphans[f"{table}.{column}"] = missing
    return orphans


# ── основной перенос ──────────────────────────────────────────────────


def import_legacy(
    target_db: str | Path,
    *,
    core_db: str | Path | None = None,
    needs_db: str | Path | None = None,
    wiki_db: str | Path | None = None,
    duty_db: str | Path | None = None,
    sources_dir: str | Path | None = None,
    skip_orphans: bool = False,
) -> ImportReport:
    """Перенести данные прежних баз в единую БД.

    Целевая БД создаётся/достраивается по схемам модулей (открывать её заранее
    не обязательно). Ссылки на несуществующих сотрудников — ошибка: с ними
    внешние ключи не дадут вставить строки, а молча терять данные нельзя.
    `skip_orphans=True` переносит всё остальное, потерянное попадает в отчёт.

    `sources_dir` — папка, куда переезжают PDF-источники «Компендиума» (ADR-0020);
    без неё берётся папка из настроек модуля.
    """
    from docapp.wiki.config import load_wiki_config

    target = Path(target_db)
    wiki_sources_dir = (
        Path(sources_dir) if sources_dir is not None else load_wiki_config().sources_dir
    )
    report = ImportReport()

    # целевая схема: как у приложения — по схеме каждого модуля
    for module in MODULES:
        if module.schema is not None:
            connection = open_db(target, module.schema)
            connection.close()

    dst = connect(target)
    try:
        if core_db is not None:
            _import_core(Path(core_db), dst, report)
        if needs_db is not None:
            _import_needs(Path(needs_db), dst, report, skip_orphans=skip_orphans)
        if wiki_db is not None:
            _import_wiki(Path(wiki_db), dst, report, sources_dir=wiki_sources_dir)
        if duty_db is not None:
            _import_duty(Path(duty_db), dst, report)

        report.orphans = find_orphans(dst)
    finally:
        dst.close()
    return report


def _import_core(source: Path, dst: sqlite3.Connection, report: ImportReport) -> None:
    if not source.exists():
        report.skipped.append(f"нет файла {source}")
        return
    src = _open_source(source)
    try:
        _copy_simple(src, dst, "employees", "employees", report, key=("id",))
        # сотрудники уже перенесены, дальше ссылки на них должны существовать
        for table, column in (
            ("anesthesia", "doctor_id"),
            ("anesthesia", "nurse_id"),
            ("active_nurse", "doctor_id"),
            ("active_nurse", "nurse_id"),
            ("accrual", "employee_id"),
        ):
            if _table_exists(src, table) and column in _columns(src, table):
                ids = [
                    row[0]
                    for row in src.execute(f"SELECT DISTINCT {column} FROM {table}")
                ]
                _guard_employees(dst, ids, f"Ядро: {table}.{column}")
        _copy_simple(src, dst, "anesthesia", "anesthesia", report, key=("id",))
        _copy_simple(src, dst, "active_nurse", "active_nurse", report, key=("doctor_id",))
        _copy_simple(src, dst, "accrual", "accrual", report, key=("employee_id", "month"))
    finally:
        src.close()


def _import_needs(
    source: Path,
    dst: sqlite3.Connection,
    report: ImportReport,
    *,
    skip_orphans: bool,
) -> None:
    """«Потребности»: и v2 (с колонкой category), и v1 (раздел выводится по строкам)."""
    if not source.exists():
        report.skipped.append(f"нет файла {source}")
        return
    src = _open_source(source)
    try:
        if not _table_exists(src, "requests"):
            report.skipped.append("needs: нет таблицы requests")
            return

        source_columns = _columns(src, "requests")
        has_category = "category" in source_columns
        request_rows = _rows(
            src,
            "requests",
            ["id", "base", "point", "week_start", "category", "author_id", "status",
             "created_at", "updated_at"],
        )

        # строки заявок: раздел заявки выводится по ним, если колонки category нет
        line_rows = (
            _rows(src, "request_lines", ["id", "request_id", "item", "unit", "grp", "qty"])
            if _table_exists(src, "request_lines")
            else []
        )
        solutions_requests = {
            row["request_id"]
            for row in line_rows
            if (row["grp"] or "") == SOLUTIONS_GROUP
        }

        def category_of(request_id: int, value: str | None) -> str:
            if has_category and value:
                return value
            return (
                CATEGORY_SOLUTIONS
                if request_id in solutions_requests
                else CATEGORY_MEDICAMENTS
            )

        orphan_ids = set(
            _guard_employees(
                dst,
                [row["author_id"] for row in request_rows],
                "«Потребности»: заявки ссылаются на несуществующих сотрудников",
                skip_orphans=skip_orphans,
            )
        )

        payload = []
        skipped_requests = 0
        for row in request_rows:
            if row["author_id"] in orphan_ids:
                skipped_requests += 1
                continue
            payload.append(
                (
                    row["id"],
                    row["base"],
                    row["point"],
                    row["week_start"],
                    category_of(row["id"], row["category"] if has_category else None),
                    row["author_id"],
                    row["status"],
                    row["created_at"],
                    row["updated_at"],
                )
            )
        _upsert(
            dst,
            "needs_requests",
            ["id", "base", "point", "week_start", "category", "author_id", "status",
             "created_at", "updated_at"],
            payload,
            ("id",),
        )
        note = "" if has_category else "раздел выведен по строкам (прежняя схема v1)"
        if skipped_requests:
            note = f"{note}; пропущено заявок без сотрудника: {skipped_requests}".strip("; ")
        report.add("needs_requests", len(request_rows), len(payload), note)

        # строки: position по порядку id внутри заявки
        line_payload = []
        positions: dict[int, int] = {}
        for row in line_rows:
            position = positions.get(row["request_id"], 0)
            positions[row["request_id"]] = position + 1
            line_payload.append(
                (row["id"], row["request_id"], row["item"], row["unit"] or "",
                 row["grp"] or "", row["qty"] or 0, position)
            )
        _upsert(
            dst,
            "needs_request_lines",
            ["id", "request_id", "item", "unit", "grp", "qty", "position"],
            line_payload,
            ("id",),
        )
        report.add("needs_request_lines", len(line_rows), len(line_payload))

        _import_closures(src, dst, report, has_category=has_category)
    finally:
        src.close()


def _unknown_employees(conn: sqlite3.Connection, ids) -> list[int]:
    """Каких из переданных сотрудников нет в целевой БД.

    Сверяемся именно с целевой БД: прежняя база «Потребностей» таблицы
    `employees` не знала вообще — сотрудники жили в основной базе.
    """
    known = {row[0] for row in conn.execute("SELECT id FROM employees").fetchall()}
    return sorted({i for i in ids if i is not None} - known)


def _guard_employees(
    conn: sqlite3.Connection, ids, where: str, *, skip_orphans: bool = False
) -> list[int]:
    """Проверить ссылки на сотрудников; по умолчанию остановить перенос.

    Без сотрудника строку не вставить (внешние ключи), а молча потерять отчёт
    дежурства или статью нельзя — поэтому по умолчанию ошибка с перечислением
    id. Пропуск реализован только там, где он осмыслен («Потребности»: заявка
    могла остаться от ушедшего врача).
    """
    missing = _unknown_employees(conn, ids)
    if missing and not skip_orphans:
        raise LegacyImportError(
            f"{where}: ссылки на несуществующих сотрудников: {missing}. "
            "Создайте их в БД или запустите перенос «Потребностей» с skip_orphans=True."
        )
    return missing


def _import_closures(
    src: sqlite3.Connection,
    dst: sqlite3.Connection,
    report: ImportReport,
    *,
    has_category: bool,
) -> None:
    """Закрытия недель: в v1 разделов не было — закрытие дублируется на оба."""
    if not _table_exists(src, "closures"):
        report.add("needs_closures", 0, 0, "нет таблицы в источнике")
        return
    rows = _rows(src, "closures", ["base", "week_start", "category", "closed_at", "closed_by"])
    payload = []
    for row in rows:
        categories = (
            [row["category"]]
            if has_category and row["category"]
            else [CATEGORY_SOLUTIONS, CATEGORY_MEDICAMENTS]
        )
        for category in categories:
            payload.append(
                (row["base"], row["week_start"], category, row["closed_at"], row["closed_by"])
            )
    key = ("base", "week_start", "category")
    _upsert(
        dst,
        "needs_closures",
        ["base", "week_start", "category", "closed_at", "closed_by"],
        payload,
        key,
    )
    note = "" if has_category else "закрытие продублировано на оба раздела (прежняя схема v1)"
    report.add("needs_closures", len(rows), len(payload), note)


def _import_wiki(
    source: Path,
    dst: sqlite3.Connection,
    report: ImportReport,
    *,
    sources_dir: Path,
) -> None:
    if not source.exists():
        report.skipped.append(f"нет файла {source}")
        return
    src = _open_source(source)
    try:
        # Ссылки на сотрудников проверяются до записи: перенос «Компендиума» —
        # всё или ничего (пропустить статью значит потерять её молча).
        for table, column in (
            ("sources", "uploaded_by"),
            ("articles", "created_by"),
            ("revisions", "edited_by"),
            ("messages", "employee_id"),
        ):
            if _table_exists(src, table) and column in _columns(src, table):
                ids = [
                    row[0]
                    for row in src.execute(f"SELECT DISTINCT {column} FROM {table}")
                ]
                _guard_employees(dst, ids, f"«Компендиум»: {table}.{column}")

        _import_wiki_sources(src, dst, report, sources_dir=sources_dir)
        _copy_simple(src, dst, "articles", "wiki_articles", report, key=("id",))
        _copy_simple(src, dst, "revisions", "wiki_revisions", report, key=("id",))
        _copy_simple(src, dst, "article_links", "wiki_article_links", report, key=("id",))
        _copy_simple(src, dst, "messages", "wiki_messages", report, key=("id",))
        _copy_simple(src, dst, "settings", "wiki_settings", report, key=("key",))
    finally:
        src.close()


def _import_wiki_sources(
    src: sqlite3.Connection,
    dst: sqlite3.Connection,
    report: ImportReport,
    *,
    sources_dir: Path,
) -> None:
    """Источники: PDF из BLOB переезжает в файлы на диске (ADR-0020).

    BLOB в прежней БД — единственная копия приказа, поэтому перенос без папки
    хранилища останавливается: положить его будет некуда.
    """
    from docapp.wiki import files

    if not _table_exists(src, "sources"):
        report.add("wiki_sources", 0, 0, "нет таблицы в источнике")
        return

    source_columns = _columns(src, "sources")
    has_blob = "source" in source_columns
    columns = [
        c
        for c in ("id", "filename", "doc_number", "title", "added_at", "uploaded_by",
                  "page_count", "ocr_status", "ocr_error", "ocr_text", "tables_json")
        if c in source_columns
    ]
    selected = ", ".join(columns + (["source"] if has_blob else []))

    required = _required_columns(dst, "wiki_sources") - set(columns) - {"stored_name"}
    if required:
        raise LegacyImportError(
            f"sources → wiki_sources: в источнике нет обязательных колонок "
            f"{sorted(required)}. Перенос без них положил бы пустые значения."
        )

    rows = src.execute(f"SELECT {selected} FROM sources ORDER BY id").fetchall()
    payload = []
    moved = 0
    for row in rows:
        blob = row["source"] if has_blob else None
        stored_name = ""
        if blob:
            stored_name = files.store(bytes(blob), sources_dir)
            moved += 1
        payload.append(
            (*(row[column] for column in columns), stored_name)
        )

    _upsert(
        dst,
        "wiki_sources",
        columns + ["stored_name"],
        payload,
        ("id",),
    )
    dst.commit()

    notes = []
    if moved:
        notes.append(f"PDF в файлах хранилища: {moved}")
    if has_blob:
        notes.append("колонка source (BLOB) не переносится")
    if not moved and has_blob and any(row["source"] for row in rows):
        notes.append("часть источников осталась без файла")
    report.add("wiki_sources", len(rows), len(payload), "; ".join(notes))


def _import_duty(source: Path, dst: sqlite3.Connection, report: ImportReport) -> None:
    if not source.exists():
        report.skipped.append(f"нет файла {source}")
        return
    src = _open_source(source)
    try:
        # Отчёты дежурств терять нельзя: врач отсутствует — остановка, не пропуск.
        if _table_exists(src, "duty_report") and "doctor_id" in _columns(src, "duty_report"):
            ids = [
                row[0]
                for row in src.execute("SELECT DISTINCT doctor_id FROM duty_report")
            ]
            _guard_employees(dst, ids, "«Дежурства»: duty_report.doctor_id")
        _copy_simple(src, dst, "duty_report", "duty_reports", report, key=("id",))
        _copy_simple(src, dst, "duty_operation", "duty_operations", report, key=("id",))
    finally:
        src.close()
