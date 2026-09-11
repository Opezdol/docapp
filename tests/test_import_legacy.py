"""Тесты переноса данных с прежних баз на единую (шаг 4b, ADR-0016).

Проверяется поведение переносчика на прежних схемах — в том числе на тех
расхождениях, которые нашлись в живых базах: лишняя колонка `history_number` в
`anesthesia` и «Потребности» версии v1 без колонки `category`.
"""

from __future__ import annotations

import sqlite3

import pytest

from docapp.legacy import LegacyImportError, check, import_legacy

# ── прежние схемы (как они выглядели до переезда) ─────────────────────

CORE_SQL = """
CREATE TABLE employees (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    last_name     TEXT NOT NULL,
    first_name    TEXT NOT NULL,
    middle_name   TEXT NOT NULL DEFAULT '',
    role          TEXT NOT NULL,
    login         TEXT UNIQUE,
    password_hash TEXT,
    buh_id        TEXT
);
CREATE TABLE anesthesia (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    date           TEXT NOT NULL,
    patient_name   TEXT NOT NULL,
    history_number TEXT,
    doctor_id      INTEGER NOT NULL,
    nurse_id       INTEGER NOT NULL,
    created_at     TEXT NOT NULL
);
CREATE TABLE active_nurse (
    doctor_id INTEGER PRIMARY KEY,
    nurse_id  INTEGER NOT NULL
);
"""

NEEDS_V1_SQL = """
CREATE TABLE requests (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    base       TEXT NOT NULL,
    point      TEXT NOT NULL,
    week_start TEXT NOT NULL,
    author_id  INTEGER NOT NULL,
    status     TEXT NOT NULL DEFAULT 'draft',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE request_lines (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id INTEGER NOT NULL REFERENCES requests(id) ON DELETE CASCADE,
    item       TEXT NOT NULL,
    unit       TEXT NOT NULL,
    grp        TEXT NOT NULL,
    qty        REAL NOT NULL
);
CREATE TABLE closures (
    base       TEXT NOT NULL,
    week_start TEXT NOT NULL,
    closed_at  TEXT NOT NULL,
    closed_by  INTEGER NOT NULL,
    PRIMARY KEY (base, week_start)
);
"""

NEEDS_V2_SQL = """
CREATE TABLE requests (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    base       TEXT NOT NULL,
    point      TEXT NOT NULL,
    week_start TEXT NOT NULL,
    category   TEXT NOT NULL,
    author_id  INTEGER NOT NULL,
    status     TEXT NOT NULL DEFAULT 'draft',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE request_lines (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id INTEGER NOT NULL REFERENCES requests(id) ON DELETE CASCADE,
    item       TEXT NOT NULL,
    unit       TEXT NOT NULL DEFAULT '',
    grp        TEXT NOT NULL DEFAULT '',
    qty        INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE closures (
    base       TEXT NOT NULL,
    week_start TEXT NOT NULL,
    category   TEXT NOT NULL,
    closed_at  TEXT NOT NULL,
    closed_by  INTEGER NOT NULL,
    PRIMARY KEY (base, week_start, category)
);
"""

WIKI_SQL = """
CREATE TABLE sources (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    filename    TEXT NOT NULL,
    doc_number  TEXT NOT NULL DEFAULT '',
    title       TEXT NOT NULL DEFAULT '',
    added_at    TEXT NOT NULL,
    uploaded_by INTEGER NOT NULL,
    page_count  INTEGER NOT NULL DEFAULT 0,
    ocr_status  TEXT NOT NULL DEFAULT 'pending',
    ocr_error   TEXT NOT NULL DEFAULT '',
    ocr_text    TEXT NOT NULL DEFAULT '',
    tables_json TEXT NOT NULL DEFAULT '[]',
    source      BLOB,
    source_name TEXT NOT NULL DEFAULT ''
);
CREATE TABLE articles (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    title         TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'draft',
    published_revision_id INTEGER,
    created_by    INTEGER NOT NULL,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE TABLE revisions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id  INTEGER NOT NULL,
    version     INTEGER NOT NULL,
    body_md     TEXT NOT NULL DEFAULT '',
    rendered    TEXT NOT NULL DEFAULT '',
    edited_by   INTEGER NOT NULL,
    created_at  TEXT NOT NULL,
    change_note TEXT NOT NULL DEFAULT '',
    is_current  INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE article_links (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id INTEGER NOT NULL,
    source_id  INTEGER NOT NULL,
    anchor     TEXT NOT NULL DEFAULT ''
);
CREATE TABLE messages (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id       INTEGER NOT NULL,
    conversation_id   TEXT NOT NULL,
    role              TEXT NOT NULL,
    content           TEXT NOT NULL,
    citations         TEXT NOT NULL DEFAULT '[]',
    prompt_tokens     INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL
);
CREATE TABLE settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);
"""

