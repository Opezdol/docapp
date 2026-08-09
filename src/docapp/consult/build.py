"""Сборка индекса консультанта: парсинг, нарезка, эмбеддинги, запись в SQLite.

Выполняется на рабочей станции (с доступом к RouterAI): читает .docx/.pdf
из папки, получает эмбеддинги и пишет новую БД data/consult/consult.db.
Сборка всегда полная — старый файл БД удаляется перед записью (ТЗ, задача T5).
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

import numpy as np

from docapp.consult.chunking import chunk_blocks, doc_number_of, doc_title_of
from docapp.consult.config import ConsultConfig
from docapp.consult.embed import EmbeddingClient
from docapp.consult.parsing import parse_docx, parse_pdf
from docapp.consult.store import SqliteConsultStore

#: Расширения документов, которые попадают в индекс.
_SUPPORTED_EXTENSIONS = {".docx", ".pdf"}


def _parse_file(path: Path):
    """Разобрать документ по расширению: .docx -> parse_docx, .pdf -> parse_pdf."""
    if path.suffix.lower() == ".docx":
        return parse_docx(path)
    return parse_pdf(path)


def build_index(
    docs_dir: str | Path,
    config: ConsultConfig,
    db_path: str | Path | None = None,
) -> int:
    """Полностью пересобрать индекс консультанта из документов в docs_dir.

    Читает все .docx/.pdf рекурсивно (в отсортированном порядке), парсит
    в блоки, нарезает на фрагменты, получает эмбеддинги через
    EmbeddingClient (клиент сам бьёт тексты на батчи) и пишет документы
    с фрагментами в новую БД. Существующий файл БД удаляется — пересборка
    всегда полная, как в ТЗ. Возвращает общее число фрагментов.

    db_path по умолчанию — config.index_dir / "consult.db".
    """
    docs_dir = Path(docs_dir)
    target = Path(db_path) if db_path is not None else config.index_dir / "consult.db"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)

    files = sorted(
        p for p in docs_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in _SUPPORTED_EXTENSIONS
    )

    client = EmbeddingClient(config)
    total_chunks = 0
    with SqliteConsultStore(target) as store:
        for path in files:
            blocks = _parse_file(path)
            if not blocks:
                continue
            doc_id = hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:12]
            head = "\n".join(b.text for b in blocks[:20])[:400]
            doc_number = doc_number_of(head)
            doc_title = doc_title_of(blocks)
            chunks = chunk_blocks(
                blocks, doc_id=doc_id, doc_number=doc_number, doc_title=doc_title
            )
            if not chunks:
                continue
            vectors = client.embed([c.text for c in chunks])
            document_id = store.add_document(
                filename=path.name,
                doc_number=doc_number,
                title=doc_title,
                added_at=datetime.now().isoformat(timespec="seconds"),
            )
            store.add_chunks(
                document_id,
                [
                    (
                        chunk.chunk_index,
                        chunk.section,
                        chunk.text,
                        np.asarray(vector, dtype=np.float32).tobytes(),
                    )
                    for chunk, vector in zip(chunks, vectors)
                ],
            )
            total_chunks += len(chunks)
    return total_chunks
