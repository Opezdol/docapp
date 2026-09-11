"""Парсинг PDF-источника в текст и таблицы.

«Чат» работает только с PDF (сканы приказов/инструкций). Здесь:
- has_text_layer — есть ли текстовый слой в PDF;
- parse_pdf_text — текст по страницам (для PDF с текстовым слоем);
- extract_tables — таблицы средствами pymupdf (для текстовых PDF);
- render_page_png — растеризация страницы в PNG для vision-OCR (сканы).
"""

from __future__ import annotations

from pathlib import Path


def has_text_layer(path: str | Path) -> bool:
    """Есть ли извлекаемый текстовый слой хотя бы в части страниц PDF."""
    import pymupdf

    doc = pymupdf.open(str(path))
    try:
        total_chars = 0
        for page in doc:
            total_chars += len(page.get_text().strip())
            if total_chars > 40:
                return True
        return False
    finally:
        doc.close()


def parse_pdf_text(path: str | Path) -> list[str]:
    """Текст каждой страницы PDF (list[page_text]), пропуская пустые."""
    import pymupdf

    doc = pymupdf.open(str(path))
    try:
        return [page.get_text() for page in doc]
    finally:
        doc.close()


def extract_tables(path: str | Path) -> list[list[list[str]]]:
    """Все таблицы PDF как list[table], table = list[row], row = list[cell]."""
    import pymupdf

    doc = pymupdf.open(str(path))
    try:
        tables: list[list[list[str]]] = []
        for page in doc:
            for table in page.find_tables().tables:
                rows: list[list[str]] = []
                for row in table.extract():
                    rows.append([(cell or "").strip() if cell is not None else "" for cell in row])
                if rows:
                    tables.append(rows)
        return tables
    finally:
        doc.close()


def page_count(path: str | Path) -> int:
    import pymupdf

    doc = pymupdf.open(str(path))
    try:
        return doc.page_count
    finally:
        doc.close()


def render_page_png(path: str | Path, page_index: int, zoom: float = 2.0) -> bytes:
    """Растеризовать страницу PDF в PNG-байты (для vision-OCR)."""
    import pymupdf

    doc = pymupdf.open(str(path))
    try:
        page = doc[page_index]
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
        return pix.tobytes("png")
    finally:
        doc.close()
