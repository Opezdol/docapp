"""Сервис модуля «Компендиум»: ответы с цитатами, статьи, версии, публикация.

Связывает хранилище (SqliteWikiStore), поиск по секциям (WikiSearch),
LLM-клиент и vision-клиент (OCR) в один сервис. В ответах участвуют ТОЛЬКО
опубликованные статьи (published_revision). Черновик редактируется свободно,
но не влияет на консультанта до публикации.

Роли: head и editor — курирование (источники, статьи, публикация); head —
настройки. Врачи, старшая сестра и заведующий задают вопросы; медсёстры —
доступа нет (как было в «Приказах»).
"""

from __future__ import annotations

import json
import threading
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path

from docapp.ai.llm import LLMClient
from docapp.ai.vision import VisionClient
from docapp.domain.employee import EDITOR, HEAD, NURSE
from docapp.wiki import ocr
from docapp.wiki.markdown import render_plain, split_sections, title_of
from docapp.wiki.search import WikiSearch
from docapp.wiki.store import SqliteWikiStore

#: Системный промпт по умолчанию: ответы только по курируемым статьям.
DEFAULT_SYSTEM_PROMPT = (
    "Ты — ИИ-консультант «Компендиума» отделения анестезиологии. Отвечай "
    "строго на русском языке и ТОЛЬКО на основе разделов курируемых статей из "
    "контекста. Каждый использованный раздел отмечай номером в квадратных "
    "скобках: [1], [2] и т.д. Если в разделах нет ответа на вопрос, напиши: "
    '"В «Компендиуме» эта информация не найдена". Не выдумывай номера приказов, '
    "пункты, сроки и дозировки. Не используй знания, выходящие за пределы разделов."
)

#: Ключи настроек в таблице settings.
SETTING_PROMPT = "system_prompt"
SETTING_TOP_K = "top_k"
SETTING_TEMPERATURE = "temperature"
SETTING_HISTORY_MESSAGES = "history_messages"

#: Диапазоны параметров.
TOP_K_MIN, TOP_K_MAX = 1, 20
TEMPERATURE_MIN, TEMPERATURE_MAX = 0.0, 1.0
HISTORY_MIN, HISTORY_MAX = 0, 20
DEFAULT_TOP_K = 5
DEFAULT_TEMPERATURE = 0.1
DEFAULT_HISTORY_MESSAGES = 6

#: Максимальная длина исторического сообщения в контексте.
_HISTORY_CONTENT_LIMIT = 4000

#: Роли, которым доступно курирование (источники и статьи).
CURATOR_ROLES = (HEAD, EDITOR)


class WikiForbidden(ValueError):
    """Нет прав на операцию (роутер отвечает 403)."""


