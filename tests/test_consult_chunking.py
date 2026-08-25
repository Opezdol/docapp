from docapp.consult.chunking import chunk_blocks, doc_number_of, doc_title_of
from docapp.consult.parsing import Block

BLOCKS = [
    Block("heading", "ПРИКАЗ № 123 от 15.03.2024"),
    Block("para", "Об утверждении порядка работы отделения."),
    Block("heading", "1. Общие положения", 1),
    Block("para", "1.1. Настоящий приказ регулирует порядок оказания помощи."),
    Block("para", "1.2. Дежурный анестезиолог обязан осмотреть пациента до операции."),
]


def test_doc_number_extracted():
    assert doc_number_of("ПРИКАЗ № 123 от 15.03.2024") == "123"


def test_title_from_first_heading():
    assert doc_title_of(BLOCKS) == "ПРИКАЗ № 123 от 15.03.2024"


def test_chunks_carry_metadata():
    chunks = chunk_blocks(BLOCKS, "123", "ПРИКАЗ № 123 от 15.03.2024")
    assert len(chunks) == 2
    assert chunks[1].section == "1. Общие положения"
    assert chunks[1].doc_number == "123"
    assert chunks[1].chunk_index == 1
    assert "осмотреть пациента" in chunks[1].text


def test_chunk_size_overflow_splits():
    long_para = [Block("para", "слово " * 200)]
    chunks = chunk_blocks(long_para, "", "", chunk_size=100)
    assert len(chunks) >= 2
