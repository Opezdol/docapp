"""Тесты хранилища PDF-источников на диске (ADR-0020, шаг 4c).

Проверяется то, ради чего PDF вынесли из БД: файл лежит на диске, имя не несёт
лишних сведений, одинаковые файлы не дублируются, а удаление источника убирает
и файл.
"""

from __future__ import annotations

import sqlite3

import pytest

from docapp.core.db import open_db
from docapp.wiki import files
from docapp.wiki.store import SCHEMA, SqliteWikiStore


class TestStorage:
    def test_stores_file_under_hash_name(self, tmp_path):
        """Имя на диске — хеш содержимого, а не имя, под которым загрузили."""
        sources = tmp_path / "sources"
        name = files.store("%PDF-1.4 текст".encode("utf-8"), sources)

        assert name.endswith(".pdf")
        assert len(name) == files.DIGEST_LENGTH + len(files.SUFFIX)
        assert "приказ" not in name
        assert (sources / name).read_bytes() == "%PDF-1.4 текст".encode("utf-8")

    def test_original_name_does_not_reach_disk(self, tmp_path):
        """PDF, названный по пациенту, не оставляет ФИО в файловой системе."""
        sources = tmp_path / "sources"
        name = files.store(b"%PDF-1.4", sources)
        assert all("Иванов" not in p.name for p in sources.iterdir())

    def test_same_content_is_not_duplicated(self, tmp_path):
        sources = tmp_path / "sources"
        first = files.store("%PDF-1.4 одинаковый".encode("utf-8"), sources)
        second = files.store("%PDF-1.4 одинаковый".encode("utf-8"), sources)

        assert first == second
        assert len(list(sources.iterdir())) == 1

    def test_different_content_gives_different_names(self, tmp_path):
        sources = tmp_path / "sources"
        assert files.store(b"%PDF-1.4 a", sources) != files.store(b"%PDF-1.4 b", sources)

    def test_read_and_remove(self, tmp_path):
        sources = tmp_path / "sources"
        name = files.store(b"%PDF-1.4", sources)

        assert files.read(sources, name) == b"%PDF-1.4"
        assert files.exists(sources, name)
        files.remove(sources, name)
        assert not files.exists(sources, name)
        files.remove(sources, name)  # повторно — не ошибка

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(files.SourceFileError):
            files.read(tmp_path / "sources", "нет-такого.pdf")

    def test_name_with_path_is_rejected(self, tmp_path):
        """Имя приходит из БД, но в путь попадает — переходы вверх запрещены."""
        for bad in ("../secret.key", "sub/dir.pdf", "", "."):
            with pytest.raises(files.SourceFileError):
                files.path_of(tmp_path / "sources", bad)

    def test_interrupted_write_leaves_no_partial_file(self, tmp_path):
        """Временный файл не остаётся даже при ошибке записи."""
        sources = tmp_path / "sources"
        files.store(b"%PDF-1.4", sources)
        assert not [p for p in sources.iterdir() if p.name.startswith(".")]


class TestStoreKeepsFileNotBlob:
    def test_schema_has_no_blob_column(self, tmp_path):
        """В схеме нет колонки с PDF — только имя файла в хранилище."""
        conn = open_db(tmp_path / "app.db", SCHEMA)
        try:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(wiki_sources)")}
        finally:
            conn.close()
        assert "source" not in columns
        assert "stored_name" in columns


V1_SCHEMA = """
CREATE TABLE employees (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    last_name TEXT NOT NULL,
    first_name TEXT NOT NULL,
    middle_name TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL,
    login TEXT UNIQUE,
    password_hash TEXT,
    buh_id TEXT
);
CREATE TABLE wiki_sources (
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
CREATE TABLE wiki_articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'draft',
    published_revision_id INTEGER,
    created_by INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE wiki_article_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id INTEGER NOT NULL REFERENCES wiki_articles(id) ON DELETE CASCADE,
    source_id INTEGER NOT NULL REFERENCES wiki_sources(id) ON DELETE CASCADE,
    anchor TEXT NOT NULL DEFAULT ''
);
"""


class TestMigrationToFiles:
    """Прежняя схема (PDF в BLOB) доводится до схемы с файлом на диске."""

    def _build_v1(self, tmp_path) -> str:
        db = tmp_path / "app.db"
        conn = sqlite3.connect(str(db))
        conn.executescript(V1_SCHEMA)
        conn.execute(
            "INSERT INTO employees (id, last_name, first_name, role) VALUES (1, 'Иванов', 'Иван', 'doctor')"
        )
        conn.execute(
            "INSERT INTO wiki_sources (id, filename, doc_number, title, added_at, "
            "uploaded_by, ocr_text, source) VALUES (1, 'prikaz.pdf', '№1', 'Приказ', "
            "'2026-03-01T09:00:00', 1, 'распознанный текст', ?)",
            ("%PDF-1.4 старый".encode("utf-8"),),
        )
        conn.execute(
            "INSERT INTO wiki_articles (id, title, status, created_by, created_at, updated_at) "
            "VALUES (1, 'Статья', 'draft', 1, '2026-03-01T09:00:00', '2026-03-01T09:00:00')"
        )
        conn.execute(
            "INSERT INTO wiki_article_links (id, article_id, source_id, anchor) "
            "VALUES (1, 1, 1, 'стр. 1')"
        )
        conn.execute("PRAGMA user_version = 1")
        conn.commit()
        conn.close()
        return str(db)

    def test_links_survive_and_blob_column_goes(self, tmp_path):
        """Ссылки на статьи не теряются при пересборке (каскад не должен сработать)."""
        db = self._build_v1(tmp_path)
        store = SqliteWikiStore(db)
        try:
            row = store.get_source(1)
            assert row["title"] == "Приказ"
            assert row["ocr_text"] == "распознанный текст"
            assert "source" not in row.keys()
            assert row["stored_name"] == ""

            links = store._conn.execute("SELECT * FROM wiki_article_links").fetchall()
            assert len(links) == 1
            assert links[0]["article_id"] == 1
            assert links[0]["source_id"] == 1

            versions = [
                row["version"]
                for row in store._conn.execute(
                    "SELECT version FROM schema_migrations WHERE module = 'wiki'"
                ).fetchall()
            ]
            # строка «1» — версия, перенесённая из PRAGMA user_version старой БД,
            # «2» — применённая миграция на файловое хранилище
            assert max(versions) == 2
        finally:
            store.close()

    def test_keys_stay_on_after_migration(self, tmp_path):
        """Миграция выключает внешние ключи — обратно их включает core.db."""
        db = self._build_v1(tmp_path)
        store = SqliteWikiStore(db)
        try:
            enabled = store._conn.execute("PRAGMA foreign_keys").fetchone()[0]
            assert enabled == 1
            with pytest.raises(sqlite3.IntegrityError):
                store._conn.execute(
                    "INSERT INTO wiki_article_links (article_id, source_id) VALUES (1, 999)"
                )
        finally:
            store.close()
