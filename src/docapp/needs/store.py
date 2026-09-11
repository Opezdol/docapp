"""Хранилище подприложения «Потребности» на SQLite.

Собственная база данных (data/needs/needs.db), основную docapp.db
не трогаем (ADR-11). Соединение и стиль — как в
ядре хранилищ (row_factory = sqlite3.Row,
check_same_thread=False (FastAPI обрабатывает запросы в пуле потоков),
PRAGMA foreign_keys = ON для каскадного удаления строк заявки.
Таймстемпы — ISO-строки datetime.now().isoformat(), как в sqlite_store.py.
"""

import sqlite3
from datetime import datetime
from pathlib import Path

from docapp.core.db import Schema, open_db
from docapp.needs import statuses

_SCHEMA = """
CREATE TABLE IF NOT EXISTS needs_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    base TEXT NOT NULL,
    point TEXT NOT NULL,
    week_start TEXT NOT NULL,
    category TEXT NOT NULL,
    author_id INTEGER NOT NULL REFERENCES employees(id),
    status TEXT NOT NULL DEFAULT 'draft',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (base, point, week_start, category)
);
CREATE TABLE IF NOT EXISTS needs_request_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id INTEGER NOT NULL REFERENCES needs_requests(id) ON DELETE CASCADE,
    item TEXT NOT NULL,
    unit TEXT NOT NULL DEFAULT '',
    grp TEXT NOT NULL DEFAULT '',
    qty INTEGER NOT NULL DEFAULT 0,
    position INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_lines_req ON needs_request_lines(request_id);
CREATE TABLE IF NOT EXISTS needs_closures (
    base TEXT NOT NULL,
    week_start TEXT NOT NULL,
    category TEXT NOT NULL,
    closed_at TEXT NOT NULL,
    closed_by INTEGER NOT NULL REFERENCES employees(id),
    PRIMARY KEY (base, week_start, category)
);
-- Каталог расходки (ADR-0019): был файлом catalog.yaml, стал таблицами.
-- Порядок строк значим (в нём каталог показывается) — хранится в `position`.
-- Колонка группы называется grp, как снимок группы в строках заявок:
-- слово `group` — ключевое в SQL, и записи пришлось бы брать в кавычки везде.
CREATE TABLE IF NOT EXISTS needs_catalog_bases (
    name     TEXT PRIMARY KEY,
    position INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS needs_catalog_points (
    base     TEXT NOT NULL REFERENCES needs_catalog_bases(name) ON DELETE CASCADE,
    name     TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (base, name)
);
CREATE TABLE IF NOT EXISTS needs_catalog_groups (
    name     TEXT PRIMARY KEY,
    position INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS needs_catalog_items (
    grp      TEXT NOT NULL REFERENCES needs_catalog_groups(name) ON DELETE CASCADE,
    name     TEXT NOT NULL,
    unit     TEXT NOT NULL DEFAULT '',
    position INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (grp, name)
);
-- Журнал правки каталога: кто, когда и что менял. employee_id пуст для
-- системных действий (первичный импорт seed или перенос прежних данных).
CREATE TABLE IF NOT EXISTS needs_catalog_audit (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    at          TEXT NOT NULL,
    employee_id INTEGER REFERENCES employees(id),
    action      TEXT NOT NULL,               -- import / upsert / delete
    entity      TEXT NOT NULL,               -- base / point / group / item
    entity_key  TEXT NOT NULL DEFAULT '',    -- «Растворы/Физ 200/250», «Ленская»
    details     TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_catalog_audit_at ON needs_catalog_audit(at DESC);
"""


def now_iso() -> str:
    """Текущее время как ISO-строка (как в sqlite_store.py: isoformat()).

    Публичная: тем же способом помечает время и журнал каталога
    (`catalog_store`), а второй формат времени модулю не нужен.
    """
    return datetime.now().isoformat()


def _connect(db_path: str | Path) -> sqlite3.Connection:
    """Открыть БД заявок: подключение, PRAGMA, схема, миграции — внутри core.db."""
    return open_db(db_path, SCHEMA)


# Версия схемы «Потребностей» в единой БД (ADR-0016). Увеличивайте при изменении
# схемы и добавляйте миграцию в _MIGRATIONS.
#
# История: до переезда на единую БД таблицы назывались requests / request_lines /
# closures, а колонка `category` появлялась миграцией v2. В новой схеме они
# создаются сразу с префиксом модуля и колонкой `category`, поэтому миграций нет:
# данные прежних баз переносит `docapp import-legacy` (docs/ТЗ-каркас.md §9).
# v2: каталог расходки переехал из файла в таблицы needs_catalog_* (ADR-0019).
SCHEMA_VERSION = 2