DUTY_SQL = """
CREATE TABLE duty_report (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    base TEXT NOT NULL,
    shift_date TEXT NOT NULL,
    doctor_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (base, shift_date, doctor_id)
);
CREATE TABLE duty_operation (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER NOT NULL REFERENCES duty_report(id) ON DELETE CASCADE,
    operation TEXT NOT NULL,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0
);
"""


def _exec(db: sqlite3.Connection, sql: str) -> None:
    db.executescript(sql)


def _build_legacy(tmp_path, *, needs_sql: str = NEEDS_V1_SQL) -> dict:
    """Собрать прежний набор из четырёх баз с узнаваемыми данными."""
    core = sqlite3.connect(str(tmp_path / "docapp.db"))
    _exec(core, CORE_SQL)
    core.executemany(
        "INSERT INTO employees (id, last_name, first_name, role, login) VALUES (?,?,?,?,?)",
        [(1, "Петров", "Пётр", "doctor", "petrov"),
         (2, "Сидорова", "Анна", "nurse", "sidorova"),
         (3, "Иванов", "Иван", "head", "ivanov")],
    )
    core.execute(
        "INSERT INTO anesthesia (id, date, patient_name, history_number, doctor_id, "
        "nurse_id, created_at) VALUES (1, '2026-03-02', 'Пациент А.', '№12345', 1, 2, "
        "'2026-03-02T08:00:00')"
    )
    core.execute("INSERT INTO active_nurse (doctor_id, nurse_id) VALUES (1, 2)")
    core.execute("PRAGMA user_version = 2")
    core.commit()
    core.close()

    needs = sqlite3.connect(str(tmp_path / "needs.db"))
    _exec(needs, needs_sql)
    if "category" in needs_sql:
        needs.executemany(
            "INSERT INTO requests (id, base, point, week_start, category, author_id, "
            "status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            [(1, "база 1", "точка 1", "2026-03-02", "solutions", 2, "sent",
              "2026-03-01T10:00:00", "2026-03-01T10:00:00"),
             (2, "база 1", "точка 2", "2026-03-02", "medicaments", 3, "draft",
              "2026-03-01T11:00:00", "2026-03-01T11:00:00")],
        )
        needs.execute(
            "INSERT INTO closures (base, week_start, category, closed_at, closed_by) "
            "VALUES ('база 1', '2026-02-23', 'solutions', '2026-03-01T09:00:00', 3)"
        )
    else:
        needs.executemany(
            "INSERT INTO requests (id, base, point, week_start, author_id, status, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            [(1, "база 1", "точка 1", "2026-03-02", 2, "sent",
              "2026-03-01T10:00:00", "2026-03-01T10:00:00"),
             (2, "база 1", "точка 2", "2026-03-02", 3, "draft",
              "2026-03-01T11:00:00", "2026-03-01T11:00:00")],
        )
        needs.execute(
            "INSERT INTO closures (base, week_start, closed_at, closed_by) "
            "VALUES ('база 1', '2026-02-23', '2026-03-01T09:00:00', 3)"
        )
    needs.executemany(
        "INSERT INTO request_lines (id, request_id, item, unit, grp, qty) VALUES (?,?,?,?,?,?)",
        [(1, 1, "Натрия хлорид 0,9%", "фл", "Растворы", 10),
         (2, 1, "Пропофол", "амп", "Медикаменты", 5),
         (3, 2, "Шприц 10 мл", "шт", "Прочее", 20)],
    )
    needs.execute("PRAGMA user_version = 1")
    needs.commit()
    needs.close()

    wiki = sqlite3.connect(str(tmp_path / "wiki.db"))
    _exec(wiki, WIKI_SQL)
    wiki.execute(
        "INSERT INTO sources (id, filename, doc_number, title, added_at, uploaded_by, "
        "page_count, ocr_status, ocr_text, source, source_name) VALUES "
        "(1, 'prikaz-1.pdf', '№1', 'Приказ №1', '2026-03-01T09:00:00', 3, 3, 'done', "
        "'текст приказа', ?, 'prikaz-1.pdf')",
        (b"%PDF-1.4 fake",),
    )
    wiki.execute(
        "INSERT INTO articles (id, title, status, created_by, created_at, updated_at) VALUES "
        "(1, 'Премедикация', 'draft', 3, '2026-03-01T09:30:00', '2026-03-01T09:30:00')"
    )
    wiki.execute(
        "INSERT INTO revisions (id, article_id, version, body_md, rendered, edited_by, "
        "created_at, is_current) VALUES (1, 1, 1, 'Текст статьи', 'Текст статьи', 3, "
        "'2026-03-01T09:30:00', 1)"
    )
    wiki.execute(
        "INSERT INTO article_links (id, article_id, source_id, anchor) VALUES (1, 1, 1, 'стр. 1')"
    )
    wiki.commit()
    wiki.close()

    duty = sqlite3.connect(str(tmp_path / "duty.db"))
    _exec(duty, DUTY_SQL)
    duty.execute(
        "INSERT INTO duty_report (id, base, shift_date, doctor_id, status, created_at, "
        "updated_at) VALUES (1, 'база 1', '2026-03-02', 1, 'sent', "
        "'2026-03-02T08:00:00', '2026-03-02T20:00:00')"
    )
    duty.execute(
        "INSERT INTO duty_operation (id, report_id, operation, start_time, end_time, "
        "position) VALUES (1, 1, 'Аппендэктомия', '09:00', '10:30', 0)"
    )
    duty.commit()
    duty.close()

    return {
        "target": tmp_path / "app.db",
        "core_db": tmp_path / "docapp.db",
        "needs_db": tmp_path / "needs.db",
        "wiki_db": tmp_path / "wiki.db",
        "duty_db": tmp_path / "duty.db",
        # Папка хранилища PDF: по умолчанию — временная, иначе перенос писал бы
        # файлы в data/wiki/sources репозитория.
        "sources_dir": tmp_path / "sources",
    }


