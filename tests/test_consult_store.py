"""Тесты хранилища консультанта: документы, чанки с эмбеддингами, сообщения."""

import sqlite3

import pytest

from docapp.consult.store import SqliteConsultStore


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "consult.db"


@pytest.fixture
def store(db_path):
    s = SqliteConsultStore(db_path)
    yield s
    s.close()


def add_doc(store, **overrides):
    base = dict(filename="prikaz-001.docx", doc_number="001", title="О графике", added_at="2026-08-01T10:00:00")
    base.update(overrides)
    return store.add_document(**base)


class TestSchema:
    def test_tables_exist(self, db_path, store):
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            tables = {
                r["name"]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
        assert {"documents", "chunks", "messages"} <= tables

    def test_indexes_exist(self, db_path, store):
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            indexes = {
                r["name"]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'index'"
                ).fetchall()
            }
        assert {"idx_chunks_doc", "idx_msg_conv", "idx_msg_emp"} <= indexes


class TestDocuments:
    def test_add_document_returns_id(self, store):
        doc_id = add_doc(store)
        assert doc_id is not None
        assert doc_id > 0

    def test_list_documents_roundtrip(self, store):
        add_doc(store, filename="prikaz-001.docx", doc_number="001", title="О графике")
        add_doc(store, filename="prikaz-002.docx", doc_number="002", title="Об отпусках")
        rows = store.list_documents()
        assert len(rows) == 2
        first = rows[0]
        assert first["filename"] == "prikaz-002.docx"  # свежие сверху
        assert first["doc_number"] == "002"
        assert first["title"] == "Об отпусках"

    def test_defaults_for_doc_number_and_title(self, store):
        doc_id = store.add_document("prikaz-003.docx", "", "", "2026-08-02T09:00:00")
        rows = store.list_documents()
        row = [r for r in rows if r["id"] == doc_id][0]
        assert row["doc_number"] == ""
        assert row["title"] == ""


class TestChunks:
    def test_add_chunks_and_count(self, store):
        doc_id = add_doc(store)
        store.add_chunks(doc_id, [(0, "Раздел 1", "Текст фрагмента", b"\x00\x01")])
        assert store.count_chunks() == 1

    def test_get_all_chunks_keeps_embedding_bytes(self, store):
        doc_id = add_doc(store)
        embedding = bytes(range(256))
        store.add_chunks(
            doc_id,
            [
                (0, "Раздел 1", "Первый фрагмент", embedding),
                (1, "Раздел 2", "Второй фрагмент", b"\xaa\xbb"),
            ],
        )
        chunks = store.get_all_chunks()
        assert len(chunks) == 2
        first = chunks[0]
        assert first["document_id"] == doc_id
        assert first["chunk_index"] == 0
        assert first["section"] == "Раздел 1"
        assert first["text"] == "Первый фрагмент"
        assert first["embedding"] == embedding  # BLOB сохраняется байт-в-байт

    def test_foreign_key_cascade_deletes_chunks(self, db_path, store):
        doc_id = add_doc(store)
        store.add_chunks(doc_id, [(0, "", "Фрагмент", b"\x01")])
        with sqlite3.connect(db_path) as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
        assert store.count_chunks() == 0

    def test_chunk_requires_existing_document(self, store):
        with pytest.raises(sqlite3.IntegrityError):
            store.add_chunks(999, [(0, "", "Фрагмент", b"\x01")])