class WikiService:
    """Бизнес-логика «Компендиума»: ответы, статьи, источники, публикация."""

    def __init__(
        self,
        store: SqliteWikiStore,
        llm: LLMClient,
        vision: VisionClient | None = None,
        sources_dir: str | Path | None = None,
    ) -> None:
        """Собрать сервис «Компендиума».

        sources_dir — папка PDF-источников на диске (ADR-0020). Без неё сервис
        берёт путь из настроек модуля; тесты и перенос указывают свою папку.
        """
        from docapp.wiki.config import load_wiki_config

        self.store = store
        self.llm = llm
        self.vision = vision
        self.sources_dir = Path(sources_dir) if sources_dir else load_wiki_config().sources_dir
        self._reload_index()

    # ── индекс опубликованных секций ──────────────────────────────────

    def _published_sections(self) -> list[dict]:
        sections = []
        for article in self.store.list_published_articles():
            rev = self.store.published_revision(article["id"])
            if rev is None:
                continue
            for section in split_sections(rev["body_md"]):
                sections.append(
                    {
                        "article_id": article["id"],
                        "article_title": article["title"] or title_of(rev["body_md"]),
                        "title": section.title,
                        "body": section.body,
                    }
                )
        return sections

    def _reload_index(self) -> None:
        self.index = WikiSearch(self._published_sections())

    def stats(self) -> dict:
        return {
            "articles": len(self.store.list_articles(include_archived=False)),
            "published": len(self.store.list_published_articles()),
            "sources": len(self.store.list_sources()),
            "sections": self.index.size,
        }

    # ── настройки ─────────────────────────────────────────────────────

    def _int_setting(self, key: str, default: int, lo: int, hi: int) -> int:
        raw = self.store.get_setting(key)
        if raw is None:
            return default
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return default
        return value if lo <= value <= hi else default

    def _float_setting(self, key: str, default: float, lo: float, hi: float) -> float:
        raw = self.store.get_setting(key)
        if raw is None:
            return default
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return default
        return value if lo <= value <= hi else default

    def system_prompt(self) -> str:
        prompt = (self.store.get_setting(SETTING_PROMPT) or "").strip()
        return prompt or DEFAULT_SYSTEM_PROMPT

    def effective_top_k(self) -> int:
        return self._int_setting(SETTING_TOP_K, DEFAULT_TOP_K, TOP_K_MIN, TOP_K_MAX)

    def effective_temperature(self) -> float:
        return self._float_setting(SETTING_TEMPERATURE, DEFAULT_TEMPERATURE, TEMPERATURE_MIN, TEMPERATURE_MAX)

    def history_messages(self) -> int:
        return self._int_setting(SETTING_HISTORY_MESSAGES, DEFAULT_HISTORY_MESSAGES, HISTORY_MIN, HISTORY_MAX)

    def settings(self) -> dict:
        return {
            SETTING_PROMPT: self.system_prompt(),
            SETTING_TOP_K: self.effective_top_k(),
            SETTING_TEMPERATURE: self.effective_temperature(),
            SETTING_HISTORY_MESSAGES: self.history_messages(),
            "defaults": {
                SETTING_PROMPT: DEFAULT_SYSTEM_PROMPT,
                SETTING_TOP_K: DEFAULT_TOP_K,
                SETTING_TEMPERATURE: DEFAULT_TEMPERATURE,
                SETTING_HISTORY_MESSAGES: DEFAULT_HISTORY_MESSAGES,
            },
        }

    def update_settings(self, values: dict) -> dict:
        prompt = values.get(SETTING_PROMPT)
        if isinstance(prompt, str):
            self.store.set_setting(SETTING_PROMPT, prompt.strip())
        for key, lo, hi in (
            (SETTING_TOP_K, TOP_K_MIN, TOP_K_MAX),
            (SETTING_HISTORY_MESSAGES, HISTORY_MIN, HISTORY_MAX),
        ):
            raw = values.get(key)
            if raw is None or raw == "":
                continue
            try:
                value = int(raw)
            except (TypeError, ValueError):
                continue
            if lo <= value <= hi:
                self.store.set_setting(key, str(value))
        raw_temp = values.get(SETTING_TEMPERATURE)
        if raw_temp is not None and raw_temp != "":
            try:
                temp = float(raw_temp)
            except (TypeError, ValueError):
                temp = None
            if temp is not None and TEMPERATURE_MIN <= temp <= TEMPERATURE_MAX:
                self.store.set_setting(SETTING_TEMPERATURE, str(temp))
        return self.settings()

    # ── вопрос-ответ ──────────────────────────────────────────────────

    def _require_access(self, role: str) -> None:
        if role == NURSE:
            raise WikiForbidden("Медсёстрам доступ закрыт")

    def ask_allowed(self, role: str) -> bool:
        return role != NURSE

    async def ask(
        self,
        employee_id: int,
        conversation_id: str,
        question: str,
        role: str = "",
        history: list[dict] | None = None,
    ) -> AsyncIterator[dict]:
        """Ответить на вопрос: поиск секций, стриминг ответа, сохранение диалога.

        События: {"type": "delta", "text": ...} и финальное {"type": "done",
        "citations": [...], "prompt_tokens": N, "completion_tokens": M}.
        Если нет опубликованных статей — подсказка, диалог не сохраняется.
        """
        self._require_access(role)
        if self.index.size == 0:
            yield {
                "type": "delta",
                "text": (
                    "В «Компендиуме» пока нет опубликованных статей. Заведующий "
                    "или редактор должны создать .md-статью и опубликовать её."
                ),
            }
            yield {"type": "done", "citations": [], "prompt_tokens": 0, "completion_tokens": 0}
            return

        system_prompt = self.system_prompt()
        top_k = self.effective_top_k()
        history_messages = self.history_messages()

        if history is None:
            rows = self.store.list_messages(conversation_id)[-history_messages:]
            history = [{"role": row["role"], "content": row["content"]} for row in rows]
        else:
            history = history[-history_messages:]

        hits = self.index.search(question, n=top_k)
        context = "\n\n".join(
            f"[{i}] {hit['article_title']} — {hit['title']}\n{hit['body']}"
            for i, hit in enumerate(hits, start=1)
        )
        citations = [
            {
                "article_id": hit["article_id"],
                "article_title": hit["article_title"],
                "section": hit["title"],
                "snippet": hit["body"][:200],
            }
            for hit in hits
        ]

        truncated_history = [
            {"role": msg["role"], "content": msg["content"][:_HISTORY_CONTENT_LIMIT]}
            for msg in history
        ]
        messages = (
            [{"role": "system", "content": system_prompt}]
            + truncated_history
            + [
                {
                    "role": "user",
                    "content": f"Вопрос: {question}\n\nРазделы статей:\n{context}",
                }
            ]
        )

        now = datetime.now().isoformat(timespec="seconds")
        self.store.add_message(employee_id, conversation_id, "user", question, "[]", 0, 0, now)

        buffer: list[str] = []
        async for token in self.llm.stream_chat(messages, temperature=self.effective_temperature()):
            buffer.append(token)
            yield {"type": "delta", "text": token}

        usage = self.llm.last_usage or {"prompt_tokens": 0, "completion_tokens": 0}
        full_text = "".join(buffer)
        self.store.add_message(
            employee_id,
            conversation_id,
            "assistant",
            full_text,
            json.dumps(citations, ensure_ascii=False),
            usage["prompt_tokens"],
            usage["completion_tokens"],
            datetime.now().isoformat(timespec="seconds"),
        )
        yield {
            "type": "done",
            "citations": citations,
            "prompt_tokens": usage["prompt_tokens"],
            "completion_tokens": usage["completion_tokens"],
        }

    def conversation(self, employee_id: int, conversation_id: str) -> list[dict]:
        rows = self.store.list_messages(conversation_id)
        if not rows:
            return []
        if rows[0]["employee_id"] != employee_id:
            return []
        return [
            {"role": row["role"], "content": row["content"],
             "citations": json.loads(row["citations"] or "[]"), "created_at": row["created_at"]}
            for row in rows
        ]

    # ── источники ─────────────────────────────────────────────────────

    def _require_curator(self, role: str) -> None:
        if role not in CURATOR_ROLES:
            raise WikiForbidden("Доступно заведующему или редактору")

    def add_source(self, uploaded_by: int, role: str, filename: str, data: bytes) -> dict:
        """Принять PDF: файл — в хранилище, метаданные — в БД (ADR-0020)."""
        self._require_curator(role)
        from pathlib import Path

        from docapp.wiki import files
        from docapp.wiki.parser import page_count as _page_count

        stored_name = files.store(data, self.sources_dir)
        try:
            pages = _page_count(files.path_of(self.sources_dir, stored_name))
        except Exception:
            # Битый PDF: файл в хранилище не оставляем — иначе он останется
            # мусором без записи в БД.
            files.remove(self.sources_dir, stored_name)
            raise

        title = Path(filename).stem
        now = datetime.now().isoformat(timespec="seconds")
        source_id = self.store.add_source(
            filename=filename,
            doc_number="",
            title=title,
            added_at=now,
            uploaded_by=uploaded_by,
            stored_name=stored_name,
            page_count=pages,
        )
        return {"id": source_id, "filename": filename, "page_count": pages, "ocr_status": "pending"}

    def source(self, source_id: int) -> dict | None:
        row = self.store.get_source(source_id)
        if row is None:
            return None
        return {
            "id": row["id"],
            "filename": row["filename"],
            "doc_number": row["doc_number"],
            "title": row["title"],
            "added_at": row["added_at"],
            "page_count": row["page_count"],
            "ocr_status": row["ocr_status"],
            "ocr_error": row["ocr_error"],
        }

    def sources(self) -> list[dict]:
        return [self.source(r["id"]) for r in self.store.list_sources()]

    def source_text(self, source_id: int) -> str | None:
        row = self.store.get_source(source_id)
        return row["ocr_text"] if row is not None else None

    def source_file(self, source_id: int) -> dict | None:
        """Байты PDF из хранилища. None — если файла нет (или запись без файла)."""
        from docapp.wiki import files

        row = self.store.get_source(source_id)
        if row is None or not row["stored_name"]:
            return None
        try:
            data = files.read(self.sources_dir, row["stored_name"])
        except files.SourceFileError:
            return None
        return {"data": data, "name": row["filename"]}

    def run_ocr(self, source_id: int) -> None:
        """Фоновая обработка источника: OCR (текст/таблицы), статус в БД.

        Вызывается в отдельном потоке (см. router). Ошибки пишутся в ocr_error,
        статус — 'error'; файл в хранилище остаётся доступен для скачивания.
        """
        from docapp.wiki import files

        row = self.store.get_source(source_id)
        if row is None:
            return
        if not row["stored_name"]:
            self.store.set_ocr_status(
                source_id, "error", "файл источника не сохранён в хранилище"
            )
            return
        self.store.set_ocr_status(source_id, "processing")
        try:
            path = files.path_of(self.sources_dir, row["stored_name"])
            text, tables, pages = ocr.process_pdf(path, self.vision)
            self.store.set_ocr_result(
                source_id, text, ocr.tables_to_json(tables), pages
            )
        except Exception as exc:  # noqa: BLE001 — статус сохраняется для UI
            self.store.set_ocr_status(source_id, "error", str(exc))

    def delete_source(self, source_id: int, role: str) -> None:
        """Удалить источник: запись и файл в хранилище.

        Файл убирается только тогда, когда на него больше не ссылается ни одна
        запись: одинаковые PDF хранятся один раз (хеш содержимого).
        """
        from docapp.wiki import files

        self._require_curator(role)
        row = self.store.get_source(source_id)
        if row is None:
            raise ValueError("Источник не найден")
        self.store.delete_source(source_id)
        stored_name = row["stored_name"]
        if stored_name and not self.store.stored_name_in_use(stored_name):
            files.remove(self.sources_dir, stored_name)

    # ── статьи и ревизии ──────────────────────────────────────────────

    def articles(self) -> list[dict]:
        result = []
        for row in self.store.list_articles(include_archived=True):
            cur = self.store.current_revision(row["id"])
            result.append(
                {
                    "id": row["id"],
                    "title": row["title"],
                    "status": row["status"],
                    "created_by": row["created_by"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                    "version": cur["version"] if cur else 0,
                    "published_revision_id": row["published_revision_id"],
                }
            )
        return result

    def article(self, article_id: int) -> dict | None:
        row = self.store.get_article(article_id)
        if row is None:
            return None
        cur = self.store.current_revision(article_id)
        links = self.store.list_links(article_id)
        return {
            "id": row["id"],
            "title": row["title"],
            "status": row["status"],
            "body_md": cur["body_md"] if cur else "",
            "version": cur["version"] if cur else 0,
            "published_revision_id": row["published_revision_id"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "links": [
                {"source_id": l["source_id"], "anchor": l["anchor"],
                 "filename": l["filename"], "doc_number": l["doc_number"],
                 "source_title": l["source_title"]}
                for l in links
            ],
        }

    def article_public(self, article_id: int) -> dict | None:
        """Опубликованный текст статьи (для чтения врачами)."""
        row = self.store.get_article(article_id)
        if row is None or row["status"] != "published":
            return None
        rev = self.store.published_revision(article_id)
        if rev is None:
            return None
        links = self.store.list_links(article_id)
        return {
            "id": row["id"],
            "title": row["title"],
            "body_md": rev["body_md"],
            "version": rev["version"],
            "updated_at": row["updated_at"],
            "links": [
                {"source_id": l["source_id"], "anchor": l["anchor"],
                 "filename": l["filename"], "doc_number": l["doc_number"],
                 "source_title": l["source_title"]}
                for l in links
            ],
        }

    def save_article(
        self,
        user_id: int,
        role: str,
        article_id: int | None,
        body_md: str,
        change_note: str = "",
        source_links: list[dict] | None = None,
    ) -> dict:
        """Создать или сохранить новую ревизию статьи (draft, не публикуется)."""
        self._require_curator(role)
        if not body_md.strip():
            raise ValueError("Текст статьи не может быть пустым")
        now = datetime.now().isoformat(timespec="seconds")
        title = title_of(body_md)

        if article_id is None:
            article_id = self.store.add_article(title, user_id, now)
        else:
            if self.store.get_article(article_id) is None:
                raise ValueError("Статья не найдена")
            self.store.set_article_status(article_id, "draft", now)
            # Заголовок статьи обновляется по текущему тексту.
            self.store.set_title(article_id, title, now)

        version = self.store.next_version(article_id)
        rendered = render_plain(body_md)
        rev_id = self.store.add_revision(
            article_id, version, body_md, rendered, user_id, now, change_note
        )

        if source_links is not None:
            self.store.clear_links(article_id)
            for link in source_links:
                sid = link.get("source_id")
                if sid is None:
                    continue
                self.store.add_link(article_id, int(sid), str(link.get("anchor") or ""))
        return self.article(article_id)

    def publish(self, user_id: int, role: str, article_id: int) -> dict:
        self._require_curator(role)
        cur = self.store.current_revision(article_id)
        if cur is None:
            raise ValueError("У статьи нет черновика для публикации")
        now = datetime.now().isoformat(timespec="seconds")
        self.store.set_published_revision(article_id, cur["id"], now)
        self._reload_index()
        return self.article(article_id)

    def unpublish(self, user_id: int, role: str, article_id: int) -> dict:
        self._require_curator(role)
        row = self.store.get_article(article_id)
        if row is None:
            raise ValueError("Статья не найдена")
        now = datetime.now().isoformat(timespec="seconds")
        self.store.set_article_status(article_id, "draft", now)
        self._reload_index()
        return self.article(article_id)

    def delete_article(self, user_id: int, role: str, article_id: int) -> None:
        self._require_curator(role)
        if self.store.get_article(article_id) is None:
            raise ValueError("Статья не найдена")
        self.store.delete_article(article_id)
        self._reload_index()

    def revisions(self, article_id: int) -> list[dict]:
        return [
            {
                "id": r["id"],
                "version": r["version"],
                "edited_by": r["edited_by"],
                "created_at": r["created_at"],
                "change_note": r["change_note"],
                "is_current": bool(r["is_current"]),
            }
            for r in self.store.list_revisions(article_id)
        ]

    # ── аналитика ─────────────────────────────────────────────────────

    def recent_questions(
        self,
        limit: int = 100,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> list[dict]:
        employee_ids = [row["employee_id"] for row in self.store.token_totals()]
        questions: list[dict] = []
        for emp_id in employee_ids:
            for conv in self.store.list_conversations(emp_id):
                for msg in self.store.list_messages(conv["conversation_id"]):
                    if msg["role"] != "user":
                        continue
                    created = msg["created_at"]
                    if from_date and created < from_date:
                        continue
                    if to_date and created[:10] > to_date:
                        continue
                    questions.append(
                        {"employee_id": emp_id, "content": msg["content"],
                         "created_at": created, "conversation_id": conv["conversation_id"]}
                    )
        questions.sort(key=lambda q: q["created_at"], reverse=True)
        return questions[:limit]

    def token_totals(self, from_date=None, to_date=None):
        return [dict(r) for r in self.store.token_totals(from_date, to_date)]

    def token_totals_by_day(self, from_date=None, to_date=None):
        return [dict(r) for r in self.store.token_totals_by_day(from_date, to_date)]
