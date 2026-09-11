"""Тесты сервиса «Компендиума»: QA, статьи, версии, публикация, права. Без сети — фейки."""

import json

import pytest

from docapp.domain.employee import DOCTOR, EDITOR, HEAD, NURSE
from docapp.wiki.service import DEFAULT_SYSTEM_PROMPT, WikiForbidden, WikiService
from docapp.wiki.store import SqliteWikiStore
from factories import test_db


class FakeLLM:
    def __init__(self):
        self.last_usage = None
        self.messages = None

    async def stream_chat(self, messages, temperature=0.1):
        self.messages = messages
        yield "От"
        yield "вет"
        self.last_usage = {"prompt_tokens": 10, "completion_tokens": 4}


@pytest.fixture
def store(tmp_path):
    s = SqliteWikiStore(test_db(tmp_path))
    yield s
    s.close()


def make_service(store, sources_dir=None):
    """Сервис с папкой источников в tmp: PDF кладутся туда (ADR-0020)."""
    return WikiService(store, FakeLLM(), sources_dir=sources_dir or store.db_dir / "sources")


def add_published_article(service, title="Атропин", body=None):
    body = body or f"# {title}\n\nАтропин вводят внутривенно по 0.5 мг."
    service.save_article(1, HEAD, None, body)
    aid = service.articles()[0]["id"]
    service.publish(1, HEAD, aid)
    return aid


# ── QA ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ask_streams_and_saves(store):
    service = make_service(store)
    add_published_article(service)

    events = [e async for e in service.ask(1, "c1", "какая доза атропина?", role=DOCTOR)]
    deltas = "".join(e["text"] for e in events if e["type"] == "delta")
    assert deltas == "Ответ"
    done = [e for e in events if e["type"] == "done"][0]
    assert done["citations"][0]["article_title"] == "Атропин"
    assert done["prompt_tokens"] == 10

    msgs = store.list_messages("c1")
    assert [m["role"] for m in msgs] == ["user", "assistant"]


@pytest.mark.asyncio
async def test_ask_empty_index(store):
    service = make_service(store)
    events = [e async for e in service.ask(1, "c1", "вопрос", role=DOCTOR)]
    deltas = [e["text"] for e in events if e["type"] == "delta"]
    assert any("нет опубликованных статей" in d for d in deltas)
    assert store.list_messages("c1") == []


@pytest.mark.asyncio
async def test_ask_nurse_forbidden(store):
    service = make_service(store)
    with pytest.raises(WikiForbidden):
        async for _ in service.ask(1, "c1", "вопрос", role=NURSE):
            pass


# ── статьи и публикация ───────────────────────────────────────────────

def test_save_article_creates_draft(store):
    service = make_service(store)
    article = service.save_article(1, HEAD, None, "# Дежурство\nГрафик утверждается ежемесячно.")
    assert article["status"] == "draft"
    assert article["version"] == 1
    assert article["title"] == "Дежурство"


def test_save_article_new_revision(store):
    service = make_service(store)
    service.save_article(1, HEAD, None, "# A\nv1")
    aid = service.articles()[0]["id"]
    article = service.save_article(1, HEAD, aid, "# A\nv2")
    assert article["version"] == 2
    assert service.revisions(aid)[0]["version"] == 2


def test_publish_and_unpublish(store):
    service = make_service(store)
    service.save_article(1, HEAD, None, "# A\nТекст")
    aid = service.articles()[0]["id"]
    service.publish(1, HEAD, aid)
    assert service.article(aid)["status"] == "published"
    assert service.stats()["published"] == 1

    service.unpublish(1, HEAD, aid)
    assert service.article(aid)["status"] == "draft"
    assert service.stats()["published"] == 0


def test_only_published_in_index(store):
    service = make_service(store)
    service.save_article(1, HEAD, None, "# Черновик\nСкрытая информация.")
    assert service.index.size == 0  # черновик не попадает в индекс
    add_published_article(service, "Публикация", "# Публикация\nВидимая информация.")
    assert service.index.size >= 1


