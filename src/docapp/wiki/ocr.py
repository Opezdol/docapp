"""OCR-конвейер PDF-источника: текст + таблицы (для сканов — через vision).

Логика:
- если в PDF есть текстовый слой — текст берётся из pymupdf, таблицы — find_tables;
- если текстового слоя нет (скан) — каждая страница растеризуется в PNG и
  распознаётся мультимодальной моделью (VisionClient), возвращающей текст
  с таблицами в markdown.

VisionClient создаётся снаружи и передаётся в process_pdf (инверсия зависимости:
тесты подменяют его фейком без сети). Возвращает (ocr_text, tables, page_count),
где tables — list[list[list[str]]].
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from docapp.ai.vision import VisionClient
from docapp.wiki import parser


def tables_to_json(tables: list[list[list[str]]]) -> str:
    """Сериализовать таблицы в JSON-строку для хранения в БД."""
    return json.dumps(tables, ensure_ascii=False)


def tables_from_json(raw: str) -> list[list[list[str]]]:
    """Прочитать таблицы из JSON-строки (пусто/невалидно -> [])."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    return data if isinstance(data, list) else []


def process_pdf(path: str | Path, vision: VisionClient | None = None) -> tuple[str, list[list[list[str]]], int]:
    """OCR PDF-источника: вернуть (text, tables, page_count).

    Для текстовых PDF vision не нужен. Для сканов vision обязателен:
    страницы распознаются последовательно (простая и надёжная стратегия),
    таблицы при этом остаются в тексте markdown-нотацией.
    """
    path = Path(path)
    pages = parser.page_count(path)

    if parser.has_text_layer(path):
        text = "\n\n".join(parser.parse_pdf_text(path))
        return text, parser.extract_tables(path), pages

    if vision is None:
        raise RuntimeError(
            "PDF — скан без текстового слоя, нужен vision-клиент для OCR"
        )

    async def _run() -> list[str]:
        texts = []
        for i in range(pages):
            png = parser.render_page_png(path, i)
            texts.append(await vision.ocr_page(png))
        return texts

    page_texts = asyncio.run(_run())
    text = "\n\n".join(page_texts)
    return text, [], pages