_CATALOG_TABLES = [
    "CREATE TABLE IF NOT EXISTS needs_catalog_bases ("
    "name TEXT PRIMARY KEY, position INTEGER NOT NULL DEFAULT 0)",
    "CREATE TABLE IF NOT EXISTS needs_catalog_points ("
    "base TEXT NOT NULL REFERENCES needs_catalog_bases(name) ON DELETE CASCADE, "
    "name TEXT NOT NULL, position INTEGER NOT NULL DEFAULT 0, "
    "PRIMARY KEY (base, name))",
    "CREATE TABLE IF NOT EXISTS needs_catalog_groups ("
    "name TEXT PRIMARY KEY, position INTEGER NOT NULL DEFAULT 0)",
    "CREATE TABLE IF NOT EXISTS needs_catalog_items ("
    "grp TEXT NOT NULL REFERENCES needs_catalog_groups(name) ON DELETE CASCADE, "
    "name TEXT NOT NULL, unit TEXT NOT NULL DEFAULT '', "
    "position INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (grp, name))",
    "CREATE TABLE IF NOT EXISTS needs_catalog_audit ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, "
    "employee_id INTEGER REFERENCES employees(id), action TEXT NOT NULL, "
    "entity TEXT NOT NULL, entity_key TEXT NOT NULL DEFAULT '', "
    "details TEXT NOT NULL DEFAULT '')",
    "CREATE INDEX IF NOT EXISTS idx_catalog_audit_at ON needs_catalog_audit(at DESC)",
]

#: v1 — схема модуля как есть (прежние таблицы под новыми именами);
#: v2 — каталог расходки переезжает в таблицы (ADR-0019).
_MIGRATIONS: list[tuple[int, str, list[str]]] = [
    (2, "каталог расходки в БД", _CATALOG_TABLES),
]


#: Схема модуля «Потребности» для общего механизма БД (core.db).
SCHEMA = Schema(
    module="needs",
    sql=_SCHEMA,
    version=SCHEMA_VERSION,
    migrations=_MIGRATIONS,
)


