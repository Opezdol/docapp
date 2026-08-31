"""Тесты парсера PDF и OCR-конвейера «Компендиума». Без сети — фейк vision."""

import pytest

from docapp.wiki import ocr, parser


def make_text_pdf(path):
    import pymupdf

    font = "/System/Library/Fonts/Supplemental/Arial Unicode.ttf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "ПРИКАЗ № 1", fontsize=14, fontname="arial-uni", fontfile=font)
    page.insert_text(
        (72, 100),
        "Текст приказа об утверждении порядка работы отделения анестезиологии.",
        fontsize=11, fontname="arial-uni", fontfile=font,
    )
    doc.save(path)
    doc.close()
    return path


def test_has_text_layer_true(tmp_path):
    p = make_text_pdf(tmp_path / "a.pdf")
    assert parser.has_text_layer(p)


def test_parse_pdf_text(tmp_path):
    p = make_text_pdf(tmp_path / "a.pdf")
    pages = parser.parse_pdf_text(p)
    assert "ПРИКАЗ № 1" in pages[0]


def test_page_count(tmp_path):
    p = make_text_pdf(tmp_path / "a.pdf")
    assert parser.page_count(p) == 1


def test_render_page_png(tmp_path):
    p = make_text_pdf(tmp_path / "a.pdf")
    png = parser.render_page_png(p, 0)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_process_pdf_text_layer(tmp_path):
    p = make_text_pdf(tmp_path / "a.pdf")
    text, tables, pages = ocr.process_pdf(p)  # vision не нужен
    assert "ПРИКАЗ № 1" in text
    assert pages == 1


class FakeVision:
    def __init__(self):
        self.calls = 0

    async def ocr_page(self, image_bytes, mime_type="image/png"):
        self.calls += 1
        return "Распознанная страница"


def test_process_pdf_scan_uses_vision(tmp_path, monkeypatch):
    import pymupdf

    # PDF без текстового слоя: пустая страница (имитация скана).
    p = tmp_path / "scan.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.save(p)
    doc.close()

    # Форсируем «скан»: monkeypatch has_text_layer -> False.
    monkeypatch.setattr(parser, "has_text_layer", lambda path: False)
    vision = FakeVision()
    text, tables, pages = ocr.process_pdf(p, vision)
    assert vision.calls == 1
    assert "Распознанная страница" in text
    assert pages == 1


def test_process_pdf_scan_without_vision_raises(tmp_path, monkeypatch):
    import pymupdf

    p = tmp_path / "scan.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.save(p)
    doc.close()

    monkeypatch.setattr(parser, "has_text_layer", lambda path: False)
    with pytest.raises(RuntimeError, match="нужен vision"):
        ocr.process_pdf(p, None)


def test_tables_json_roundtrip():
    tables = [[["a", "b"], ["1", "2"]]]
    raw = ocr.tables_to_json(tables)
    assert ocr.tables_from_json(raw) == tables
    assert ocr.tables_from_json("not json") == []
