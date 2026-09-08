"""Хранилище подприложения «Потребности» на SQLite.

Собственная база данных (data/needs/needs.db), основную docapp.db
не трогаем (ADR-11). Соединение и стиль — как в
docapp/storage/sqlite_store.py: row_factory = sqlite3.Row,
check_same_thread=False (FastAPI обрабатывает запросы в пуле потоков),
PRAGMA foreign_keys = ON для каскадного удаления строк заявки.
Таймстемпы — ISO-строки datetime.now().isoformat(), как в sqlite_store.py.
"""

import sqlite3
from datetime import datetime
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    base TEXT NOT NULL,
    point TEXT NOT NULL,
    week_start TEXT NOT NULL,
    category TEXT NOT NULL,
    author_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (base, point, week_start, category)
);
CREATE TABLE IF NOT EXISTS request_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id INTEGER NOT NULL REFERENCES requests(id) ON DELETE CASCADE,
    item TEXT NOT NULL,
    unit TEXT NOT NULL DEFAULT '',
    grp TEXT NOT NULL DEFAULT '',
    qty INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_lines_req ON request_lines(request_id);
CREATE TABLE IF NOT EXISTS closures (
    base TEXT NOT NULL,
    week_start TEXT NOT NULL,
    category TEXT NOT NULL,
    closed_at TEXT NOT NULL,
    closed_by INTEGER NOT NULL,
    PRIMARY KEY (base, week_start, category)
);
"""


def _now() -> str:
    """Текущее время как ISO-строка (как в sqlite_store.py: isoformat())."""
    return datetime.now().isoformat()


def _connect(db_path: str | Path) -> sqlite3.Connection:
    # check_same_thread=False: FastAPI обрабатывает запросы в пуле потоков,
    # соединение не может быть привязано к одному потоку.
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.executescript(_SCHEMA)
    _apply_migrations(conn)
    conn.commit()
    return conn


# Версия схемы БД потребностей. Увеличивайте при изменении схемы
# и добавляйте миграцию в _MIGRATIONS.
SCHEMA_VERSION = 2

# Миграции: (версия_после_применения, название, [SQL...])
_MIGRATIONS: list[tuple[int, str, list[str]]] = [
    # (1, "initial schema", [])
    # v2: колонка `category` в requests и closures (разделы растворы/медикаменты).
    # SQLite не меняет UNIQUE/PK через ALTER — пересборка таблиц
    # (прецедент: пересборка таблицы anesthesia в sqlite_store.py).
    # Пересборка идёт через RENAME/создание/копирование, чтобы сохранить
    # строки request_lines (их FK на requests переключается без потери данных,
    # foreign_keys остаётся ON — DROP родителя не каскадит строки).
    # Категория заявки выводится по её строкам: есть строка grp='Растворы' —
    # solutions, иначе medicaments. Старые закрытия дублируются на оба раздела.
    (
        2,
        "add category column to requests and closures",
        [
            "ALTER TABLE requests RENAME TO requests_v1",
            "CREATE TABLE requests ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "base TEXT NOT NULL, point TEXT NOT NULL, week_start TEXT NOT NULL, "
            "category TEXT NOT NULL, author_id INTEGER NOT NULL, "
            "status TEXT NOT NULL DEFAULT 'draft', created_at TEXT NOT NULL, "
            "updated_at TEXT NOT NULL, "
            "UNIQUE (base, point, week_start, category))",
            "INSERT INTO requests "
            "(id, base, point, week_start, category, author_id, status, created_at, updated_at) "
            "SELECT id, base, point, week_start, "
            "CASE WHEN EXISTS (SELECT 1 FROM request_lines l "
            "WHERE l.request_id = requests_v1.id AND l.grp = 'Растворы') "
            "THEN 'solutions' ELSE 'medicaments' END, "
            "author_id, status, created_at, updated_at FROM requests_v1",
            "ALTER TABLE request_lines RENAME TO request_lines_v1",
            "DROP INDEX idx_lines_req",
            "CREATE TABLE request_lines ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "request_id INTEGER NOT NULL REFERENCES requests(id) ON DELETE CASCADE, "
            "item TEXT NOT NULL, unit TEXT NOT NULL DEFAULT '', "
            "grp TEXT NOT NULL DEFAULT '', qty INTEGER NOT NULL DEFAULT 0)",
            "CREATE INDEX idx_lines_req ON request_lines(request_id)",
            "INSERT INTO request_lines (id, request_id, item, unit, grp, qty) "
            "SELECT id, request_id, item, unit, grp, qty FROM request_lines_v1",
            "DROP TABLE request_lines_v1",
            "DROP TABLE requests_v1",
            "CREATE TABLE closures_new ("
            "base TEXT NOT NULL, week_start TEXT NOT NULL, category TEXT NOT NULL, "
            "closed_at TEXT NOT NULL, closed_by INTEGER NOT NULL, "
            "PRIMARY KEY (base, week_start, category))",
            "INSERT INTO closures_new (base, week_start, category, closed_at, closed_by) "
            "SELECT base, week_start, 'solutions', closed_at, closed_by FROM closures",
            "INSERT INTO closures_new (base, week_start, category, closed_at, closed_by) "
            "SELECT base, week_start, 'medicaments', closed_at, closed_by FROM closures",
            "DROP TABLE closures",
            "ALTER TABLE closures_new RENAME TO closures",
        ],
    ),
]


def _apply_migrations(conn: sqlite3.Connection) -> None:
    """Применить миграции схемы, если user_version устарел."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version == 0:
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
        return
    for target, name, statements in _MIGRATIONS:
        if version < target:
            for stmt in statements:
                conn.execute(stmt)
            conn.execute(f"PRAGMA user_version = {target}")
            conn.commit()
            version = target


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
        """Строки заявки как list[dict(item, unit, grp, qty)]."""
        rows = self._conn.execute(
            "SELECT item, unit, grp, qty FROM request_lines "
            "WHERE request_id = ? ORDER BY id",
            (request_id,),
        ).fetchall()
        return [
            {"item": r["item"], "unit": r["unit"], "grp": r["grp"], "qty": r["qty"]}
            for r in rows
        ]

    def _row_to_request(self, row: sqlite3.Row) -> dict:
        """Строка requests + её lines -> dict с ключами из ТЗ."""
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
            "SELECT * FROM requests "
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
        status: str = "draft",
    ) -> int:
        """Создать или обновить заявку (upsert по UNIQUE(base, point, week_start, category)).

        Существующая запись обновляется (author_id, status, updated_at),
        её строки ЗАМЕНЯЮТСЯ (DELETE + INSERT). Возвращает id заявки.
        """
        self._validate_lines(lines)
        now = _now()
        existing = self._conn.execute(
            "SELECT id FROM requests "
            "WHERE base = ? AND point = ? AND week_start = ? AND category = ?",
            (base, point, week_start, category),
        ).fetchone()
        if existing:
            request_id = existing["id"]
            self._conn.execute(
                "UPDATE requests SET author_id = ?, status = ?, updated_at = ? "
                "WHERE id = ?",
                (author_id, status, now, request_id),
            )
            self._conn.execute(
                "DELETE FROM request_lines WHERE request_id = ?", (request_id,)
            )
        else:
            cur = self._conn.execute(
                "INSERT INTO requests "
                "(base, point, week_start, category, author_id, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (base, point, week_start, category, author_id, status, now, now),
            )
            request_id = cur.lastrowid
            assert request_id is not None  # INSERT только что прошёл
        self._conn.executemany(
            "INSERT INTO request_lines (request_id, item, unit, grp, qty) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                (
                    request_id,
                    line.get("item", ""),
                    line.get("unit", ""),
                    line.get("grp", ""),
                    line.get("qty", 0),
                )
                for line in lines
            ],
        )
        self._conn.commit()
        return request_id

    def set_status(self, request_id: int, status: str) -> None:
        """Сменить статус заявки (обновляет updated_at)."""
        self._conn.execute(
            "UPDATE requests SET status = ?, updated_at = ? WHERE id = ?",
            (status, _now(), request_id),
        )
        self._conn.commit()

    def list_requests(self, base: str, category: str, week_start: str) -> list[dict]:
        """Все заявки базы и раздела за неделю (с lines)."""
        rows = self._conn.execute(
            "SELECT * FROM requests WHERE base = ? AND week_start = ? AND category = ? "
            "ORDER BY point, id",
            (base, week_start, category),
        ).fetchall()
        return [self._row_to_request(r) for r in rows]

    def list_week(self, week_start: str) -> list[dict]:
        """Все заявки всех баз за неделю (с lines) — для доски старшей."""
        rows = self._conn.execute(
            "SELECT * FROM requests WHERE week_start = ? ORDER BY base, point",
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
        sql = "SELECT * FROM requests WHERE week_start >= ? AND week_start <= ?"
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
            "INSERT OR REPLACE INTO closures "
            "(base, week_start, category, closed_at, closed_by) "
            "VALUES (?, ?, ?, ?, ?)",
            (base, week_start, category, _now(), closed_by),
        )
        self._conn.commit()

    def is_closed(self, base: str, category: str, week_start: str) -> bool:
        """Закрыта ли неделя для базы и раздела."""
        row = self._conn.execute(
            "SELECT 1 FROM closures WHERE base = ? AND week_start = ? AND category = ?",
            (base, week_start, category),
        ).fetchone()
        return row is not None

    def reopen(self, base: str, category: str, week_start: str) -> None:
        """Открыть неделю заново для базы и раздела (удалить закрытие)."""
        self._conn.execute(
            "DELETE FROM closures WHERE base = ? AND week_start = ? AND category = ?",
            (base, week_start, category),
        )
        self._conn.commit()

    def list_closures(self) -> list[dict]:
        """Все закрытия недель (база, неделя, раздел, кто/когда)."""
        rows = self._conn.execute(
            "SELECT base, week_start, category, closed_at, closed_by FROM closures "
            "ORDER BY base, week_start, category"
        ).fetchall()
        return [dict(r) for r in rows]

    def closed_sections(self, week_start: str) -> list[dict]:
        """Закрытые разделы (base, category) за неделю — для отображения сёстрам."""
        rows = self._conn.execute(
            "SELECT base, category FROM closures WHERE week_start = ? "
            "ORDER BY base, category",
            (week_start,),
        ).fetchall()
        return [{"base": r["base"], "category": r["category"]} for r in rows]
