"""Маршруты подприложения «Приказы»: страница консультанта и JSON/SSE-API.

Задача T9 ТЗ-консультанта: GET /orders (F1 — страница консультанта),
POST /orders/ask (F2/F3/F5 — стриминг ответа с цитатами), GET /orders/documents
(F6 — список документов индекса), POST /orders/reindex (F7 — перезагрузка
индекса, только заведующий). Авторизация — current_user из docapp.web.app;
редиректы и коды ошибок — в стиле остального приложения.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

import docapp.web.app
from docapp.consult.service import ConsultService
from docapp.consult.store import SqliteConsultStore
from docapp.domain.employee import HEAD

#: Директории шаблонов: сначала консультанта, затем общие (чтобы consult.html
#: мог наследовать base.html). Starlette принимает список директорий.
consult_templates_dir = Path(__file__).parent / "templates"
web_templates_dir = Path(docapp.web.app.__file__).parent / "templates"
TEMPLATES = Jinja2Templates(directory=[consult_templates_dir, web_templates_dir])

router = APIRouter()


def _service(request: Request) -> ConsultService:
    """Сервис консультанта из state приложения."""
    return request.app.state.consult["service"]


@router.get("/orders", response_class=HTMLResponse)
def orders_page(request: Request):
    """Страница консультанта по приказам (F1): поле вопроса и статистика индекса."""
    user = docapp.web.app.current_user(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    return TEMPLATES.TemplateResponse(
        request,
        "consult.html",
        {
            "user": user,
            "flash": None,
            "stats": _service(request).index_stats(),
            "is_head": user.role == HEAD,
        },
    )


@router.post("/orders/ask")
async def ask_question(request: Request):
    """Ответ консультанта на вопрос (F2/F3/F5) потоком SSE.

    Тело: {"question": str, "conversation_id": str | None}. Если
    conversation_id не задан — генерируется новый. События потока:
    {"type": "delta", "text": ...} по токенам ответа и финальное
    {"type": "done", "citations": [...], "prompt_tokens": N,
    "completion_tokens": M, "conversation_id": ...}.
    """
    user = docapp.web.app.current_user(request)
    if user is None:
        return JSONResponse({"error": "Требуется авторизация"}, status_code=401)
    body = await request.json()
    question = str(body.get("question") or "")
    if not question.strip():
        return JSONResponse({"error": "Пустой вопрос"}, status_code=400)
    conversation_id = body.get("conversation_id") or uuid.uuid4().hex
    service = _service(request)

    async def gen():
        try:
            async for evt in service.ask(user.id, conversation_id, question):
                if evt["type"] == "done":
                    evt["conversation_id"] = conversation_id
                yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
        except RuntimeError as exc:
            error_event = {"type": "delta", "text": f"Ошибка сервиса: {exc}"}
            yield f"data: {json.dumps(error_event, ensure_ascii=False)}\n\n"
            done_event = {"type": "done", "conversation_id": conversation_id}
            yield f"data: {json.dumps(done_event, ensure_ascii=False)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.get("/orders/documents")
def documents_list(request: Request):
    """Список документов индекса (F6): файлы и число фрагментов (всем ролям)."""
    user = docapp.web.app.current_user(request)
    if user is None:
        return JSONResponse({"error": "Требуется авторизация"}, status_code=401)
    service = _service(request)
    return {
        "documents": service.documents(),
        "chunks": service.index_stats()["chunks"],
    }


@router.post("/orders/reindex")
def reindex(request: Request):
    """Перезагрузить индекс из data/consult/consult.db (F7). Только заведующий.

    Закрывает старое хранилище, пересоздаёт SqliteConsultStore и сервис
    поверх существующих клиентов эмбеддингов/LLM, перестраивает индекс.
    """
    user = docapp.web.app.current_user(request)
    if user is None:
        return JSONResponse({"error": "Требуется авторизация"}, status_code=401)
    if user.role != HEAD:
        return JSONResponse({"error": "Только заведующий"}, status_code=403)
    state = request.app.state.consult
    old = state["service"]
    old.store.close()
    store = SqliteConsultStore(state["db_path"])
    service = ConsultService(store, state["embed"], state["llm"])
    service.reload_index()
    state["service"] = service
    return {"ok": True, **service.index_stats()}
