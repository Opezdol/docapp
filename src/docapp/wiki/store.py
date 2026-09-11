"""Хранилище подприложения «Компендиум» на SQLite.

Собственная база (data/wiki/wiki.db), основную docapp.db не трогаем.
Стиль — как в docapp/storage/sqlite_store.py и docapp/needs/store.py:
row_factory = sqlite3.Row, check_same_thread=False, PRAGMA foreign_keys=ON,
journal_mode=WAL. Таймстемпы — ISO-строки datetime.now().isoformat().

Модель данных:
- wiki_sources      — источник правды (загруженный PDF), неизменяем после OCR;
- wiki_articles     — курируемая статья (.md-тезисы), статус draft/published/archived;
- wiki_revisions    — версии статьи (версионирование + откат + публикация);
- wiki_article_links — связь статья -> источник (многие-ко-многим, с якорем);
- wiki_messages     — история QA + учёт токенов;
- wiki_settings     — настройки (системный промпт, top_k, температура, история).
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path

from docapp.core.db import Schema, open_db

_SCHEMA = """
CREATE TABLE IF NOT EXISTS wiki_sources (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    filename    TEXT NOT NULL,                     -- имя, как загрузили (человеку)
    doc_number  TEXT NOT NULL DEFAULT '',
    title       TEXT NOT NULL DEFAULT '',
    added_at    TEXT NOT NULL,
    uploaded_by INTEGER NOT NULL REFERENCES employees(id),
    page_count  INTEGER NOT NULL DEFAULT 0,
    ocr_status  TEXT NOT NULL DEFAULT 'pending',  -- pending/processing/done/error
    ocr_error   TEXT NOT NULL DEFAULT '',
    ocr_text    TEXT NOT NULL DEFAULT '',          -- распознанный текст
    tables_json TEXT NOT NULL DEFAULT '[]',        -- таблицы (JSON: list[list[list[str]]])
    stored_name TEXT NOT NULL DEFAULT ''           -- имя файла в хранилище (ADR-0020)
);
CREATE TABLE IF NOT EXISTS wiki_articles (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    title         TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'draft',   -- draft/published/archived
    published_revision_id INTEGER,                 -- «живая» ревизия для ответов
    created_by    INTEGER NOT NULL REFERENCES employees(id),
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS wiki_revisions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id  INTEGER NOT NULL REFERENCES wiki_articles(id) ON DELETE CASCADE,
    version     INTEGER NOT NULL,
    body_md     TEXT NOT NULL DEFAULT '',          -- .md-текст статьи
    rendered    TEXT NOT NULL DEFAULT '',          -- обычный текст (для поиска)
    edited_by   INTEGER NOT NULL REFERENCES employees(id),
    created_at  TEXT NOT NULL,
    change_note TEXT NOT NULL DEFAULT '',
    is_current  INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_rev_article_ver ON wiki_revisions(article_id, version);
CREATE INDEX IF NOT EXISTS idx_rev_article ON wiki_revisions(article_id);
CREATE TABLE IF NOT EXISTS wiki_article_links (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id INTEGER NOT NULL REFERENCES wiki_articles(id) ON DELETE CASCADE,
    source_id  INTEGER NOT NULL REFERENCES wiki_sources(id) ON DELETE CASCADE,
    anchor     TEXT NOT NULL DEFAULT ''            -- «стр. 3, п. 2.1»
);
CREATE INDEX IF NOT EXISTS idx_links_article ON wiki_article_links(article_id);
CREATE INDEX IF NOT EXISTS idx_links_source ON wiki_article_links(source_id);
CREATE TABLE IF NOT EXISTS wiki_messages (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id       INTEGER NOT NULL REFERENCES employees(id),
    conversation_id   TEXT NOT NULL,
    role              TEXT NOT NULL,               -- 'user' | 'assistant'
    content           TEXT NOT NULL,
    citations         TEXT NOT NULL DEFAULT '[]',  -- JSON
    prompt_tokens     INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_msg_conv ON wiki_messages(conversation_id, created_at);
CREATE INDEX IF NOT EXISTS idx_msg_emp  ON wiki_messages(employee_id, created_at);
CREATE TABLE IF NOT EXISTS wiki_settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);
"""

# Версия схемы БД «Компендиума».
SCHEMA_VERSION = 2

_MIGRATIONS: list[tuple[int, str, list[str]]] = [
    # (1, "initial schema", [])
    # v2 (ADR-0020): PDF-источник переехал из BLOB в файл на диске. Убрать
    # колонку `source` и добавить имя файла в хранилище. Пересборка через копию:
    # ALTER TABLE … DROP COLUMN требует SQLite ≥ 3.35, а на хостинге он старше.
    #
    # Внешние ключи выключаются на время пересборки: `wiki_article_links`
    # ссылается на `wiki_sources` с ON DELETE CASCADE, и обычный DROP TABLE
    # снёс бы ссылки на статьи. Обратно ключи включает core.db после миграций.
    (
        2,
        "PDF источников файлами на диске",
        [
            "PRAGMA foreign_keys = OFF",
            "CREATE TABLE wiki_sources_new ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "filename TEXT NOT NULL, "
            "doc_number TEXT NOT NULL DEFAULT '', "
            "title TEXT NOT NULL DEFAULT '', "
            "added_at TEXT NOT NULL, "
            "uploaded_by INTEGER NOT NULL REFERENCES employees(id), "
            "page_count INTEGER NOT NULL DEFAULT 0, "
            "ocr_status TEXT NOT NULL DEFAULT 'pending', "
            "ocr_error TEXT NOT NULL DEFAULT '', "
            "ocr_text TEXT NOT NULL DEFAULT '', "
            "tables_json TEXT NOT NULL DEFAULT '[]', "
            "stored_name TEXT NOT NULL DEFAULT '')",
            "INSERT INTO wiki_sources_new (id, filename, doc_number, title, added_at, "
            "uploaded_by, page_count, ocr_status, ocr_error, ocr_text, tables_json, "
            "stored_name) "
            "SELECT id, filename, doc_number, title, added_at, uploaded_by, page_count, "
            "ocr_status, ocr_error, ocr_text, tables_json, '' FROM wiki_sources",
            "CREATE TABLE wiki_article_links_new ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "article_id INTEGER NOT NULL REFERENCES wiki_articles(id) ON DELETE CASCADE, "
            "source_id INTEGER NOT NULL REFERENCES wiki_sources(id) ON DELETE CASCADE, "
            "anchor TEXT NOT NULL DEFAULT '')",
            "INSERT INTO wiki_article_links_new (id, article_id, source_id, anchor) "
            "SELECT id, article_id, source_id, anchor FROM wiki_article_links",
            "DROP TABLE wiki_article_links",
            "DROP TABLE wiki_sources",
            "ALTER TABLE wiki_sources_new RENAME TO wiki_sources",
            "ALTER TABLE wiki_article_links_new RENAME TO wiki_article_links",
            "CREATE INDEX IF NOT EXISTS idx_links_article ON wiki_article_links(article_id)",
            "CREATE INDEX IF NOT EXISTS idx_links_source ON wiki_article_links(source_id)",
        ],
    ),
]


def _connect(db_path: str | Path) -> sqlite3.Connection:
    """Открыть БД «Компендиума»: подключение, PRAGMA, схема, миграции — в core.db."""
    return open_db(db_path, SCHEMA)


#: Схема модуля «Компендиум» для общего механизма БД (core.db).
SCHEMA = Schema(
    module="wiki",
    sql=_SCHEMA,
    version=SCHEMA_VERSION,
    migrations=_MIGRATIONS,
)


def _next_day(day: str) -> str:
    """Следующий день после 'YYYY-MM-DD' — строгий верх диапазона 'по день'."""
    return (date.fromisoformat(day) + timedelta(days=1)).isoformat()


class SqliteWikiStore:
    """Источники, статьи, ревизии, связи, история QA и настройки в SQLite."""

    def __init__(self, db_path: str | Path) -> None:
        #: Папка с БД: рядом модуль держит свои файлы — например PDF-источники,
        #: которые по ADR-0020 лежат на диске, а не в базе.
        self.db_dir = Path(db_path).parent
        self._conn = _connect(db_path)

    def close(self) -> None:
        self._conn.close()

    def checkpoint(self) -> None:
        self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def __enter__(self) -> "SqliteWikiStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ── источники ─────────────────────────────────────────────────────

    def add_source(
        self,
        filename: str,
        doc_number: str,
        title: str,
        added_at: str,
        uploaded_by: int,
        stored_name: str = "",
        page_count: int = 0,
    ) -> int:
        """Записать источник. `filename` — имя для человека, `stored_name` — на диске."""
        cur = self._conn.execute(
            "INSERT INTO wiki_sources (filename, doc_number, title, added_at, "
            "uploaded_by, stored_name, page_count) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (filename, doc_number, title, added_at, uploaded_by, stored_name, page_count),
        )
        self._conn.commit()
        assert cur.lastrowid is not None
        return cur.lastrowid

    def get_source(self, source_id: int) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM wiki_sources WHERE id = ?", (source_id,)
        ).fetchone()

    def list_sources(self) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM wiki_sources ORDER BY added_at DESC, id DESC"
        ).fetchall()

    def set_ocr_result(
        self, source_id: int, ocr_text: str, tables_json: str, page_count: int
    ) -> None:
        self._conn.execute(
            "UPDATE wiki_sources SET ocr_text = ?, tables_json = ?, page_count = ?, "
            "ocr_status = 'done', ocr_error = '' WHERE id = ?",
            (ocr_text, tables_json, page_count, source_id),
        )
        self._conn.commit()

    def set_ocr_status(self, source_id: int, status: str, error: str = "") -> None:
        self._conn.execute(
            "UPDATE wiki_sources SET ocr_status = ?, ocr_error = ? WHERE id = ?",
            (status, error, source_id),
        )
        self._conn.commit()

    def delete_source(self, source_id: int) -> None:
        self._conn.execute("DELETE FROM wiki_sources WHERE id = ?", (source_id,))
        self._conn.commit()

    def stored_name_in_use(self, stored_name: str) -> bool:
        """Ссылается ли на файл хранилища хоть один источник.

        Один и тот же PDF (одинаковый хеш) может быть привязан к нескольким
        записям: файл удаляется только вместе с последней из них.
        """
        if not stored_name:
            return False
        row = self._conn.execute(
            "SELECT COUNT(*) AS c FROM wiki_sources WHERE stored_name = ?",
            (stored_name,),
        ).fetchone()
        return bool(row["c"])

    # ── статьи ────────────────────────────────────────────────────────

    def add_article(self, title: str, created_by: int, created_at: str) -> int:
        cur = self._conn.execute(
            "INSERT INTO wiki_articles (title, created_by, created_at, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (title, created_by, created_at, created_at),
        )
        self._conn.commit()
        assert cur.lastrowid is not None
        return cur.lastrowid

    def get_article(self, article_id: int) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM wiki_articles WHERE id = ?", (article_id,)
        ).fetchone()

    def list_articles(self, include_archived: bool = False) -> list[sqlite3.Row]:
        sql = "SELECT * FROM wiki_articles"
        if not include_archived:
            sql += " WHERE status != 'archived'"
        sql += " ORDER BY updated_at DESC, id DESC"
        return self._conn.execute(sql).fetchall()

    def list_published_articles(self) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM wiki_articles WHERE status = 'published' "
            "AND published_revision_id IS NOT NULL ORDER BY updated_at DESC, id DESC"
        ).fetchall()

    def set_title(self, article_id: int, title: str, updated_at: str) -> None:
        self._conn.execute(
            "UPDATE wiki_articles SET title = ?, updated_at = ? WHERE id = ?",
            (title, updated_at, article_id),
        )
        self._conn.commit()

    def set_article_status(self, article_id: int, status: str, updated_at: str) -> None:
        self._conn.execute(
            "UPDATE wiki_articles SET status = ?, updated_at = ? WHERE id = ?",
            (status, updated_at, article_id),
        )
        self._conn.commit()

    def set_published_revision(self, article_id: int, revision_id: int, updated_at: str) -> None:
        self._conn.execute(
            "UPDATE wiki_articles SET published_revision_id = ?, status = 'published', "
            "updated_at = ? WHERE id = ?",
            (revision_id, updated_at, article_id),
        )
        self._conn.commit()

    def delete_article(self, article_id: int) -> None:
        self._conn.execute("DELETE FROM wiki_articles WHERE id = ?", (article_id,))
        self._conn.commit()

    # ── ревизии ───────────────────────────────────────────────────────

    def add_revision(
        self,
        article_id: int,
        version: int,
        body_md: str,
        rendered: str,
        edited_by: int,
        created_at: str,
        change_note: str = "",
    ) -> int:
        # Новая ревизия становится текущей (is_current=1), прочие сбрасываются.
        self._conn.execute(
            "UPDATE wiki_revisions SET is_current = 0 WHERE article_id = ?", (article_id,)
        )
        cur = self._conn.execute(
            "INSERT INTO wiki_revisions (article_id, version, body_md, rendered, "
            "edited_by, created_at, change_note, is_current) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 1)",
            (article_id, version, body_md, rendered, edited_by, created_at, change_note),
        )
        self._conn.commit()
        assert cur.lastrowid is not None
        return cur.lastrowid

    def get_revision(self, revision_id: int) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM wiki_revisions WHERE id = ?", (revision_id,)
        ).fetchone()

    def current_revision(self, article_id: int) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM wiki_revisions WHERE article_id = ? AND is_current = 1",
            (article_id,),
        ).fetchone()

    def published_revision(self, article_id: int) -> sqlite3.Row | None:
        row = self._conn.execute(
            "SELECT published_revision_id FROM wiki_articles WHERE id = ?", (article_id,)
        ).fetchone()
        if row is None or row["published_revision_id"] is None:
            return None
        return self.get_revision(row["published_revision_id"])

    def list_revisions(self, article_id: int) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM wiki_revisions WHERE article_id = ? ORDER BY version DESC",
            (article_id,),
        ).fetchall()

    def next_version(self, article_id: int) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(MAX(version), 0) AS m FROM wiki_revisions WHERE article_id = ?",
            (article_id,),
        ).fetchone()
        return row["m"] + 1

    # ── связи статья -> источник ──────────────────────────────────────

    def add_link(self, article_id: int, source_id: int, anchor: str = "") -> None:
        self._conn.execute(
            "INSERT INTO wiki_article_links (article_id, source_id, anchor) VALUES (?, ?, ?)",
            (article_id, source_id, anchor),
        )
        self._conn.commit()

    def clear_links(self, article_id: int) -> None:
        self._conn.execute(
            "DELETE FROM wiki_article_links WHERE article_id = ?", (article_id,)
        )
        self._conn.commit()

    def list_links(self, article_id: int) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT l.*, s.filename, s.doc_number, s.title AS source_title "
            "FROM wiki_article_links l JOIN wiki_sources s ON s.id = l.source_id "
            "WHERE l.article_id = ? ORDER BY l.id",
            (article_id,),
        ).fetchall()

    def articles_for_source(self, source_id: int) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT a.* FROM wiki_article_links l JOIN wiki_articles a ON a.id = l.article_id "
            "WHERE l.source_id = ? ORDER BY a.updated_at DESC",
            (source_id,),
        ).fetchall()

    # ── сообщения и аналитика ─────────────────────────────────────────

    def add_message(
        self,
        employee_id: int,
        conversation_id: str,
        role: str,
        content: str,
        citations_json: str,
        prompt_tokens: int,
        completion_tokens: int,
        created_at: str,
    ) -> int:
        cur = self._conn.execute(
            "INSERT INTO wiki_messages (employee_id, conversation_id, role, content, "
            "citations, prompt_tokens, completion_tokens, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                employee_id,
                conversation_id,
                role,
                content,
                citations_json,
                prompt_tokens,
                completion_tokens,
                created_at,
            ),
        )
        self._conn.commit()
        assert cur.lastrowid is not None
        return cur.lastrowid

    def list_messages(self, conversation_id: str) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM wiki_messages WHERE conversation_id = ? ORDER BY created_at, id",
            (conversation_id,),
        ).fetchall()

    def list_conversations(self, employee_id: int) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT m.conversation_id, m.content, m.created_at "
            "FROM wiki_messages m "
            "JOIN (SELECT conversation_id, MAX(id) AS max_id FROM wiki_messages "
            "      WHERE employee_id = ? GROUP BY conversation_id) last "
            "  ON m.id = last.max_id "
            "ORDER BY m.created_at DESC, m.id DESC",
            (employee_id,),
        ).fetchall()

    def token_totals(
        self, from_date: str | None = None, to_date: str | None = None
    ) -> list[sqlite3.Row]:
        sql = (
            "SELECT employee_id, SUM(prompt_tokens) AS total_prompt, "
            "SUM(completion_tokens) AS total_completion, COUNT(*) AS count "
            "FROM wiki_messages"
        )
        params: list[str] = []
        conditions: list[str] = []
        if from_date:
            conditions.append("created_at >= ?")
            params.append(from_date)
        if to_date:
            conditions.append("created_at < ?")
            params.append(_next_day(to_date))
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " GROUP BY employee_id ORDER BY employee_id"
        return self._conn.execute(sql, params).fetchall()

    def token_totals_by_day(
        self, from_date: str | None = None, to_date: str | None = None
    ) -> list[sqlite3.Row]:
        sql = (
            "SELECT substr(created_at, 1, 10) AS day, "
            "SUM(prompt_tokens) AS total_prompt, "
            "SUM(completion_tokens) AS total_completion, COUNT(*) AS count "
            "FROM wiki_messages"
        )
        params: list[str] = []
        conditions: list[str] = []
        if from_date:
            conditions.append("created_at >= ?")
            params.append(from_date)
        if to_date:
            conditions.append("created_at < ?")
            params.append(_next_day(to_date))
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " GROUP BY day ORDER BY day"
        return self._conn.execute(sql, params).fetchall()

    # ── настройки ─────────────────────────────────────────────────────

    def get_setting(self, key: str) -> str | None:
        row = self._conn.execute(
            "SELECT value FROM wiki_settings WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None

    def set_setting(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO wiki_settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self._conn.commit()
