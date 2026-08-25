"""Гибридный поиск по чанкам консультанта.

Косинусная похожесть эмбеддингов (numpy) + BM25 по тексту,
результаты двух рангов объединяются через Reciprocal Rank Fusion (RRF).
"""

import re
from dataclasses import dataclass

import numpy as np
from rank_bm25 import BM25Okapi

from docapp.consult.store import SqliteConsultStore


def rrf(ranked_lists: list[list[str]], k: int = 60) -> list[tuple[str, float]]:
    """Слить ранжированные списки id через Reciprocal Rank Fusion.

    score(item) += 1 / (k + rank + 1); результат отсортирован по убыванию.
    """
    scores: dict[str, float] = {}
    for ranked in ranked_lists:
        for rank, item in enumerate(ranked):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)


def _tokenize(text: str) -> list[str]:
    """Токенизация для BM25: слова (в т.ч. кириллица) и числа в нижнем регистре."""
    return re.findall(r"[a-zа-яё0-9]+", text.lower())


@dataclass
class _Chunk:
    """Чанк в памяти индекса."""

    id: int
    document_id: int
    chunk_index: int
    section: str
    text: str
    embedding: np.ndarray


class SearchIndex:
    """Индекс чанков в памяти: эмбеддинги (numpy) + BM25 по тексту."""

    def __init__(self, store: SqliteConsultStore) -> None:
        self._chunks: list[_Chunk] = []
        self._docs: dict[int, tuple[str, str]] = {}
        self._matrix: np.ndarray = np.empty((0, 0), dtype=np.float32)
        self._bm25: BM25Okapi | None = None

        # мапа document_id -> (doc_number, doc_title)
        for row in store.list_documents():
            self._docs[row["id"]] = (row["doc_number"], row["title"])

        # чанки с эмбеддингами
        for row in store.get_all_chunks():
            self._chunks.append(
                _Chunk(
                    id=row["id"],
                    document_id=row["document_id"],
                    chunk_index=row["chunk_index"],
                    section=row["section"],
                    text=row["text"],
                    embedding=np.frombuffer(row["embedding"], dtype=np.float32),
                )
            )

        if self._chunks:
            # матрица N x dim, строки нормированы — косинус = dot product
            self._matrix = np.vstack([c.embedding for c in self._chunks])
            norms = np.linalg.norm(self._matrix, axis=1, keepdims=True)
            self._matrix = self._matrix / np.where(norms == 0, 1.0, norms)
            self._bm25 = BM25Okapi([_tokenize(c.text) for c in self._chunks])

    @property
    def dim(self) -> int:
        """Размерность эмбеддингов (0, если индекс пуст)."""
        return 0 if self._matrix.size == 0 else self._matrix.shape[1]

    def search(self, query_text: str, query_vec: list[float], n: int = 5) -> list[dict]:
        """Гибридный поиск: cosine + BM25, слияние RRF, топ n результатов.

        Возвращает список dict: chunk_id, text, section, doc_number,
        doc_title, score (RRF-скор).
        """
        if not self._chunks:
            return []
        assert self._bm25 is not None  # индекс не пуст — BM25 построен

        pool = max(n * 2, 8)

        # векторный ранг: косинусная похожесть по всем чанкам
        q = np.asarray(query_vec, dtype=np.float32)
        q_norm = np.linalg.norm(q)
        q = q / q_norm if q_norm > 0 else q
        sims = self._matrix @ q
        vec_rank = [str(self._chunks[i].id) for i in np.argsort(sims)[::-1][:pool]]

        # BM25 ранг по всему корпусу
        scores = self._bm25.get_scores(_tokenize(query_text))
        bm_rank = [str(self._chunks[i].id) for i in np.argsort(scores)[::-1][:pool]]

        # слияние рангов и топ n
        fused = rrf([vec_rank, bm_rank])[:n]
        by_id = {str(c.id): c for c in self._chunks}

        results = []
        for chunk_id, score in fused:
            chunk = by_id[chunk_id]
            doc_number, doc_title = self._docs.get(chunk.document_id, ("", ""))
            results.append(
                {
                    "chunk_id": chunk.id,
                    "document_id": chunk.document_id,
                    "text": chunk.text,
                    "section": chunk.section,
                    "doc_number": doc_number,
                    "doc_title": doc_title,
                    "score": score,
                }
            )
        return results
