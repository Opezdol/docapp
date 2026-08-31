"""Разбор .md-статей на секции по заголовкам и рендер в обычный текст/HTML.

Курируемые статьи «Компендиума» — это Markdown. Для ответов с цитатами нужны
две вещи: (1) секции по заголовкам — единица поиска и цитирования; (2) plain-
text «рендер» для BM25 и для подачи в контекст LLM (без markdown-разметки).
Для просмотра статьи в браузере есть безопасный render_html (безопасен, т.к.
сначала HTML-экранируется, затем размечается).
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

#: ATX-заголовки: от «# Заголовок» до «###### Заголовок» (только в начале строки).
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


@dataclass(frozen=True)
class Section:
    """Одна секция статьи: заголовок (может быть пустым) и текст."""

    title: str
    body: str
    level: int = 0


def split_sections(md: str) -> list[Section]:
    """Разбить Markdown на секции по ATX-заголовкам.

    Текст до первого заголовка образует секцию с пустым заголовком (level=0).
    Каждая секция — заголовок + тело до следующего заголовка.
    """
    lines = md.splitlines()
    sections: list[Section] = []
    current_title = ""
    current_level = 0
    current_body: list[str] = []
    first_heading_seen = False

    def flush() -> None:
        nonlocal current_body
        body = "\n".join(current_body).strip()
        if body or current_title:
            sections.append(Section(current_title, body, current_level))
        current_body = []

    for line in lines:
        m = _HEADING_RE.match(line.strip())
        if m:
            flush()
            hashes, title = m.group(1), m.group(2).strip()
            current_title = title
            current_level = len(hashes)
            first_heading_seen = True
            continue
        current_body.append(line)

    # Хвост после последнего заголовка.
    flush()
    return sections


def _strip_inline(text: str) -> str:
    """Убрать базовую inline-разметку: код, ссылки, жирность, курсив."""
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)  # картинки
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)   # ссылки
    text = re.sub(r"(\*\*|__)(.*?)\1", r"\2", text)        # жирность
    text = re.sub(r"(\*|_)(.*?)\1", r"\2", text)           # курсив
    return text


def render_plain(md: str) -> str:
    """Рендер Markdown в обычный текст: без заголовков-решёток и inline-разметки.

    Таблицы (строки с |) остаются как есть — они читаемы в контексте LLM.
    """
    lines = []
    for line in md.splitlines():
        stripped = line.strip()
        m = _HEADING_RE.match(stripped)
        if m:
            lines.append(m.group(2).strip())
            continue
        if stripped.startswith(("-", "*", "+")) and not stripped.startswith(("---", "***")):
            # Маркированный список — оставляем как есть, убрав маркер.
            lines.append(_strip_inline(stripped[1:].strip()))
            continue
        if re.match(r"^\d+\.\s", stripped):
            lines.append(_strip_inline(re.sub(r"^\d+\.\s+", "", stripped)))
            continue
        lines.append(_strip_inline(line))
    return "\n".join(lines)


def title_of(md: str) -> str:
    """Название статьи: первый заголовок (H1/H2), иначе первая непустая строка."""
    for section in split_sections(md):
        if section.title:
            return section.title[:120]
    for line in md.splitlines():
        if line.strip():
            return line.strip()[:120]
    return ""


def render_html(md: str) -> str:
    """Безопасный рендер Markdown в HTML.

    Безопасность: сначала каждая строка HTML-экранируется, затем на уже
    экранированном тексте применяется ограниченная разметка (заголовки,
    списки, жирность/курсив, инлайн-код, ссылки с экранированными атрибутами).
    Сырой HTML из .md не проходит — он оказывается экранированным.
    """
    out: list[str] = []
    in_list = False
    list_type = ""

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            out.append(f"</{list_type}>")
            in_list = False

    for raw in md.splitlines():
        line = raw.rstrip()
        stripped = line.strip()

        # Заголовки ATX
        m = _HEADING_RE.match(stripped)
        if m:
            close_list()
            level = len(m.group(1))
            out.append(f"<h{level}>{_inline_html(m.group(2))}</h{level}>")
            continue

        # Горизонтальная линия
        if re.match(r"^(\s*[-*_])\s*\1\s*\1\s*$", stripped):
            close_list()
            out.append("<hr>")
            continue

        # Маркированный список: маркер + пробел (- , * , + )
        bullet = re.match(r"^([-+*])\s+(.*)$", stripped)
        if bullet:
            if not in_list or list_type != "ul":
                close_list()
                out.append("<ul>")
                in_list, list_type = True, "ul"
            out.append(f"<li>{_inline_html(bullet.group(2))}</li>")
            continue

        # Нумерованный список
        num = re.match(r"^\d+\.\s+(.*)$", stripped)
        if num:
            if not in_list or list_type != "ol":
                close_list()
                out.append("<ol>")
                in_list, list_type = True, "ol"
            out.append(f"<li>{_inline_html(num.group(1))}</li>")
            continue

        # Таблица (строки с |) — упрощённо: экранируем и сохраняем разрывы
        if "|" in line:
            close_list()
            cells = line.split("|")
            cells = [c.strip() for c in cells]
            cells = [c for c in cells if c != ""] or cells
            if all(re.match(r"^:?-{2,}:?$", c) for c in cells):
                continue  # строка-разделитель таблицы — пропускаем
            out.append(
                "<p class='md-table'>" + " | ".join(html.escape(c) for c in cells) + "</p>"
            )
            continue

        # Пустая строка
        if not stripped:
            close_list()
            continue

        # Обычный абзац
        close_list()
        out.append(f"<p>{_inline_html(line.strip())}</p>")

    close_list()
    return "\n".join(out)


def _inline_html(text: str) -> str:
    """Inline-разметка поверх ЭКРАНИРОВАННОГО текста (безопасно)."""
    text = html.escape(text)
    # инлайн-код `x`
    text = re.sub(r"`([^`]*)`", r"<code>\1</code>", text)
    # жирность **x** / __x__
    text = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"__([^_]+)__", r"<b>\1</b>", text)
    # курсив *x* / _x_
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<i>\1</i>", text)
    # ссылки [текст](url) — url экранируется в кавычках
    text = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        lambda m: f'<a href="{html.escape(m.group(2), quote=True)}">{m.group(1)}</a>',
        text,
    )
    return text
