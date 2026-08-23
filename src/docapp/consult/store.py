"""Хранилище консультанта на SQLite: документы, чанки с эмбеддингами, сообщения.

Отдельный файл БД (data/consult/consult.db, переносится между машинами),
основная docapp.db не затрагивается. Схема — строго из ТЗ-консультанта (раздел 4).
"""

import sqlite3
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    filename    TEXT NOT NULL,
    doc_number  TEXT NOT NULL DEFAULT '',
    title       TEXT NOT NULL DEFAULT '',
    added_at    TEXT NOT NULL,
    full_text   TEXT NOT NULL DEFAULT '',   -- полный текст (чтение «Приказа» целиком)
    source      BLOB,                        -- оригинальный файл (.docx/.pdf)
    source_name TEXT NOT NULL DEFAULT ''     -- имя файла для скачивания
);
CREATE TABLE IF NOT EXISTS chunks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    section     TEXT NOT NULL DEFAULT '',
    text        TEXT NOT NULL,
    embedding   BLOB NOT NULL               -- float32 numpy
);
CREATE TABLE IF NOT EXISTS messages (        -- история диалогов + учёт токенов
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id       INTEGER NOT NULL,
    conversation_id   TEXT NOT NULL,        -- UUID, генерится на клиенте
    role              TEXT NOT NULL,        -- 'user' | 'assistant'
    content           TEXT NOT NULL,
    citations         TEXT NOT NULL DEFAULT '[]',   -- JSON
    prompt_tokens     INTEGER NOT NULL DEFAULT 0,   -- токены входа
    completion_tokens INTEGER NOT NULL DEFAULT 0,   -- токены выхода
    created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(document_id);
CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conversation_id, created_at);
CREATE INDEX IF NOT EXISTS idx_msg_emp  ON messages(employee_id, created_at);
"""


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


# Версия схемы БД консультанта. Увеличивайте при изменении схемы
# и добавляйте миграцию в _MIGRATIONS.
SCHEMA_VERSION = 1

# Миграции: (версия_после_применения, название, [SQL...])
_MIGRATIONS: list[tuple[int, str, list[str]]] = [
    # (1, "initial schema", [])
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


class SqliteConsultStore:
    """Индекс приказов и история диалогов в SQLite-файле."""

    def __init__(self, db_path: str | Path) -> None:
        self._conn = _connect(db_path)

    def close(self) -> None:
        self._conn.close()

    def checkpoint(self) -> None:
        """Сбросить WAL в основной файл (перед атомарной заменой файла БД).

        PRAGMA wal_checkpoint(TRUNCATE): страницы WAL записываются в главный
        файл, WAL обнуляется — осиротевший сайдкар после os.replace не сможет
        «воскреснуть» в новом файле (иначе дублируются сообщения при миграции).
        """
        self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def __enter__(self) -> "SqliteConsultStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def add_document(
        self,
        filename: str,
        doc_number: str,
        title: str,
        added_at: str,
        full_text: str = "",
        source: bytes | None = None,
        source_name: str = "",
    ) -> int:
        """Добавить документ, вернуть его id.

        full_text — полный текст приказа для страницы «Читать»; source — байты
        оригинального файла (.docx/.pdf) для скачивания; source_name — имя файла.
        """
        cur = self._conn.execute(
            "INSERT INTO documents (filename, doc_number, title, added_at, "
            "full_text, source, source_name) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (filename, doc_number, title, added_at, full_text, source, source_name),
        )
        self._conn.commit()
        assert cur.lastrowid is not None
        return cur.lastrowid

    def get_document(self, document_id: int) -> sqlite3.Row | None:
        """Документ по id со всеми полями (включая full_text/source) или None."""
        return self._conn.execute(
            "SELECT * FROM documents WHERE id = ?", (document_id,)
        ).fetchone()

    def add_chunks(self, document_id: int, chunks: list[tuple[int, str, str, bytes]]) -> None:
        """Добавить чанки документа: (chunk_index, section, text, embedding)."""
        self._conn.executemany(
            "INSERT INTO chunks (document_id, chunk_index, section, text, embedding) "
            "VALUES (?, ?, ?, ?, ?)",
            [(document_id, idx, section, text, embedding) for idx, section, text, embedding in chunks],
        )
        self._conn.commit()

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
        """Сохранить сообщение диалога, вернуть его id."""
        cur = self._conn.execute(
            "INSERT INTO messages "
            "(employee_id, conversation_id, role, content, citations, prompt_tokens, completion_tokens, created_at) "
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
        """Все сообщения беседы по возрастанию created_at/id (для истории и контекста)."""
        return self._conn.execute(
            "SELECT * FROM messages WHERE conversation_id = ? "
            "ORDER BY created_at, id",
            (conversation_id,),
        ).fetchall()

    def all_messages(self) -> list[sqlite3.Row]:
        """Все сообщения диалогов (для миграции истории при пересборке индекса)."""
        return self._conn.execute("SELECT * FROM messages ORDER BY id").fetchall()

    def list_conversations(self, employee_id: int) -> list[sqlite3.Row]:
        """Беседы сотрудника: conversation_id, последнее сообщение, created_at.

        Свежие беседы сверху — для восстановления диалога при открытии страницы.
        """
        return self._conn.execute(
            "SELECT m.conversation_id, m.content, m.created_at "
            "FROM messages m "
            "JOIN (SELECT conversation_id, MAX(id) AS max_id FROM messages "
            "      WHERE employee_id = ? GROUP BY conversation_id) last "
            "  ON m.id = last.max_id "
            "ORDER BY m.created_at DESC, m.id DESC",
            (employee_id,),
        ).fetchall()

    def get_all_chunks(self) -> list[sqlite3.Row]:
        """Все чанки с эмбеддингами — загрузка индекса в память (numpy) при старте."""
        return self._conn.execute(
            "SELECT id, document_id, chunk_index, section, text, embedding "
            "FROM chunks ORDER BY document_id, chunk_index"
        ).fetchall()

    def list_documents(self) -> list[sqlite3.Row]:
        """Список документов индекса (для страницы «Документы»)."""
        return self._conn.execute(
            "SELECT * FROM documents ORDER BY added_at DESC, id DESC"
        ).fetchall()

    def count_chunks(self) -> int:
        """Число чанков в индексе."""
        row = self._conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()
        return row["n"]

    def token_totals(self) -> list[sqlite3.Row]:
        """Расход токенов по сотрудникам для аналитики заведующего.

        Каждая строка: employee_id, total_prompt, total_completion, count.
        """
        return self._conn.execute(
            "SELECT employee_id, "
            "       SUM(prompt_tokens) AS total_prompt, "
            "       SUM(completion_tokens) AS total_completion, "
            "       COUNT(*) AS count "
            "FROM messages GROUP BY employee_id ORDER BY employee_id"
        ).fetchall()
