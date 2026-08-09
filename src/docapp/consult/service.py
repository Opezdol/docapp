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
SYSTEM_PROMPT = (
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
#: Число последних сообщений истории, попадающих в контекст LLM.
_HISTORY_MESSAGES = 6


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

        top_k — сколько фрагментов приказов попадает в контекст ответа.
        """
        self.store = store
        self.embed = embed
        self.llm = llm
        self.top_k = top_k
        self.index = SearchIndex(store)

    def reload_index(self) -> None:
        """Пересоздать индекс из хранилища (после загрузки нового consult.db)."""
        self.index = SearchIndex(self.store)

    def index_stats(self) -> dict:
        """Статистика индекса: число документов и чанков."""
        return {
            "documents": len(self.store.list_documents()),
            "chunks": self.store.count_chunks(),
        }

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
                    "Индекс пуст. Соберите индекс командой "
                    "python -m docapp.consult <папка> и перезагрузите его."
                ),
            }
            yield {"type": "done", "citations": [], "prompt_tokens": 0, "completion_tokens": 0}
            return

        # История: переданная или из БД, в обоих случаях — последние 6 сообщений.
        if history is None:
            rows = self.store.list_messages(conversation_id)[-_HISTORY_MESSAGES:]
            history = [{"role": row["role"], "content": row["content"]} for row in rows]
        else:
            history = history[-_HISTORY_MESSAGES:]

        # Поиск фрагментов по гибридному индексу.
        query_vec = self.embed.embed_query(question)
        hits = self.index.search(question, query_vec, n=self.top_k)

        # Контекст для LLM: "[1] текст\n\n[2] текст..." и цитаты для ответа клиенту.
        context = "\n\n".join(f"[{i}] {hit['text']}" for i, hit in enumerate(hits, start=1))
        citations = [
            {
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
            [{"role": "system", "content": SYSTEM_PROMPT}]
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
        async for token in self.llm.stream_chat(messages):
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
        self, employee_id: int | None = None, limit: int = 50
    ) -> list[dict]:
        """Последние вопросы сотрудников (сообщения role=user) с датами.

        По конкретному сотруднику (employee_id) или по всем — для страницы
        аналитики заведующего. Свежие вопросы сверху, не более limit.
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
                    questions.append(
                        {
                            "employee_id": emp_id,
                            "content": msg["content"],
                            "created_at": msg["created_at"],
                            "conversation_id": conversation_id,
                        }
                    )

        questions.sort(key=lambda q: q["created_at"], reverse=True)
        return questions[:limit]

    def token_totals(self) -> list[dict]:
        """Расход токенов по сотрудникам (обёртка над store.token_totals())."""
        return [dict(row) for row in self.store.token_totals()]
