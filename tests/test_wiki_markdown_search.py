"""Тесты разбора Markdown и поиска по секциям «Чата»."""

from docapp.wiki.markdown import render_html, render_plain, split_sections, title_of
from docapp.wiki.search import WikiSearch


def test_split_sections_by_headings():
    md = "# Введение\nОбщие слова.\n\n## Дозировка\nАтропин 0.5 мг.\n\n# Конец\nЗаключение."
    sections = split_sections(md)
    assert [s.title for s in sections] == ["Введение", "Дозировка", "Конец"]
    assert "Атропин 0.5 мг" in sections[1].body
    assert sections[1].level == 2


def test_split_sections_leading_text():
    md = "Вводный абзац без заголовка.\n\n# Раздел\nТекст."
    sections = split_sections(md)
    assert sections[0].title == ""
    assert sections[0].level == 0
    assert "Вводный абзац" in sections[0].body
    assert sections[1].title == "Раздел"


def test_render_plain_strips_markdown():
    md = "# Заголовок\n\n**Жирный** текст с [ссылкой](http://x) и `код`.\n- пункт один\n1. пункт два"
    text = render_plain(md)
    assert "Заголовок" in text
    assert "#" not in text.split("Заголовок")[0]
    assert "**" not in text
    assert "[ссылкой]" not in text
    assert "код" in text


def test_title_of_first_heading():
    assert title_of("# Приказ № 5\nТекст") == "Приказ № 5"


def test_title_of_first_line_without_heading():
    assert title_of("Первая строка.\nВторая.") == "Первая строка."


def test_search_returns_matching_sections():
    sections = [
        {"article_id": 1, "article_title": "Атропин", "title": "Дозировка", "body": "Атропин 0.5 мг внутривенно."},
        {"article_id": 2, "article_title": "Дежурства", "title": "График", "body": "График утверждается ежемесячно."},
        {"article_id": 3, "article_title": "Осмотр", "title": "До операции", "body": "Осмотреть пациента."},
    ]
    index = WikiSearch(sections)
    hits = index.search("дозировка атропина", n=2)
    assert hits[0]["article_id"] == 1
    assert hits[0]["title"] == "Дозировка"


def test_search_title_boost():
    sections = [
        {"article_id": 1, "article_title": "A", "title": "Атропин", "body": "обычный текст"},
        {"article_id": 2, "article_title": "B", "title": "Другое", "body": "упоминание атропина в теле"},
    ]
    index = WikiSearch(sections)
    hits = index.search("атропин", n=1)
    # Совпадение в заголовке должно выиграть у совпадения в теле.
    assert hits[0]["article_id"] == 1


def test_search_empty_corpus():
    index = WikiSearch([])
    assert index.search("что угодно") == []
    assert index.size == 0


def test_render_html_headings_lists_bold():
    md = "# Заголовок\n\n**Жирный** и *курсив* и `код`.\n\n- пункт 1\n- пункт 2"
    html_out = render_html(md)
    assert "<h1>Заголовок</h1>" in html_out
    assert "<b>Жирный</b>" in html_out
    assert "<i>курсив</i>" in html_out
    assert "<code>код</code>" in html_out
    assert "<ul>" in html_out
    assert "<li>пункт 1</li>" in html_out


def test_render_html_escapes_raw_html():
    md = "<script>alert(1)</script>\n\nОбычный текст"
    html_out = render_html(md)
    assert "<script>" not in html_out
    assert "&lt;script&gt;" in html_out


def test_render_html_ordered_list_and_link():
    md = "1. Первый\n2. Второй\n\n[Ссылка](http://example.com)"
    html_out = render_html(md)
    assert "<ol>" in html_out
    assert "<li>Первый</li>" in html_out
    assert 'href="http://example.com"' in html_out
