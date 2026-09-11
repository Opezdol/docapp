"""Форма таблиц: файл от прежней схемы не должен жить до первой записи.

Дефект, из-за которого эти тесты появились: `data/docapp.db` достался от
прежней базы, где у `anesthesia` была колонка `history_number NOT NULL`, а
`CREATE TABLE IF NOT EXISTS` такую таблицу принимает как есть. Версия модуля
считалась текущей, миграция «убрать номер истории болезни» не выполнялась, и
первая же запись падала на `NOT NULL constraint failed:
anesthesia.history_number` — при 588 зелёных тестах, потому что тестовая БД
собирается с нуля и всегда имеет текущую форму.

Здесь проверяется весь путь: расхождение видно (`check`), движок о нём
предупреждает, а `repair-schema` приводит таблицу к объявлению, сохранив строки.
"""

import logging
import sqlite3

import pytest

from docapp.core.db import SCHEMA_TABLE, declared_tables, repair, shape_problems
from docapp.legacy.checks import check, shape_issues
from docapp.records.store import SCHEMA as RECORDS_SCHEMA
from docapp.records.store import SqliteAnesthesiaStore
from docapp.people.store import SCHEMA as PEOPLE_SCHEMA
from docapp.records.service import AnesthesiaService

from factories import make_db

#: Прежняя форма таблицы анестезий: номер истории болезни был обязательным
#: (ADR-14 убрал его, миграция v2 пересобрала таблицу — но не в этом файле).
LEGACY_ANESTHESIA = """
CREATE TABLE anesthesia (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    date           TEXT NOT NULL,
    patient_name   TEXT NOT NULL,
    history_number TEXT NOT NULL,
    doctor_id      INTEGER NOT NULL REFERENCES employees(id),
    nurse_id       INTEGER NOT NULL REFERENCES employees(id),
    created_at     TEXT NOT NULL
);
"""


def _make_legacy_shape(db) -> None:
    """Сделать вид, что файл достался от прежней базы.

    Таблица пересобрана в прежней форме, а версия модуля `records` заявлена
    текущей — ровно то состояние, в котором оказалась рабочая БД.
    """
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("DROP TABLE anesthesia")
    conn.executescript(LEGACY_ANESTHESIA)
    conn.execute(
        f"DELETE FROM {SCHEMA_TABLE} WHERE module = 'records'"
    )
    conn.execute(
        f"INSERT INTO {SCHEMA_TABLE} (module, version, applied_at, note) "
        f"VALUES ('records', 3, '2026-09-11T00:00:00', 'initial schema')"
    )
    conn.execute(
        "INSERT INTO anesthesia (date, patient_name, history_number, doctor_id, "
        "nurse_id, created_at) VALUES ('2026-09-10', 'Тестов, Пациент', '1234/26', 1, 2, "
        "'2026-09-10T08:00:00')"
    )
    conn.commit()
    conn.close()


def test_record_insert_fails_on_legacy_shape(tmp_path):
    """Сначала убеждаемся, что дефект настоящий: в такую таблицу запись падает."""
    db = make_db(tmp_path)
    _make_legacy_shape(db)

    store = SqliteAnesthesiaStore(db)
    try:
        with pytest.raises(sqlite3.IntegrityError) as error:
            AnesthesiaService(store).create(1, 2, "Новый, Пациент")
        assert "history_number" in str(error.value)
    finally:
        store.close()


def test_shape_problem_is_visible_before_the_user_clicks(tmp_path):
    """`check` показывает расхождение — это то, что ловит дефект до записи."""
    db = make_db(tmp_path)
    _make_legacy_shape(db)

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        problems = shape_problems(conn, RECORDS_SCHEMA)
        issues = shape_issues(conn)
    finally:
        conn.close()

    assert any("anesthesia" in p and "history_number" in p for p in problems)
    assert any("history_number" in i for i in issues)
    report = check(db)
    assert not report.ok
    assert any("history_number" in issue for issue in report.issues)


def test_engine_warns_about_the_shape_without_refusing_to_open(tmp_path, caplog):
    """Открыть файл движок не мешает (читается всё), но говорит о расхождении."""
    db = make_db(tmp_path)
    _make_legacy_shape(db)

    with caplog.at_level(logging.WARNING, logger="docapp.core.db"):
        SqliteAnesthesiaStore(db).close()

    assert any("history_number" in record.getMessage() for record in caplog.records)


def test_repair_fixes_the_shape_and_keeps_rows(tmp_path):
    """`repair-schema` приводит таблицу к объявлению, строки остаются на месте."""
    db = make_db(tmp_path)
    _make_legacy_shape(db)

    actions = repair(db, [RECORDS_SCHEMA])

    assert any("anesthesia" in action and "history_number" in action for action in actions)
    conn = sqlite3.connect(str(db))
    try:
        columns = [row[1] for row in conn.execute("PRAGMA table_info(anesthesia)")]
        rows = list(conn.execute("SELECT patient_name, doctor_id FROM anesthesia"))
    finally:
        conn.close()
    assert columns == list(declared_tables(RECORDS_SCHEMA)["anesthesia"][0])
    assert rows == [("Тестов, Пациент", 1)]

    # то самое действие, которое раньше падало с 500
    store = SqliteAnesthesiaStore(db)
    try:
        created = AnesthesiaService(store).create(1, 2, "Новый, Пациент")
        assert created.id is not None
    finally:
        store.close()


def test_repair_is_idempotent_and_check_goes_green(tmp_path):
    """Повторный прогон ничего не делает; после починки `check` чист."""
    db = make_db(tmp_path)
    _make_legacy_shape(db)

    repair(db, [RECORDS_SCHEMA])
    assert repair(db, [RECORDS_SCHEMA]) == []
    assert check(db).ok


def test_current_schema_has_no_shape_problems(tmp_path):
    """Свежая БД: форма совпадает объявлением, править нечего."""
    from docapp.modules import MODULES

    db = make_db(tmp_path)
    conn = sqlite3.connect(str(db))
    try:
        for module in MODULES:
            if module.schema is None:
                continue
            assert shape_problems(conn, module.schema) == []
    finally:
        conn.close()
    assert repair(db, [m.schema for m in MODULES if m.schema]) == []


def test_parser_reads_declared_columns_with_comments():
    """Разбор DDL не путается в комментариях `--` и вложенных скобках."""
    from docapp.core.db import Schema

    schema = Schema(
        module="проверка",
        sql=(
            "-- комментарий с запятой, скобкой ) и словом CREATE TABLE\n"
            "CREATE TABLE IF NOT EXISTS пример (\n"
            "    id      INTEGER PRIMARY KEY AUTOINCREMENT,\n"
            "    name    TEXT NOT NULL,          -- пояснение (в скобках)\n"
            "    amount  TEXT NOT NULL REFERENCES other(id),\n"
            "    PRIMARY KEY (id)\n"
            ");\n"
        ),
        version=1,
    )
    columns, statement = declared_tables(schema)["пример"]
    assert columns == ("id", "name", "amount")
    assert statement.upper().startswith("CREATE TABLE IF NOT EXISTS ПРИМЕР") or "пример" in statement
    assert "PRIMARY KEY (id)" in statement  # тело таблицы разобрано до парной скобки