def _import(paths: dict, **kwargs):
    kwargs.setdefault("sources_dir", paths["sources_dir"])
    return import_legacy(
        paths["target"],
        core_db=paths["core_db"],
        needs_db=paths["needs_db"],
        wiki_db=paths["wiki_db"],
        duty_db=paths["duty_db"],
        **kwargs,
    )


def _count(db: str, table: str) -> int:
    conn = sqlite3.connect(db)
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


def _scalar(db: str, sql: str, *params):
    conn = sqlite3.connect(db)
    try:
        return conn.execute(sql, params).fetchone()[0]
    finally:
        conn.close()


def _one(db: str, sql: str) -> tuple:
    conn = sqlite3.connect(db)
    try:
        return conn.execute(sql).fetchone()
    finally:
        conn.close()


# ── перенос ───────────────────────────────────────────────────────────


class TestImport:
    def test_carries_data_from_all_four_databases(self, tmp_path):
        paths = _build_legacy(tmp_path)
        report = _import(paths)

        assert not report.orphans
        assert _count(str(paths["target"]), "employees") == 3
        assert _count(str(paths["target"]), "anesthesia") == 1
        assert _count(str(paths["target"]), "active_nurse") == 1
        assert _count(str(paths["target"]), "needs_requests") == 2
        assert _count(str(paths["target"]), "needs_request_lines") == 3
        assert _count(str(paths["target"]), "wiki_sources") == 1
        assert _count(str(paths["target"]), "wiki_articles") == 1
        assert _count(str(paths["target"]), "duty_reports") == 1
        assert _count(str(paths["target"]), "duty_operations") == 1

    def test_keeps_ids_and_drops_unknown_column(self, tmp_path):
        """id сохраняются (на них ссылаются дети), лишняя колонка не переносится."""
        paths = _build_legacy(tmp_path)
        _import(paths)
        target = str(paths["target"])

        assert _scalar(target, "SELECT id FROM anesthesia") == 1
        assert _scalar(target, "SELECT patient_name FROM anesthesia") == "Пациент А."
        assert _scalar(target, "SELECT doctor_id FROM anesthesia") == 1

        conn = sqlite3.connect(target)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(anesthesia)")}
        conn.close()
        assert "history_number" not in columns

        note = next(t.note for t in _import(paths).tables if t.table == "anesthesia")
        assert "history_number" in note

    def test_derives_category_from_lines_for_v1(self, tmp_path):
        """Прежняя схема «Потребностей» без category: раздел выводится по строкам."""
        paths = _build_legacy(tmp_path, needs_sql=NEEDS_V1_SQL)
        report = _import(paths)
        target = str(paths["target"])

        assert _scalar(target, "SELECT category FROM needs_requests WHERE id = 1") == "solutions"
        assert _scalar(target, "SELECT category FROM needs_requests WHERE id = 2") == "medicaments"

        # закрытие недели без разделов дублируется на оба (как делала миграция v2)
        closures = conn_rows(target, "SELECT base, week_start, category FROM needs_closures")
        assert {row[2] for row in closures} == {"solutions", "medicaments"}

        note = next(t.note for t in report.tables if t.table == "needs_requests")
        assert "по строкам" in note

    def test_keeps_category_for_v2(self, tmp_path):
        """Если колонка category уже есть, она переносится как есть."""
        paths = _build_legacy(tmp_path, needs_sql=NEEDS_V2_SQL)
        _import(paths)
        target = str(paths["target"])
        assert _scalar(target, "SELECT category FROM needs_requests WHERE id = 1") == "solutions"
        assert _scalar(target, "SELECT COUNT(*) FROM needs_closures") == 1

    def test_sets_position_in_existing_order(self, tmp_path):
        """У строк заявок не было position — порядок берётся из id."""
        paths = _build_legacy(tmp_path)
        _import(paths)
        conn = sqlite3.connect(str(paths["target"]))
        rows = conn.execute(
            "SELECT id, position FROM needs_request_lines WHERE request_id = 1 ORDER BY id"
        ).fetchall()
        conn.close()
        assert rows == [(1, 0), (2, 1)]

    def test_second_run_changes_nothing(self, tmp_path):
        """Повторный прогон не плодит копии и не сносит детей каскадом."""
        paths = _build_legacy(tmp_path)
        _import(paths)
        before = {
            table: _count(str(paths["target"]), table)
            for table in ("employees", "needs_requests", "needs_request_lines", "duty_operations")
        }

        _import(paths)

        after = {table: _count(str(paths["target"]), table) for table in before}
        assert after == before
        assert _count(str(paths["target"]), "needs_request_lines") == 3

    def test_stops_on_orphan_author(self, tmp_path):
        """Заявка от несуществующего сотрудника — остановка, а не тихая потеря."""
        paths = _build_legacy(tmp_path)
        conn = sqlite3.connect(str(paths["needs_db"]))
        conn.execute(
            "INSERT INTO requests (id, base, point, week_start, author_id, status, "
            "created_at, updated_at) VALUES (9, 'база 1', 'точка 9', '2026-03-02', 99, "
            "'draft', '2026-03-01T12:00:00', '2026-03-01T12:00:00')"
        )
        conn.commit()
        conn.close()

        with pytest.raises(LegacyImportError) as exc:
            _import(paths)
        assert "99" in str(exc.value)

    def test_skip_orphans_imports_the_rest(self, tmp_path):
        paths = _build_legacy(tmp_path)
        conn = sqlite3.connect(str(paths["needs_db"]))
        conn.execute(
            "INSERT INTO requests (id, base, point, week_start, author_id, status, "
            "created_at, updated_at) VALUES (9, 'база 1', 'точка 9', '2026-03-02', 99, "
            "'draft', '2026-03-01T12:00:00', '2026-03-01T12:00:00')"
        )
        conn.commit()
        conn.close()

        report = _import(paths, skip_orphans=True)
        target = str(paths["target"])

        assert _count(target, "needs_requests") == 2  # заявка 9 пропущена
        assert _scalar(target, "SELECT COUNT(*) FROM needs_requests WHERE id = 9") == 0
        note = next(t.note for t in report.tables if t.table == "needs_requests")
        assert "пропущено заявок без сотрудника: 1" in note

    def test_wiki_sources_move_to_files(self, tmp_path):
        """PDF из BLOB прежней базы становится файлом в хранилище (ADR-0020)."""
        paths = _build_legacy(tmp_path)
        sources_dir = tmp_path / "sources"
        report = _import(paths, sources_dir=sources_dir)

        target = str(paths["target"])
        row = _one(target, "SELECT filename, stored_name FROM wiki_sources WHERE id = 1")
        assert row[0] == "prikaz-1.pdf"

        stored = sources_dir / row[1]
        assert stored.is_file()
        assert stored.read_bytes() == b"%PDF-1.4 fake"
        assert "приказ" not in row[1]          # имя файла — хеш, без названия приказа

        note = next(t.note for t in report.tables if t.table == "wiki_sources")
        assert "PDF в файлах хранилища: 1" in note

        conn = sqlite3.connect(target)
        columns = {r[1] for r in conn.execute("PRAGMA table_info(wiki_sources)")}
        conn.close()
        assert "source" not in columns         # BLOB в целевой схеме больше нет

    def test_catalog_file_fills_catalog_tables(self, tmp_path):
        """Каталог — файл, а не строки прежней базы: заливается целиком (ADR-0019)."""
        paths = _build_legacy(tmp_path)
        catalog_path = tmp_path / "catalog.yaml"
        catalog_path.write_text(
            "bases:\n  Ленская: [травма]\ngroups:\n  Растворы:\n    Физ 200/250: фл\n",
            encoding="utf-8",
        )

        report = _import(paths, catalog_path=catalog_path)
        target = str(paths["target"])

        assert _scalar(target, "SELECT COUNT(*) FROM needs_catalog_items") == 1
        assert _scalar(target, "SELECT unit FROM needs_catalog_items") == "фл"
        assert _scalar(target, "SELECT COUNT(*) FROM needs_catalog_points") == 1

        # первая запись журнала каталога — импорт, без человека (системное действие)
        row = _one(target, "SELECT action, entity, employee_id, details FROM needs_catalog_audit")
        assert row[0] == "import" and row[1] == "catalog"
        assert row[2] is None and "catalog.yaml" in row[3]

        counters = {t.table: t for t in report.tables}
        assert counters["needs_catalog_items"].imported == 1

    def test_missing_source_is_reported_not_silent(self, tmp_path):
        """Базы может не быть (сервер её не вёл) — об этом сообщается."""
        paths = _build_legacy(tmp_path)
        paths["duty_db"].unlink()
        report = _import(paths)
        assert any("duty.db" in item for item in report.skipped)
        assert _count(str(paths["target"]), "employees") == 3

    def test_refuses_when_source_cannot_fill_required_columns(self, tmp_path):
        """В источнике нет обязательной колонки — остановка, не пустое значение."""
        paths = _build_legacy(tmp_path)
        conn = sqlite3.connect(str(paths["wiki_db"]))
        conn.executescript(
            "DROP TABLE sources;"
            "CREATE TABLE sources (id INTEGER PRIMARY KEY, title TEXT NOT NULL);"
        )
        conn.commit()
        conn.close()

        with pytest.raises(LegacyImportError) as exc:
            _import(paths)
        message = str(exc.value)
        assert "sources → wiki_sources" in message
        assert "filename" in message and "added_at" in message


