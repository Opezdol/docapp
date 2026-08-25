"""Структурная нарезка блоков документа на фрагменты (чанки) с метаданными."""

import re
from dataclasses import dataclass


@dataclass
class Chunk:
    text: str
    doc_number: str
    doc_title: str
    section: str
    chunk_index: int


PRIKAZ_RE = re.compile(r"приказ\s*[№#]?\s*([\d\-/]+)", re.IGNORECASE)


def doc_number_of(text: str) -> str:
    """Номер приказа из первых строк документа («приказ № 123» -> '123')."""
    m = PRIKAZ_RE.search(text[:400])
    return m.group(1) if m else ""


def doc_title_of(blocks) -> str:
    """Название документа: первый заголовок или первая строка."""
    for b in blocks[:15]:
        if b.kind == "heading":
            return b.text[:120]
    return blocks[0].text[:120] if blocks else ""


def chunk_blocks(blocks, doc_number: str, doc_title: str,
                 chunk_size: int = 600) -> list[Chunk]:
    """Нарезать блоки на фрагменты: заголовок начинает новый фрагмент и
    становится его разделом (section); при превышении chunk_size фрагмент
    закрывается на границе абзаца."""
    chunks: list[Chunk] = []
    buf, section, idx = "", "", 0

    def flush() -> None:
        nonlocal buf, idx
        if buf.strip():
            chunks.append(Chunk(buf.strip(), doc_number, doc_title, section, idx))
            idx += 1
            buf = ""

    for b in blocks:
        if b.kind == "heading":
            flush()
            section = b.text
            buf = b.text + "\n"
            continue
        if buf.strip() and len(buf) + len(b.text) + 1 > chunk_size:
            flush()
        if len(b.text) > chunk_size:
            flush()
            for i in range(0, len(b.text), chunk_size):
                part = b.text[i:i + chunk_size].strip()
                if part:
                    chunks.append(Chunk(part, doc_number, doc_title, section, idx))
                    idx += 1
            continue
        buf += b.text + "\n"
    flush()
    return chunks