class SqliteNeedsStore:
    """Заявки на потребности и закрытия недель в SQLite-файле."""

    def __init__(self, db_path: str | Path) -> None:
        self._conn = _connect(db_path)

    def close_conn(self) -> None:
        """Закрыть соединение с БД.

        Название не `close`: метод `close(base, week_start, closed_by)`
        (закрытие недели) зафиксирован ТЗ, а в Python нельзя определить
        в одном классе два метода с одним именем (см. sqlite_store.py).
        """
        self._conn.close()

    def __enter__(self) -> "SqliteNeedsStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close_conn()

    # ── внутренние помощники ──────────────────────────────────────────

    @staticmethod
    def _validate_lines(lines: list[dict]) -> None:
        """Проверка строк заявки: количество не может быть отрицательным."""
        for line in lines:
            if line.get("qty", 0) < 0:
                raise ValueError(
                    f"Количество не может быть отрицательным: {line.get('qty')}"
                )

    def _load_lines(self, request_id: int) -> list[dict]:
        """Строки заявки как list[dict(item, unit, grp, qty)] в порядке формы."""
        rows = self._conn.execute(
            "SELECT item, unit, grp, qty FROM needs_request_lines "
            "WHERE request_id = ? ORDER BY position, id",
            (request_id,),
        ).fetchall()
        return [
            {"item": r["item"], "unit": r["unit"], "grp": r["grp"], "qty": r["qty"]}
            for r in rows
        ]

    def _row_to_request(self, row: sqlite3.Row) -> dict:
        """Строка needs_requests + её lines -> dict с ключами из ТЗ."""
        return {
            "id": row["id"],
            "base": row["base"],
            "point": row["point"],
            "week_start": row["week_start"],
            "category": row["category"],
            "author_id": row["author_id"],
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "lines": self._load_lines(row["id"]),
        }

    # ── заявки ────────────────────────────────────────────────────────

    def get_request(self, base: str, point: str, category: str, week_start: str) -> dict | None:
        """Заявка по (base, point, week_start, category) или None."""
        row = self._conn.execute(
            "SELECT * FROM needs_requests "
            "WHERE base = ? AND point = ? AND week_start = ? AND category = ?",
            (base, point, week_start, category),
        ).fetchone()
        return self._row_to_request(row) if row else None

    def save_request(
        self,
        base: str,
        point: str,
        category: str,
        week_start: str,
        author_id: int,
        lines: list[dict],
        status: str = statuses.DRAFT,
    ) -> int:
        """Создать или обновить заявку (upsert по UNIQUE(base, point, week_start, category)).

        Существующая запись обновляется (author_id, status, updated_at),
        её строки ЗАМЕНЯЮТСЯ (DELETE + INSERT). Возвращает id заявки.
        """
        self._validate_lines(lines)
        now = now_iso()
        existing = self._conn.execute(
            "SELECT id FROM needs_requests "
            "WHERE base = ? AND point = ? AND week_start = ? AND category = ?",
            (base, point, week_start, category),
        ).fetchone()
        if existing:
            request_id = existing["id"]
            self._conn.execute(
                "UPDATE needs_requests SET author_id = ?, status = ?, updated_at = ? "
                "WHERE id = ?",
                (author_id, status, now, request_id),
            )
            self._conn.execute(
                "DELETE FROM needs_request_lines WHERE request_id = ?", (request_id,)
            )
        else:
            cur = self._conn.execute(
                "INSERT INTO needs_requests "
                "(base, point, week_start, category, author_id, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (base, point, week_start, category, author_id, status, now, now),
            )
            request_id = cur.lastrowid
            assert request_id is not None  # INSERT только что прошёл
        self._conn.executemany(
            "INSERT INTO needs_request_lines (request_id, item, unit, grp, qty, position) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    request_id,
                    line.get("item", ""),
                    line.get("unit", ""),
                    line.get("grp", ""),
                    line.get("qty", 0),
                    position,
                )
                for position, line in enumerate(lines)
            ],
        )
        self._conn.commit()
        return request_id

    def set_status(self, request_id: int, status: str) -> None:
        """Сменить статус заявки (обновляет updated_at)."""
        self._conn.execute(
            "UPDATE needs_requests SET status = ?, updated_at = ? WHERE id = ?",
            (status, now_iso(), request_id),
        )
        self._conn.commit()

    def list_requests(self, base: str, category: str, week_start: str) -> list[dict]:
        """Все заявки базы и раздела за неделю (с lines)."""
        rows = self._conn.execute(
            "SELECT * FROM needs_requests WHERE base = ? AND week_start = ? AND category = ? "
            "ORDER BY point, id",
            (base, week_start, category),
        ).fetchall()
        return [self._row_to_request(r) for r in rows]

    def list_week(self, week_start: str) -> list[dict]:
        """Все заявки всех баз за неделю (с lines) — для доски старшей."""
        rows = self._conn.execute(
            "SELECT * FROM needs_requests WHERE week_start = ? ORDER BY base, point",
            (week_start,),
        ).fetchall()
        return [self._row_to_request(r) for r in rows]

    def list_range(
        self,
        from_week: str,
        to_week: str,
        base: str | None = None,
        point: str | None = None,
    ) -> list[dict]:
        """Заявки за диапазон недель (с lines).

        week_start сравнивается лексикографически: формат ISO 'YYYY-MM-DD'.
        """
        sql = "SELECT * FROM needs_requests WHERE week_start >= ? AND week_start <= ?"
        params: list = [from_week, to_week]
        if base is not None:
            sql += " AND base = ?"
            params.append(base)
        if point is not None:
            sql += " AND point = ?"
            params.append(point)
        sql += " ORDER BY week_start, base, point"
        rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_request(r) for r in rows]

    # ── закрытия недель ───────────────────────────────────────────────

    def close(self, base: str, category: str, week_start: str, closed_by: int) -> None:
        """Закрыть неделю для базы и раздела (INSERT OR REPLACE, актуальное время)."""
        self._conn.execute(
            "INSERT OR REPLACE INTO needs_closures "
            "(base, week_start, category, closed_at, closed_by) "
            "VALUES (?, ?, ?, ?, ?)",
            (base, week_start, category, now_iso(), closed_by),
        )
        self._conn.commit()

    def is_closed(self, base: str, category: str, week_start: str) -> bool:
        """Закрыта ли неделя для базы и раздела."""
        row = self._conn.execute(
            "SELECT 1 FROM needs_closures WHERE base = ? AND week_start = ? AND category = ?",
            (base, week_start, category),
        ).fetchone()
        return row is not None

    def reopen(self, base: str, category: str, week_start: str) -> None:
        """Открыть неделю заново для базы и раздела (удалить закрытие)."""
        self._conn.execute(
            "DELETE FROM needs_closures WHERE base = ? AND week_start = ? AND category = ?",
            (base, week_start, category),
        )
        self._conn.commit()

    def list_closures(self) -> list[dict]:
        """Все закрытия недель (база, неделя, раздел, кто/когда)."""
        rows = self._conn.execute(
            "SELECT base, week_start, category, closed_at, closed_by FROM needs_closures "
            "ORDER BY base, week_start, category"
        ).fetchall()
        return [dict(r) for r in rows]

    def closed_sections(self, week_start: str) -> list[dict]:
        """Закрытые разделы (base, category) за неделю — для отображения сёстрам."""
        rows = self._conn.execute(
            "SELECT base, category FROM needs_closures WHERE week_start = ? "
            "ORDER BY base, category",
            (week_start,),
        ).fetchall()
        return [{"base": r["base"], "category": r["category"]} for r in rows]
