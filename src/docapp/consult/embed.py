"""Клиент эмбеддингов RouterAI: батчевые запросы к POST {base_url}/embeddings.

API — OpenAI-совместимый: {"model": ..., "input": [...]} в ответ даёт
{"data": [{"index": 0, "embedding": [...]}, ...]}. Векторы всегда
возвращаются в порядке входных текстов (ответ сортируется по "index").
Сетевые сбои и статусы 429/500/502/503 повторяются до 3 попыток
с нарастающей паузой (1, 2, 4 с).
"""

from __future__ import annotations

import time

import httpx

from docapp.consult.config import ConsultConfig

#: Статусы, при которых запрос повторяется (429 — лимит, 5xx — сбой сервера).
_RETRYABLE_STATUSES = {429, 500, 502, 503}
#: Паузы между попытками (сек).
_RETRY_BACKOFF = (1, 2, 4)
#: Максимальное число попыток на один батч.
_MAX_ATTEMPTS = 3
#: Таймаут HTTP-запроса (сек).
_TIMEOUT = 60.0


class EmbeddingClient:
    """Клиент эмбеддингов RouterAI.

    Батчит тексты по batch_size, сортирует ответ по "index" и повторяет
    запрос при 429/500/502/503. transport — точка инъекции для тестов
    (httpx.MockTransport); при None используется обычный сетевой транспорт.
    """

    def __init__(
        self,
        config: ConsultConfig,
        batch_size: int = 100,
        transport: httpx.Transport | None = None,
    ) -> None:
        self._config = config
        self.batch_size = batch_size
        self._dim: int | None = None
        self._client = httpx.Client(
            base_url=config.base_url,
            headers={"Authorization": f"Bearer {config.api_key}"},
            timeout=_TIMEOUT,
            transport=transport,
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Эмбеддинги текстов батчами по batch_size, в порядке входных."""
        model = self._config.embed_model
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start : start + self.batch_size]
            vectors.extend(self._embed_batch(model, batch))
        return vectors

    def embed_query(self, text: str) -> list[float]:
        """Эмбеддинг одного запроса."""
        return self.embed([text])[0]

    def embed_dim(self) -> int:
        """Размерность эмбеддингов (зондирующий запрос, результат кешируется)."""
        if self._dim is None:
            self._dim = len(self.embed_query(""))
        return self._dim

    def _embed_batch(self, model: str, batch: list[str]) -> list[list[float]]:
        """Один запрос за батч; при 429/5xx или сетевом сбое — до 3 попыток."""
        payload = {"model": model, "input": batch}
        last_error: Exception | None = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                resp = self._client.post("/embeddings", json=payload)
            except httpx.HTTPError as exc:
                last_error = exc
            else:
                if resp.status_code == 200:
                    return self._parse_embeddings(resp)
                last_error = RuntimeError(
                    f"RouterAI /embeddings ответил {resp.status_code}: {resp.text[:200]!r}"
                )
                if resp.status_code not in _RETRYABLE_STATUSES:
                    break
            if attempt < _MAX_ATTEMPTS:
                time.sleep(_RETRY_BACKOFF[attempt - 1])
        assert last_error is not None
        raise RuntimeError(
            f"Не удалось получить эмбеддинги от RouterAI после "
            f"{_MAX_ATTEMPTS} попыток: {last_error}"
        ) from last_error

    @staticmethod
    def _parse_embeddings(resp: httpx.Response) -> list[list[float]]:
        """Достать векторы из ответа, отсортировав по "index", если он есть."""
        body = resp.json()
        items = body.get("data")
        if not isinstance(items, list):
            raise RuntimeError("Некорректный ответ RouterAI /embeddings: нет поля data")
        if items and "index" in items[0]:
            items = sorted(items, key=lambda item: item.get("index", 0))
        return [item["embedding"] for item in items]