# ── проверка ──────────────────────────────────────────────────────────


class TestCheck:
    def test_clean_after_import(self, tmp_path):
        paths = _build_legacy(tmp_path)
        _import(paths)
        report = check(paths["target"])

        assert report.ok
        assert report.counts["employees"] == 3
        assert report.counts["needs_requests"] == 2
        assert report.versions["docapp"] == 3
        assert report.versions["needs"] == 2
        assert report.versions["duty"] == 1
        assert "всё сходится" in report.summary()

    def test_reports_missing_file(self, tmp_path):
        report = check(tmp_path / "нет-такого.db")
        assert not report.ok
        assert "файла нет" in report.summary()

    def test_reports_orphans(self, tmp_path):
        """Сироту видно и в готовой БД: ключи сняты вручную, как при переносе."""
        paths = _build_legacy(tmp_path)
        _import(paths)
        conn = sqlite3.connect(str(paths["target"]))
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute(
            "INSERT INTO duty_reports (id, base, shift_date, doctor_id, status, "
            "created_at, updated_at) VALUES (77, 'база 1', '2026-03-03', 55, 'sent', "
            "'2026-03-03T08:00:00', '2026-03-03T20:00:00')"
        )
        conn.commit()
        conn.close()

        report = check(paths["target"])
        assert not report.ok
        assert report.orphans["duty_reports.doctor_id"] == [55]


def conn_rows(db: str, sql: str) -> list[tuple]:
    conn = sqlite3.connect(db)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()
