"""Маршруты подприложения «Приказы»: страница консультанта и JSON/SSE-API.

Задача T9 ТЗ-консультанта: GET /orders (F1 — страница консультанта),
POST /orders/ask (F2/F3/F5 — стриминг ответа с цитатами), GET /orders/documents
(F6 — список документов индекса), POST /orders/reindex (F7 — пересборка
индекса, только заведующий), POST /orders/documents/upload (F11 — веб-загрузка
документов с авто-переиндексацией, только заведующий), GET /orders/status
(F11 — статус фоновой пересборки). Авторизация — current_user из
docapp.web.app; редиректы и коды ошибок — в стиле остального приложения.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.templating import Jinja2Templates

import docapp.web.app
from docapp.consult.build import build_index
from docapp.consult.service import ConsultService
from docapp.consult.store import SqliteConsultStore
from docapp.domain.employee import HEAD

#: Директории шаблонов: сначала консультанта, затем общие (чтобы consult.html
#: мог наследовать base.html). Starlette принимает список директорий.
consult_templates_dir = Path(__file__).parent / "templates"
web_templates_dir = Path(docapp.web.app.__file__).parent / "templates"
TEMPLATES = Jinja2Templates(directory=[consult_templates_dir, web_templates_dir])

router = APIRouter()

MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 МБ


def _service(request: Request) -> ConsultService:
    """Сервис консультанта из state приложения."""
    return request.app.state.consult["service"]


def _media_type(filename: str) -> str:
    """Media type по расширению файла для Content-Type при скачивании (F10)."""
    return {
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".doc": "application/msword",
    }.get(Path(filename).suffix.lower(), "application/octet-stream")


def _content_disposition(filename: str) -> str:
    """Заголовок Content-Disposition: ASCII-имя в кавычках, кириллица — RFC 5987 (filename*)."""
    name = Path(filename).name
    try:
        name.encode("latin-1")
    except UnicodeEncodeError:
        return f"attachment; filename*=UTF-8''{quote(name)}"
    return f'attachment; filename="{name}"'


def _run_rebuild(state: dict) -> None:
    """Пересобрать индекс из статичной папки и переключить сервис.

    Сборка идёт во временный файл (старый остаётся рабочим при сбое),
    затем атомарно заменяется; история диалогов (messages) мигрирует
    из старого файла в новый. Состояние пишется в state['status'].
    """
    try:
        config = state["config"]
        temp_db = state["db_path"].with_suffix(".db.new")
        build_index(config.docs_dir, config, temp_db)     # временный файл
        old = state["service"]
        messages = old.store.all_messages()               # история ДО закрытия
        old.store.checkpoint()                            # WAL -> основной файл
        old.store.close()
        os.replace(temp_db, state["db_path"])             # атомарная замена
        # Осиротевшие WAL-сайдкары старого файла: если к consult.db ещё
        # открыто другое соединение (напр. созданное create_app и не
        # закрытое), они переживают os.replace, и SQLite «отыгрывает» их
        # в новый файл при первом открытии — дублируя мигрированные
        # сообщения. Удаляем их сразу после замены.
        for suffix in (".db-wal", ".db-shm"):
            Path(f"{state['db_path']}{suffix}").unlink(missing_ok=True)
        store = SqliteConsultStore(state["db_path"])
        for row in messages:                              # миграция истории
            store.add_message(
                row["employee_id"], row["conversation_id"], row["role"],
                row["content"], row["citations"], row["prompt_tokens"],
                row["completion_tokens"], row["created_at"],
            )
        service = ConsultService(store, state["embed"], state["llm"])
        service.reload_index()
        state["service"] = service
        state["status"].update(busy=False, finished_at=datetime.now().isoformat(timespec="seconds"), error=None)
    except Exception as exc:
        state["status"].update(busy=False, error=str(exc))
        # старый сервис/файл остаются работать — сборка шла во временный файл


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


@router.get("/orders/conversation")
def conversation(request: Request):
    """Сообщения беседы сотрудника (F3): восстановление диалога при открытии страницы.

    Возвращает {"messages": [...]} — role/content/citations/created_at по
    возрастанию времени; пустой список, если conversation_id не задан или
    беседа принадлежит другому сотруднику.
    """
    user = docapp.web.app.current_user(request)
    if user is None:
        return JSONResponse({"error": "Требуется авторизация"}, status_code=401)
    conv_id = request.query_params.get("conversation_id") or ""
    if not conv_id:
        return {"messages": []}
    service = _service(request)
    return {"messages": service.conversation(user.id, conv_id)}


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


@router.get("/orders/documents/{document_id}", response_class=HTMLResponse)
def document_page(request: Request, document_id: int):
    """Страница полного текста приказа (F10): «Читать» из списка документов.

    Доступна всем ролям. Если документа с таким id нет — 404.
    """
    user = docapp.web.app.current_user(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    service = _service(request)
    meta = next((d for d in service.documents() if d["id"] == document_id), None)
    if meta is None:
        return HTMLResponse("Документ не найден", status_code=404)
    text = service.document_text(document_id) or ""
    return TEMPLATES.TemplateResponse(
        request,
        "document.html",
        {"user": user, "flash": None, "doc": {**meta, "text": text}},
    )


@router.get("/orders/documents/{document_id}/download")
def document_download(request: Request, document_id: int):
    """Скачивание приказа (F10): оригинальный файл из source, иначе .txt из full_text.

    Имя файла — только basename, в Content-Disposition в кавычках. Если
    оригинала нет — отдаётся текстовый файл; если нет и текста — 404.
    """
    user = docapp.web.app.current_user(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    service = _service(request)
    src = service.document_source(document_id)
    if src is not None:
        headers = {"Content-Disposition": _content_disposition(src["source_name"])}
        return Response(
            content=src["source"],
            media_type=_media_type(src["source_name"]),
            headers=headers,
        )
    text = service.document_text(document_id)
    if text:
        headers = {"Content-Disposition": _content_disposition(f"приказ-{document_id}.txt")}
        return Response(
            content=text,
            media_type="text/plain; charset=utf-8",
            headers=headers,
        )
    return HTMLResponse("Документ не найден", status_code=404)


@router.post("/orders/documents/upload")
async def upload_documents(request: Request, files: list[UploadFile] = File(...)):
    """Веб-загрузка приказов с авто-переиндексацией (F11). Только заведующий.

    Сохраняет .docx/.pdf в статичную папку приказов (CONSULT_DOCS_DIR)
    и запускает фоновую пересборку индекса (эмбеддинги через RouterAI).
    Лимит размера файла — 50 МБ (MAX_UPLOAD_BYTES), больше — 400.
    Пока идёт пересборка, статус — GET /orders/status; повторный запуск
    до завершения — 409. Возвращает {"ok": True, "busy": True, "saved": N}.
    """
    user = docapp.web.app.current_user(request)
    if user is None:
        return JSONResponse({"error": "Требуется авторизация"}, status_code=401)
    if user.role != HEAD:
        return JSONResponse({"error": "Только заведующий"}, status_code=403)
    state = request.app.state.consult
    config = state["config"]
    with state["lock"]:
        if state["status"]["busy"]:
            return JSONResponse({"error": "Индексация уже идёт"}, status_code=409)
        # Сначала валидация всех файлов (имя — только basename, расширение
        # .docx/.pdf), затем запись — при ошибке ничего не сохраняется.
        payloads: list[tuple[str, bytes]] = []
        for f in files:
            name = Path(f.filename or "").name
            if Path(name).suffix.lower() not in {".docx", ".pdf"}:
                return JSONResponse({"error": "Только .docx и .pdf"}, status_code=400)
            data = await f.read(MAX_UPLOAD_BYTES + 1)
            if len(data) > MAX_UPLOAD_BYTES:
                return JSONResponse(
                    {"error": "Файл слишком большой (максимум 50 МБ)"}, status_code=400
                )
            payloads.append((name, data))
        docs_dir = Path(config.docs_dir)
        docs_dir.mkdir(parents=True, exist_ok=True)
        for name, data in payloads:
            (docs_dir / name).write_bytes(data)
        state["status"] = {
            "busy": True,
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "finished_at": None,
            "error": None,
        }
    threading.Thread(target=_run_rebuild, args=(state,), daemon=True).start()
    return {"ok": True, "busy": True, "saved": len(payloads)}


@router.get("/orders/status")
def status(request: Request):
    """Статус фоновой пересборки индекса (F11): всем ролям.

    Возвращает busy/started_at/finished_at/error из state плюс статистику
    индекса (documents/chunks) из сервиса.
    """
    user = docapp.web.app.current_user(request)
    if user is None:
        return JSONResponse({"error": "Требуется авторизация"}, status_code=401)
    state = request.app.state.consult
    return {**state["status"], **state["service"].index_stats()}


@router.post("/orders/reindex")
def reindex(request: Request):
    """Пересобрать индекс из статичной папки приказов (F7/F11). Только заведующий.

    Запускает фоновую пересборку (thread + _run_rebuild): сборка во
    временный файл, атомарная замена, миграция истории диалогов.
    Пока идёт пересборка — 409; статус — GET /orders/status.
    """
    user = docapp.web.app.current_user(request)
    if user is None:
        return JSONResponse({"error": "Требуется авторизация"}, status_code=401)
    if user.role != HEAD:
        return JSONResponse({"error": "Только заведующий"}, status_code=403)
    state = request.app.state.consult
    with state["lock"]:
        if state["status"]["busy"]:
            return JSONResponse({"error": "Индексация уже идёт"}, status_code=409)
        state["status"] = {
            "busy": True,
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "finished_at": None,
            "error": None,
        }
    threading.Thread(target=_run_rebuild, args=(state,), daemon=True).start()
    return {"ok": True, "busy": True}


@router.get("/orders/questions")
def questions(request: Request):
    """Аналитика для заведующего (F4): вопросы сотрудников и расход токенов.

    Возвращает {"questions": [...]} — последние вопросы с датами и
    {"totals": [...]} — сумму prompt/completion токенов по сотрудникам.
    Только заведующий (HEAD).
    """
    user = docapp.web.app.current_user(request)
    if user is None:
        return JSONResponse({"error": "Требуется авторизация"}, status_code=401)
    if user.role != HEAD:
        return JSONResponse({"error": "Только заведующий"}, status_code=403)
    service = _service(request)
    return {
        "questions": service.recent_questions(limit=100),
        "totals": service.token_totals(),
    }
