"""Тесты сборки индекса консультанта: build_index и CLI, без сети.

EmbeddingClient подменяется фейком (monkeypatch.setattr(build, ...)),
который возвращает детерминированные векторы — реальные запросы к
RouterAI не выполняются.
"""

import shutil

import pytest

from docapp.consult import build
from docapp.consult.build import build_index
from docapp.consult.config import ConsultConfig
from docapp.consult.store import SqliteConsultStore


class FakeEmbed:
    """Фейк EmbeddingClient: детерминированные векторы, без сети."""

    def __init__(self, config, batch_size: int = 100, transport=None) -> None:
        self._config = config

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Вектор i-го текста — [i+1, i+1, i+1, i+1], однозначно по позиции."""
        return [[float(i + 1)] * 4 for i in range(len(texts))]


def make_config(tmp_path) -> ConsultConfig:
    """ConsultConfig с index_dir в tmp_path, чтобы не трогать data/ проекта."""
    return ConsultConfig(
        api_key="key",
        base_url="https://routerai.ru/api/v1",
        embed_model="text-embedding-3-small",
        index_dir=tmp_path / "index",
    )


def copy_docs(tmp_path, *paths) -> None:
    """Скопировать файлы-фикстуры в tmp_path/docs (фикстуры session-scope)."""
    docs = tmp_path / "docs"
    docs.mkdir()
    for p in paths:
        shutil.copy(p, docs / p.name)


def db_path_of(tmp_path):
    return tmp_path / "index" / "consult.db"


@pytest.fixture(autouse=True)
def fake_embed(monkeypatch):
    """Подменить EmbeddingClient фейком во всех тестах модуля."""
    monkeypatch.setattr(build, "EmbeddingClient", FakeEmbed)


def test_build_index_creates_db(tmp_path, sample_docx, sample_pdf):
    """Сборка из .docx и .pdf создаёт consult.db с фрагментами и 2 документами."""
    copy_docs(tmp_path, sample_docx, sample_pdf)
    n = build_index(tmp_path / "docs", make_config(tmp_path))
    assert n > 0
    assert db_path_of(tmp_path).exists()
    with SqliteConsultStore(db_path_of(tmp_path)) as store:
        assert store.count_chunks() == n
        assert len(store.list_documents()) == 2


def test_build_index_doc_number_extracted(tmp_path, sample_docx):
    """Из prikaz_123.docx извлекается номер приказа «123»."""
    copy_docs(tmp_path, sample_docx)
    build_index(tmp_path / "docs", make_config(tmp_path))
    with SqliteConsultStore(db_path_of(tmp_path)) as store:
        rows = store.list_documents()
        assert len(rows) == 1
        assert rows[0]["filename"] == "prikaz_123.docx"
        assert rows[0]["doc_number"] == "123"


def test_build_index_replaces_old_db(tmp_path, sample_docx):
    """Повторная сборка перезаписывает БД: число фрагментов не удваивается."""
    copy_docs(tmp_path, sample_docx)
    config = make_config(tmp_path)
    build_index(tmp_path / "docs", config)
    with SqliteConsultStore(db_path_of(tmp_path)) as store:
        first = store.count_chunks()
    assert first > 0

    build_index(tmp_path / "docs", config)
    with SqliteConsultStore(db_path_of(tmp_path)) as store:
        assert store.count_chunks() == first
        assert len(store.list_documents()) == 1


def test_cli_missing_arg(tmp_path, monkeypatch, capsys):
    """main() без аргументов печатает usage и возвращает 1."""
    from docapp.consult.__main__ import main

    assert main([]) == 1
    out = capsys.readouterr()
    assert "Использование" in out.out + out.err


def test_cli_build_success(tmp_path, sample_docx, monkeypatch, capsys):
    """main([путь]) собирает индекс и печатает итог, код возврата 0."""
    from docapp.consult.__main__ import main

    copy_docs(tmp_path, sample_docx)
    monkeypatch.setenv("CONSULT_INDEX_DIR", str(tmp_path / "index"))
    assert main([str(tmp_path / "docs")]) == 0
    out = capsys.readouterr().out
    assert "Индексировано" in out
    assert "1 документов" in out
    assert str(tmp_path / "index" / "consult.db") in out


def test_build_index_ignores_unsupported(tmp_path, sample_docx):
    """Файлы не .docx/.pdf (например, .txt) в индекс не попадают."""
    copy_docs(tmp_path, sample_docx)
    (tmp_path / "docs" / "заметки.txt").write_text("черновик, не приказ", encoding="utf-8")
    n = build_index(tmp_path / "docs", make_config(tmp_path))
    assert n > 0
    with SqliteConsultStore(db_path_of(tmp_path)) as store:
        assert len(store.list_documents()) == 1
