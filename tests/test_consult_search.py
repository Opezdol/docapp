"""Тесты гибридного поиска: RRF, косинус + BM25, метаданные, пустой индекс.

Эмбеддинги — детерминированные float32-векторы, без сети и моделей.
"""

import numpy as np
import pytest

from docapp.consult.search import SearchIndex, rrf
from docapp.consult.store import SqliteConsultStore


def emb(values):
    """Эмбеддинг float32 -> байты для хранения в SQLite."""
    return np.array(values, dtype=np.float32).tobytes()


@pytest.fixture
def store_factory(tmp_path):
    """Фабрика хранилища во временной БД; все хранилища закрываются в конце теста."""
    stores = []

    def factory():
        s = SqliteConsultStore(tmp_path / "consult.db")
        stores.append(s)
        return s

    yield factory
    for s in stores:
        s.close()


def build_filled_store(store_factory):
    """Хранилище с 3 документами и 4 чанками (неортогональные эмбеддинги).

    Векторы подобраны так, что query [1.0, 0.05, 0.0] косинусно ближе всего
    к doc2/chunk0; тексты пересекаются по словам — BM25 даёт свой сигнал.
    """
    store = store_factory()
    d1 = store.add_document("prikaz-001.docx", "001", "О графике работы", "2026-08-01T10:00:00")
    d2 = store.add_document("prikaz-002.docx", "002", "Об отпусках", "2026-08-01T11:00:00")
    d3 = store.add_document("prikaz-003.docx", "003", "О больничных", "2026-08-01T12:00:00")
    store.add_chunks(
        d1,
        [(0, "Раздел 1", "Приказ о графике работы и дежурствах", emb([0.3, 0.9, 0.0]))],
    )
    store.add_chunks(
        d2,
        [
            (0, "Раздел 1", "Правила предоставления отпусков работникам", emb([0.9, 0.1, 0.0])),
            (1, "Раздел 2", "График отпусков утверждается руководителем", emb([0.0, 1.0, 0.0])),
        ],
    )
    store.add_chunks(
        d3,
        [(0, "Раздел 1", "Порядок оформления больничных листов", emb([0.0, 0.0, 1.0]))],
    )
    return store


def chunk_id(store, document_id, chunk_index):
    """id чанка по документу и индексу — не полагаемся на автоинкремент."""
    for row in store.get_all_chunks():
        if row["document_id"] == document_id and row["chunk_index"] == chunk_index:
            return row["id"]
    raise AssertionError(f"чанк (doc={document_id}, idx={chunk_index}) не найден")


class TestRrf:
    def test_rrf_merges(self):
        # "b" есть в обоих списках — суммарный скор выше, чем у одиночных "a" и "c"
        assert rrf([["a", "b"], ["b", "c"]])[0][0] == "b"

    def test_rrf_sorts_descending(self):
        result = rrf([["x", "y"], ["y", "x"]])
        scores = [score for _, score in result]
        assert scores == sorted(scores, reverse=True)


class TestSearchIndex:
    def test_search_cosine_top(self, store_factory):
        store = build_filled_store(store_factory)
        index = SearchIndex(store)
        doc2_chunk0 = chunk_id(store, 2, 0)  # документ "002 Об отпусках", чанк 0

        results = index.search("отпусков работникам", [1.0, 0.05, 0.0])

        assert results[0]["chunk_id"] == doc2_chunk0

    def test_search_returns_metadata(self, store_factory):
        store = build_filled_store(store_factory)
        index = SearchIndex(store)

        results = index.search("отпусков", [0.9, 0.1, 0.0])

        top = results[0]
        assert top["doc_number"] == "002"
        assert top["doc_title"] == "Об отпусках"
        assert top["section"] == "Раздел 1"
        assert top["text"] == "Правила предоставления отпусков работникам"
        assert top["score"] > 0

    def test_search_bm25_helps(self, store_factory):
        # вектор запроса далёк от doc3/chunk0 ([0,0,1]), но текст совпадает —
        # BM25 поднимает чанк в топ, и RRF его сохраняет
        store = build_filled_store(store_factory)
        index = SearchIndex(store)
        doc3_chunk0 = chunk_id(store, 3, 0)

        results = index.search("больничных листов оформление", [1.0, 0.05, 0.0], n=3)

        found = [r["chunk_id"] for r in results]
        assert doc3_chunk0 in found

    def test_search_respects_n(self, store_factory):
        store = build_filled_store(store_factory)
        index = SearchIndex(store)

        assert len(index.search("отпусков", [1.0, 0.05, 0.0], n=2)) == 2

    def test_search_empty_index(self, store_factory):
        store = store_factory()  # документов и чанков нет
        index = SearchIndex(store)

        assert index.dim == 0
        assert index.search("любой запрос", [1.0, 0.0, 0.0]) == []

    def test_dim_matches_embedding_size(self, store_factory):
        store = build_filled_store(store_factory)
        index = SearchIndex(store)

        assert index.dim == 3
