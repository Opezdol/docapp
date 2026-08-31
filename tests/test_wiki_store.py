"""Тесты хранилища «Компендиума»: источники, статьи, ревизии, публикация, связи."""

import pytest

from docapp.wiki.store import SqliteWikiStore


@pytest.fixture
def store_factory(tmp_path):
    stores = []

    def factory():
        s = SqliteWikiStore(tmp_path / "wiki.db")
        stores.append(s)
        return s

    yield factory
    for s in stores:
        s.close()


def test_add_and_get_source(store_factory):
    store = store_factory()
    sid = store.add_source(
        "prikaz.pdf", "123", "Приказ 123", "2026-08-01T10:00:00", 1, b"%PDF", "prikaz.pdf"
    )
    row = store.get_source(sid)
    assert row["doc_number"] == "123"
    assert row["ocr_status"] == "pending"
    assert row["source"] == b"%PDF"
    assert store.list_sources()[0]["id"] == sid


def test_ocr_result_updates_source(store_factory):
    store = store_factory()
    sid = store.add_source("s.pdf", "", "", "2026-08-01T10:00:00", 1)
    store.set_ocr_result(sid, "текст", '[[["a","b"]]]', 3)
    row = store.get_source(sid)
    assert row["ocr_status"] == "done"
    assert row["ocr_text"] == "текст"
    assert row["page_count"] == 3


def test_article_revision_and_publish_flow(store_factory):
    store = store_factory()
    aid = store.add_article("Дежурство", 1, "2026-08-01T10:00:00")
    assert store.get_article(aid)["status"] == "draft"

    v = store.next_version(aid)
    rid = store.add_revision(aid, v, "# Введение\nТекст", "Введение\nТекст", 1, "2026-08-01T11:00:00")
    assert store.current_revision(aid)["id"] == rid
    assert store.published_revision(aid) is None  # ещё не опубликовано

    store.set_published_revision(aid, rid, "2026-08-01T12:00:00")
    assert store.get_article(aid)["status"] == "published"
    assert store.published_revision(aid)["id"] == rid
    assert store.list_published_articles()[0]["id"] == aid


def test_revision_versions_increment(store_factory):
    store = store_factory()
    aid = store.add_article("A", 1, "2026-08-01T10:00:00")
    r1 = store.add_revision(aid, 1, "v1", "v1", 1, "2026-08-01T10:00:00")
    r2 = store.add_revision(aid, 2, "v2", "v2", 1, "2026-08-01T11:00:00")
    assert store.current_revision(aid)["id"] == r2
    revs = store.list_revisions(aid)
    assert [r["version"] for r in revs] == [2, 1]


def test_links_article_source(store_factory):
    store = store_factory()
    sid = store.add_source("s.pdf", "123", "S", "2026-08-01T10:00:00", 1)
    aid = store.add_article("A", 1, "2026-08-01T10:00:00")
    store.add_link(aid, sid, "стр. 3")
    links = store.list_links(aid)
    assert links[0]["source_id"] == sid
    assert links[0]["anchor"] == "стр. 3"
    assert store.articles_for_source(sid)[0]["id"] == aid

    store.clear_links(aid)
    assert store.list_links(aid) == []


def test_settings_and_messages(store_factory):
    store = store_factory()
    assert store.get_setting("k") is None
    store.set_setting("k", "v")
    assert store.get_setting("k") == "v"

    store.add_message(1, "c1", "user", "вопрос", "[]", 0, 0, "2026-08-01T10:00:00")
    store.add_message(1, "c1", "assistant", "ответ", "[]", 10, 4, "2026-08-01T10:00:01")
    msgs = store.list_messages("c1")
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    totals = store.token_totals()
    assert totals[0]["total_prompt"] == 10
    assert totals[0]["total_completion"] == 4


def test_cascade_delete_article(store_factory):
    store = store_factory()
    aid = store.add_article("A", 1, "2026-08-01T10:00:00")
    store.add_revision(aid, 1, "v1", "v1", 1, "2026-08-01T10:00:00")
    store.delete_article(aid)
    assert store.get_article(aid) is None
    assert store.list_revisions(aid) == []