class TestMessages:
    def test_add_message_and_list_in_order(self, store):
        m1 = store.add_message(1, "conv-1", "user", "Привет", "[]", 10, 0, "2026-08-03T10:00:00")
        m2 = store.add_message(1, "conv-1", "assistant", "Здравствуйте", '[{"doc": "001"}]', 12, 34, "2026-08-03T10:00:01")
        rows = store.list_messages("conv-1")
        assert [r["id"] for r in rows] == [m1, m2]
        assert rows[0]["role"] == "user"
        assert rows[1]["content"] == "Здравствуйте"
        assert rows[1]["citations"] == '[{"doc": "001"}]'
        assert rows[1]["prompt_tokens"] == 12
        assert rows[1]["completion_tokens"] == 34

    def test_list_messages_filters_by_conversation(self, store):
        store.add_message(1, "conv-1", "user", "Вопрос", "[]", 0, 0, "2026-08-03T10:00:00")
        store.add_message(1, "conv-2", "user", "Другой вопрос", "[]", 0, 0, "2026-08-03T10:00:00")
        assert len(store.list_messages("conv-1")) == 1

    def test_default_tokens_and_citations(self, store):
        store.add_message(1, "conv-1", "user", "Вопрос", "[]", 0, 0, "2026-08-03T10:00:00")
        row = store.list_messages("conv-1")[0]
        assert row["citations"] == "[]"
        assert row["prompt_tokens"] == 0
        assert row["completion_tokens"] == 0


class TestConversations:
    def test_list_conversations_latest_message(self, store):
        store.add_message(1, "conv-1", "user", "Первый вопрос", "[]", 0, 0, "2026-08-03T10:00:00")
        store.add_message(1, "conv-1", "assistant", "Первый ответ", "[]", 5, 7, "2026-08-03T10:00:01")
        store.add_message(1, "conv-2", "user", "Второй вопрос", "[]", 0, 0, "2026-08-03T11:00:00")
        rows = store.list_conversations(1)
        assert [r["conversation_id"] for r in rows] == ["conv-2", "conv-1"]  # свежие сверху
        assert rows[1]["content"] == "Первый ответ"  # последнее сообщение беседы
        assert rows[1]["created_at"] == "2026-08-03T10:00:01"

    def test_list_conversations_filters_by_employee(self, store):
        store.add_message(1, "conv-1", "user", "Вопрос врача", "[]", 0, 0, "2026-08-03T10:00:00")
        store.add_message(2, "conv-2", "user", "Вопрос сестры", "[]", 0, 0, "2026-08-03T10:00:00")
        rows = store.list_conversations(1)
        assert [r["conversation_id"] for r in rows] == ["conv-1"]

    def test_list_conversations_empty(self, store):
        assert store.list_conversations(1) == []


class TestTokenTotals:
    def test_aggregates_per_employee(self, store):
        store.add_message(1, "c1", "user", "q1", "[]", 100, 10, "2026-08-03T10:00:00")
        store.add_message(1, "c1", "assistant", "a1", "[]", 100, 20, "2026-08-03T10:00:01")
        store.add_message(1, "c2", "user", "q2", "[]", 50, 5, "2026-08-03T11:00:00")
        store.add_message(2, "c3", "user", "q3", "[]", 30, 3, "2026-08-03T12:00:00")
        rows = store.token_totals()
        by_emp = {r["employee_id"]: r for r in rows}
        assert by_emp[1]["total_prompt"] == 250
        assert by_emp[1]["total_completion"] == 35
        assert by_emp[1]["count"] == 3
        assert by_emp[2]["total_prompt"] == 30
        assert by_emp[2]["total_completion"] == 3
        assert by_emp[2]["count"] == 1

    def test_token_totals_empty(self, store):
        assert store.token_totals() == []


class TestDocumentFullText:
    def test_add_document_with_full_text_and_source(self, store):
        doc_id = store.add_document(
            "x.docx", "1", "T", "2026-08-01",
            full_text="текст", source=b"%PDF-1.4", source_name="x.docx",
        )
        row = store.get_document(doc_id)
        assert row is not None
        assert row["full_text"] == "текст"
        assert row["source"] == b"%PDF-1.4"
        assert row["source_name"] == "x.docx"

    def test_defaults_empty(self, store):
        doc_id = store.add_document("y.docx", "", "", "2026-08-01")
        row = store.get_document(doc_id)
        assert row is not None
        assert row["full_text"] == ""
        assert row["source"] is None
        assert row["source_name"] == ""
