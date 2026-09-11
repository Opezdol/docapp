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


def make_service(store):
    return WikiService(store, FakeLLM())


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


def test_source_flow(store, monkeypatch):
    service = make_service(store)
    # подменяем page_count, чтобы не создавать реальный PDF
    monkeypatch.setattr("docapp.wiki.parser.page_count", lambda path: 2)
    source = service.add_source(1, HEAD, "prikaz.pdf", b"%PDF-1.4")
    assert source["page_count"] == 2
    assert source["ocr_status"] == "pending"
    assert service.source_file(source["id"])["name"] == "prikaz.pdf"


def test_settings_default_and_update(store):
    service = make_service(store)
    assert service.system_prompt() == DEFAULT_SYSTEM_PROMPT
    out = service.update_settings({"top_k": 8, "temperature": "0.5"})
    assert out["top_k"] == 8
    assert out["temperature"] == 0.5
    assert store.get_setting("top_k") == "8"
