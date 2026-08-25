"""Сервис консультанта: RAG-цепочка «вопрос -> поиск по индексу -> ответ LLM».

Связывает хранилище (SqliteConsultStore), индекс (SearchIndex),
клиент эмбеддингов (EmbeddingClient) и LLM-клиент (LLMClient) в один
сервис, который умеет: отвечать на вопросы с цитатами фрагментов
приказов (задача T8, требования F2/F3/F5 ТЗ-консультанта), сохранять
историю диалогов и учёт токенов в БД, а также отдавать данные для
аналитики заведующего (последние вопросы, расход токенов).

Сервис не знает про settings: число фрагментов контекста задаётся
параметром top_k в конструкторе (по умолчанию 5).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import datetime

from docapp.consult.embed import EmbeddingClient
from docapp.consult.llm import LLMClient
from docapp.consult.search import SearchIndex
from docapp.consult.store import SqliteConsultStore

#: Системный промпт: ответы только по фрагментам из контекста, с номерами цитат.
#: Промпт по умолчанию; заведующий может переопределить его в настройках
#: консультанта (таблица settings в consult.db, вкладка «Настройки»).
DEFAULT_SYSTEM_PROMPT = (
    "Ты — ИИ-консультант по внутренним приказам отделения анестезиологии. "
    "Отвечай строго на русском языке и ТОЛЬКО на основе фрагментов документов "
    "из контекста. Каждый использованный фрагмент отмечай номером в квадратных "
    "скобках: [1], [2] и т.д. Если в фрагментах нет ответа на вопрос, напиши: "
    '"В предоставленных приказах эта информация не найдена". Не выдумывай '
    "номера приказов, пункты, сроки и дозировки. Не используй знания, "
    "выходящие за пределы фрагментов."
)

#: Максимальная длина содержимого исторического сообщения (символов).
_HISTORY_CONTENT_LIMIT = 4000
#: Число последних сообщений истории, попадающих в контекст LLM (по умолчанию).
DEFAULT_HISTORY_MESSAGES = 6

#: Ключи настроек консультанта в таблице settings (consult.db).
SETTING_PROMPT = "system_prompt"
SETTING_TOP_K = "top_k"
SETTING_TEMPERATURE = "temperature"
SETTING_HISTORY_MESSAGES = "history_messages"

#: Диапазоны параметров, принимаемые настройками (защита от опечаток заведующего).
TOP_K_MIN, TOP_K_MAX = 1, 20
TEMPERATURE_MIN, TEMPERATURE_MAX = 0.0, 1.0
HISTORY_MIN, HISTORY_MAX = 0, 20


class ConsultService:
    """RAG-сервис консультанта: поиск по индексу приказов + ответ LLM.

    Индекс строится из хранилища при создании; после загрузки нового
    consult.db (другим процессом) нужно вызвать reload_index().
    """

    def __init__(
        self,
        store: SqliteConsultStore,
        embed: EmbeddingClient,
        llm: LLMClient,
        top_k: int = 5,
    ) -> None:
        """Создать сервис: хранилище, эмбеддер, LLM и индекс в памяти.

        top_k — сколько фрагментов приказов попадает в контекст ответа
        (по умолчанию; может переопределяться настройкой 'top_k' в БД).
        """
        self.store = store
        self.embed = embed
        self.llm = llm
        self.top_k = top_k
        self.index = SearchIndex(store)

    # ── настройки консультанта (системный промпт и параметры) ────────

    def _int_setting(self, key: str, default: int, lo: int, hi: int) -> int:
        """Целочисленная настройка с валидацией диапазона; невалидно — default."""
        raw = self.store.get_setting(key)
        if raw is None:
            return default
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return default
        return value if lo <= value <= hi else default

    def _float_setting(self, key: str, default: float, lo: float, hi: float) -> float:
        """Дробная настройка с валидацией диапазона; невалидно — default."""
        raw = self.store.get_setting(key)
        if raw is None:
            return default
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return default
        return value if lo <= value <= hi else default

    def system_prompt(self) -> str:
        """Системный промпт из настроек (пустой/отсутствующий — DEFAULT_SYSTEM_PROMPT)."""
        prompt = (self.store.get_setting(SETTING_PROMPT) or "").strip()
        return prompt or DEFAULT_SYSTEM_PROMPT

    def history_messages(self) -> int:
        """Число последних сообщений истории в контексте LLM (из настроек)."""
        return self._int_setting(SETTING_HISTORY_MESSAGES, DEFAULT_HISTORY_MESSAGES, HISTORY_MIN, HISTORY_MAX)

    def effective_top_k(self) -> int:
        """top_k из настроек (иначе — значение конструктора)."""
        return self._int_setting(SETTING_TOP_K, self.top_k, TOP_K_MIN, TOP_K_MAX)

    def effective_temperature(self) -> float:
        """Температура LLM из настроек (иначе — 0.1)."""
        return self._float_setting(SETTING_TEMPERATURE, 0.1, TEMPERATURE_MIN, TEMPERATURE_MAX)

    def settings(self) -> dict:
        """Текущие настройки консультанта с фактически применяемыми значениями."""
        return {
            SETTING_PROMPT: self.system_prompt(),
            SETTING_TOP_K: self.effective_top_k(),
            SETTING_TEMPERATURE: self.effective_temperature(),
            SETTING_HISTORY_MESSAGES: self.history_messages(),
            "defaults": {
                SETTING_PROMPT: DEFAULT_SYSTEM_PROMPT,
                SETTING_TOP_K: self.top_k,
                SETTING_TEMPERATURE: 0.1,
                SETTING_HISTORY_MESSAGES: DEFAULT_HISTORY_MESSAGES,
            },
        }

    def update_settings(self, values: dict[str, str | int | float | None]) -> dict:
        """Сохранить настройки консультанта; невалидные параметры не пишутся.

        Ключи — SETTING_*; системный промпт сохраняется как строка (можно
        пустой — тогда применяется DEFAULT_SYSTEM_PROMPT). Возвращает
        settings() после записи.
        """
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

    def reload_index(self) -> None:
        """Пересоздать индекс из хранилища (после загрузки нового consult.db)."""
        self.index = SearchIndex(self.store)

    def index_stats(self) -> dict:
        """Статистика индекса: число документов и чанков."""
        return {
            "documents": len(self.store.list_documents()),
            "chunks": self.store.count_chunks(),
        }

    def documents(self) -> list[dict]:
        """Список документов индекса для страницы «Документы» (с id для ссылок)."""
        return [
            {"id": row["id"], "filename": row["filename"], "doc_number": row["doc_number"],
             "title": row["title"], "added_at": row["added_at"]}
            for row in self.store.list_documents()
        ]

    def document_text(self, document_id: int) -> str | None:
        """Полный текст приказа для страницы «Читать» (None, если нет)."""
        row = self.store.get_document(document_id)
        return row["full_text"] if row is not None else None

    def document_source(self, document_id: int) -> dict | None:
        """Оригинальный файл приказа: {"source": bytes, "source_name": str} или None."""
        row = self.store.get_document(document_id)
        if row is None or row["source"] is None:
            return None
        return {"source": row["source"], "source_name": row["source_name"] or row["filename"]}

    def conversation(self, employee_id: int, conversation_id: str) -> list[dict]:
        """Сообщения беседы сотрудника (для восстановления диалога в интерфейсе)."""
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

    async def ask(
        self,
        employee_id: int,
        conversation_id: str,
        question: str,
        history: list[dict] | None = None,
    ) -> AsyncIterator[dict]:
        """Ответить на вопрос: поиск фрагментов, стриминг ответа, сохранение диалога.

        Генерирует события: {"type": "delta", "text": ...} по токенам ответа
        и в конце {"type": "done", "citations": [...], "prompt_tokens": N,
        "completion_tokens": M}. Если индекс пуст — сообщения в БД не
        сохраняются, выдаётся подсказка про сборку индекса.

        history — последние сообщения диалога (role/content); если None,
        история загружается из БД (последние 6 сообщений беседы).
        """
        if self.store.count_chunks() == 0:
            yield {
                "type": "delta",
                "text": (
                    "Индекс пуст. Положите .docx/.pdf в папку "
                    "data/consult/documents/ и выполните python -m docapp.consult, "
                    "затем нажмите «Перезагрузить индекс»."
                ),
            }
            yield {"type": "done", "citations": [], "prompt_tokens": 0, "completion_tokens": 0}
            return

        # Настройки консультанта: промпт, число фрагментов, температура, история.
        system_prompt = self.system_prompt()
        top_k = self.effective_top_k()
        history_messages = self.history_messages()

        # История: переданная или из БД, в обоих случаях — последние N сообщений.
        if history is None:
            rows = self.store.list_messages(conversation_id)[-history_messages:]
            history = [{"role": row["role"], "content": row["content"]} for row in rows]
        else:
            history = history[-history_messages:]

        # Поиск фрагментов по гибридному индексу.
        query_vec = self.embed.embed_query(question)
        hits = self.index.search(question, query_vec, n=top_k)

        # Контекст для LLM: "[1] текст\n\n[2] текст..." и цитаты для ответа клиенту.
        context = "\n\n".join(f"[{i}] {hit['text']}" for i, hit in enumerate(hits, start=1))
        citations = [
            {
                "document_id": hit.get("document_id"),
                "doc_number": hit["doc_number"],
                "doc_title": hit["doc_title"],
                "section": hit["section"],
                "snippet": hit["text"][:200],
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
                    "content": f"Вопрос: {question}\n\nФрагменты приказов:\n{context}",
                }
            ]
        )

        # Сохранить вопрос пользователя до начала стриминга.
        now = datetime.now().isoformat(timespec="seconds")
        self.store.add_message(employee_id, conversation_id, "user", question, "[]", 0, 0, now)

        # Стриминг ответа; полный текст копится в буфер для записи в БД.
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

    def recent_questions(
        self,
        employee_id: int | None = None,
        limit: int = 100,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> list[dict]:
        """Последние вопросы сотрудников (сообщения role=user) с датами.

        По конкретному сотруднику (employee_id) или по всем — для страницы
        аналитики заведующего. Свежие вопросы сверху, не более limit.
        from_date/to_date — необязательные границы 'YYYY-MM-DD' (включительно)
        по created_at.
        """
        if employee_id is None:
            employee_ids = [row["employee_id"] for row in self.store.token_totals()]
        else:
            employee_ids = [employee_id]

        questions: list[dict] = []
        for emp_id in employee_ids:
            for conv in self.store.list_conversations(emp_id):
                conversation_id = conv["conversation_id"]
                for msg in self.store.list_messages(conversation_id):
                    if msg["role"] != "user":
                        continue
                    created = msg["created_at"]
                    if from_date and created < from_date:
                        continue
                    if to_date and created[:10] > to_date:
                        continue
                    questions.append(
                        {
                            "employee_id": emp_id,
                            "content": msg["content"],
                            "created_at": created,
                            "conversation_id": conversation_id,
                        }
                    )

        questions.sort(key=lambda q: q["created_at"], reverse=True)
        return questions[:limit]

    def token_totals(
        self, from_date: str | None = None, to_date: str | None = None
    ) -> list[dict]:
        """Расход токенов по сотрудникам (обёртка над store.token_totals()).

        from_date/to_date — необязательные границы 'YYYY-MM-DD' (включительно).
        """
        return [dict(row) for row in self.store.token_totals(from_date, to_date)]

    def token_totals_by_day(
        self, from_date: str | None = None, to_date: str | None = None
    ) -> list[dict]:
        """Расход токенов по дням (обёртка над store.token_totals_by_day())."""
        return [dict(row) for row in self.store.token_totals_by_day(from_date, to_date)]
