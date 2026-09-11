"""Поиск по опубликованным секциям .md-статей «Чата».

BM25 по тексту секций + буст совпадения в заголовке. Корпус мал (десятки-
сотни секций) — эмбеддинги не нужны. Секции — осмысленные разделы .md,
а не 600-символьные чанки PDF (принципиальное отличие от прежнего RAG).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from rank_bm25 import BM25Okapi


def _tokenize(text: str) -> list[str]:
    """Слова (в т.ч. кириллица) и числа в нижнем регистре."""
    return re.findall(r"[a-zа-яё0-9]+", text.lower())


@dataclass
class _Section:
    id: str
    article_id: int
    article_title: str
    title: str
    body: str
    text: str  # title + body для BM25


class WikiSearch:
    """Индекс опубликованных секций в памяти: BM25 по тексту + буст заголовка."""

    def __init__(self, sections: list[dict]) -> None:
        """sections: list[{article_id, article_title, title, body}]."""
        self._sections: list[_Section] = []
        self._bm25: BM25Okapi | None = None
        self._corpus: list[str] = []

        for i, s in enumerate(sections):
            title = s.get("title") or ""
            body = s.get("body") or ""
            text = f"{title}\n{body}".strip()
            self._sections.append(
                _Section(
                    id=str(i),
                    article_id=s["article_id"],
                    article_title=s.get("article_title") or "",
                    title=title,
                    body=body,
                    text=text,
                )
            )
            self._corpus.append(text)
        if self._corpus:
            self._bm25 = BM25Okapi([_tokenize(t) for t in self._corpus])

    @property
    def size(self) -> int:
        return len(self._sections)

    def search(self, query: str, n: int = 5) -> list[dict]:
        """Вернуть топ-n секций: id, article_id, title, body, score."""
        if not self._bm25 or not self._sections:
            return []
        assert self._bm25 is not None
        tokens = _tokenize(query)
        if not tokens:
            return []
        scores = self._bm25.get_scores(tokens)
        # Буст заголовков: +0.5 к нормированному скору при совпадении слов запроса
        # в заголовке секции. Дешёвая эвристика «заголовок важнее тела».
        qwords = set(tokens)
        boosted = []
        for i, s in enumerate(self._sections):
            score = scores[i]
            title_tokens = set(_tokenize(s.title))
            if qwords & title_tokens:
                score += 0.5
            boosted.append((score, i))
        boosted.sort(key=lambda x: x[0], reverse=True)
        results = []
        for score, i in boosted[:n]:
            s = self._sections[i]
            results.append(
                {
                    "article_id": s.article_id,
                    "article_title": s.article_title,
                    "title": s.title,
                    "body": s.body,
                    "score": round(float(score), 4),
                }
            )
        return results
