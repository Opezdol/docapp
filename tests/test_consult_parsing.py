from docapp.consult.parsing import parse_docx, parse_pdf


def test_docx_heading_and_paragraph(sample_docx):
    blocks = parse_docx(str(sample_docx))
    assert any(b.kind == "heading" and "ПРИКАЗ" in b.text for b in blocks)
    assert any(b.kind == "para" and "осмотреть пациента" in b.text for b in blocks)


def test_pdf_heading_by_font_size(sample_pdf):
    blocks = parse_pdf(str(sample_pdf))
    assert any(b.kind == "heading" and "ПРИКАЗ № 456" in b.text for b in blocks)
    assert any(b.kind == "para" and "График дежурств" in b.text for b in blocks)
