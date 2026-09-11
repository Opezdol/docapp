import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

import os

import pytest


@pytest.fixture(autouse=True, scope="session")
def _isolated_module_dirs(tmp_path_factory):
    """Тесты не пишут в `data/` репозитория.

    При сборке приложения модуль «Компендиум» создаёт папку PDF-источников
    (ADR-0020) — по умолчанию это `data/wiki/sources` внутри проекта. В тестах
    уводим её во временную папку: репозиторий остаётся чистым.
    """
    base = tmp_path_factory.mktemp("module-dirs")
    previous = os.environ.get("WIKI_SOURCES_DIR")
    os.environ["WIKI_SOURCES_DIR"] = str(base / "wiki-sources")
    yield
    if previous is None:
        os.environ.pop("WIKI_SOURCES_DIR", None)
    else:
        os.environ["WIKI_SOURCES_DIR"] = previous


@pytest.fixture(scope="session")
def sample_docx(tmp_path_factory):
    from docx import Document
    p = tmp_path_factory.mktemp("docs") / "prikaz_123.docx"
    doc = Document()
    doc.add_heading("ПРИКАЗ № 123 от 15.03.2024", level=0)
    doc.add_paragraph("Об утверждении порядка работы отделения анестезиологии.")
    doc.add_heading("1. Общие положения", level=1)
    doc.add_paragraph("1.1. Настоящий приказ регулирует порядок оказания анестезиологической помощи.")
    doc.add_paragraph("1.2. Дежурный анестезиолог обязан осмотреть пациента до операции и зафиксировать данные в карте.")
    doc.save(p)
    return p


@pytest.fixture(scope="session")
def sample_pdf(tmp_path_factory):
    import pymupdf
    p = tmp_path_factory.mktemp("docs") / "prikaz_456.pdf"
    font = "/System/Library/Fonts/Supplemental/Arial Unicode.ttf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "ПРИКАЗ № 456 от 20.05.2024", fontsize=16, fontname="arial-uni", fontfile=font)
    page.insert_text((72, 100), "О графике дежурств отделения анестезиологии.", fontsize=11, fontname="arial-uni", fontfile=font)
    page.insert_text((72, 128), "1.1. График дежурств утверждается заведующим отделением ежемесячно.", fontsize=11, fontname="arial-uni", fontfile=font)
    doc.save(p)
    doc.close()
    return p