def test_editor_can_curate_doctor_cannot(store):
    service = make_service(store)
    service.save_article(1, EDITOR, None, "# A\nТекст")  # редактор может
    with pytest.raises(WikiForbidden):
        service.save_article(1, DOCTOR, None, "# B\nТекст")
    with pytest.raises(WikiForbidden):
        service.save_article(1, NURSE, None, "# C\nТекст")


def test_source_flow_writes_file_not_blob(store, monkeypatch):
    """PDF уходит файлом в хранилище, в БД — только имя файла (ADR-0020)."""
    # подменяем page_count, чтобы не создавать реальный PDF
    monkeypatch.setattr("docapp.wiki.parser.page_count", lambda path: 2)
    service = make_service(store)
    source = service.add_source(1, HEAD, "prikaz.pdf", b"%PDF-1.4")

    assert source["page_count"] == 2
    assert source["ocr_status"] == "pending"

    row = store.get_source(source["id"])
    assert "source" not in row.keys()          # BLOB в БД больше нет
    assert row["page_count"] == 2              # число страниц видно сразу, до OCR
    stored = service.sources_dir / row["stored_name"]
    assert stored.is_file()
    assert stored.read_bytes() == b"%PDF-1.4"

    downloaded = service.source_file(source["id"])
    assert downloaded["data"] == b"%PDF-1.4"
    assert downloaded["name"] == "prikaz.pdf"  # человеку — имя, как загрузили

    service.delete_source(source["id"], HEAD)
    assert not stored.exists()                 # удаление уносит и файл
    assert service.source_file(source["id"]) is None


def test_source_without_file_is_reported(store):
    """Запись есть, файла нет (старая база) — «нет файла», а не падение."""
    service = make_service(store)
    sid = store.add_source("старый.pdf", "", "Старый", "2026-03-01T09:00:00", 1)

    assert service.source_file(sid) is None
    service.run_ocr(sid)
    assert store.get_source(sid)["ocr_status"] == "error"
    assert "не сохранён" in store.get_source(sid)["ocr_error"]


def test_shared_file_survives_until_last_record(store, monkeypatch):
    """Одинаковые PDF делят файл: он уходит только с последней записью."""
    monkeypatch.setattr("docapp.wiki.parser.page_count", lambda path: 1)
    service = make_service(store)

    first = service.add_source(1, HEAD, "приказ.pdf", "%PDF-1.4 один".encode("utf-8"))
    second = service.add_source(1, HEAD, "копия приказа.pdf", "%PDF-1.4 один".encode("utf-8"))
    stored = service.sources_dir / store.get_source(first["id"])["stored_name"]
    assert stored.is_file()
    assert store.get_source(second["id"])["stored_name"] == store.get_source(first["id"])["stored_name"]

    service.delete_source(first["id"], HEAD)
    assert stored.is_file()                    # на файл ещё ссылается вторая запись

    service.delete_source(second["id"], HEAD)
    assert not stored.exists()


def test_broken_pdf_leaves_no_file(store, monkeypatch):
    """Битый PDF не оставляет мусор в хранилище."""
    def boom(path):
        raise ValueError("не PDF")

    monkeypatch.setattr("docapp.wiki.parser.page_count", boom)
    service = make_service(store)
    with pytest.raises(ValueError):
        service.add_source(1, HEAD, "битый.pdf", "не pdf".encode("utf-8"))

    assert not list(service.sources_dir.glob("*.pdf"))


def test_settings_default_and_update(store):
    service = make_service(store)
    assert service.system_prompt() == DEFAULT_SYSTEM_PROMPT
    out = service.update_settings({"top_k": 8, "temperature": "0.5"})
    assert out["top_k"] == 8
    assert out["temperature"] == 0.5
    assert store.get_setting("top_k") == "8"
