"""Маршруты подприложения «Компендиум»: страница, JSON/SSE-API, источники, статьи.

Маршруты под префиксом /compendium. Авторизация — core.access (`current_user`,
`require`, `ensure`): модуль не импортирует docapp.web.app, кругового импорта нет.
Роли: медсёстрам доступ закрыт (403); врачи/старшая сестра/заведующий задают
вопросы; head/editor курируют (источники, статьи, публикация); настройки —
только head.
"""

from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.templating import Jinja2Templates

from docapp.config import git_revision
from docapp.core import access
from docapp.wiki.markdown import render_html
from docapp.wiki.service import WikiForbidden, WikiService

compendium_templates_dir = Path(__file__).parent / "templates"
#: Общие шаблоны приложения (base.html) — рядом с модулем web, без импорта app.
web_templates_dir = Path(__file__).resolve().parent.parent / "web" / "templates"
TEMPLATES = Jinja2Templates(
    directory=[compendium_templates_dir, web_templates_dir],
    context_processors=[lambda request: {"git_revision": git_revision()}],
)

router = APIRouter(prefix="/compendium")

MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 МБ


def _service(request: Request) -> WikiService:
    return request.app.state.compendium["service"]


def _api_user(request: Request):
    """Пользователь для JSON-API: 401 без сессии (права — отдельными проверками)."""
    return access.api_user(request)


def _require_not_nurse(user) -> None:
    """«Компендиум» доступен всем, кроме медсестёр (ADR-10, ADR-11)."""
    access.ensure(user, access.WIKI_READ, message="Медсёстрам доступ закрыт")


def _require_curator(user) -> None:
    """Источники, статьи и публикация — заведующий и редактор."""
    access.ensure(user, access.WIKI_CURATE, message="Доступно заведующему или редактору")


def _require_head(user) -> None:
    """Настройки консультанта — только заведующий."""
    access.ensure(user, access.WIKI_SETTINGS, message="Только заведующий")


def _content_disposition(filename: str) -> str:
    name = Path(filename).name
    try:
        name.encode("latin-1")
    except UnicodeEncodeError:
        return f"attachment; filename*=UTF-8''{quote(name)}"
    return f'attachment; filename="{name}"'


# ── страница ──────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
def compendium_page(request: Request):
    user = access.current_user(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    _require_not_nurse(user)
    state = request.app.state.compendium
    return TEMPLATES.TemplateResponse(
        request,
        "compendium.html",
        {
            "user": user,
            "flash": None,
            "stats": _service(request).stats(),
            "is_curator": access.has(user.role, access.WIKI_CURATE),
            "is_head": access.has(user.role, access.WIKI_SETTINGS),
        },
    )


# ── вопрос-ответ ──────────────────────────────────────────────────────

@router.post("/ask")
async def ask_question(request: Request):
    """Ответ консультанта (SSE): delta-токены и done с цитатами."""
    user = _api_user(request)
    _require_not_nurse(user)
    body = await request.json()
    question = str(body.get("question") or "")
    if not question.strip():
        return JSONResponse({"error": "Пустой вопрос"}, status_code=400)
    conversation_id = body.get("conversation_id") or uuid.uuid4().hex
    service = _service(request)

    async def gen():
        try:
            async for evt in service.ask(user.id, conversation_id, question, role=user.role):
                if evt["type"] == "done":
                    evt["conversation_id"] = conversation_id
                yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
        except RuntimeError as exc:
            yield f"data: {json.dumps({'type': 'delta', 'text': f'Ошибка сервиса: {exc}'}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'conversation_id': conversation_id}, ensure_ascii=False)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.get("/conversation")
def conversation(request: Request):
    user = _api_user(request)
    _require_not_nurse(user)
    conv_id = request.query_params.get("conversation_id") or ""
    if not conv_id:
        return {"messages": []}
    return {"messages": _service(request).conversation(user.id, conv_id)}


# ── источники ─────────────────────────────────────────────────────────

@router.get("/sources")
def sources_list(request: Request):
    user = _api_user(request)
    _require_not_nurse(user)
    return {"sources": _service(request).sources()}


@router.get("/sources/{source_id}/download")
def source_download(request: Request, source_id: int):
    user = access.current_user(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    _require_not_nurse(user)
    src = _service(request).source_file(source_id)
    if src is None:
        return HTMLResponse("Источник не найден", status_code=404)
    headers = {"Content-Disposition": _content_disposition(src["name"])}
    return Response(content=src["data"], media_type="application/pdf", headers=headers)


@router.get("/sources/{source_id}/text", response_class=HTMLResponse)
def source_text_page(request: Request, source_id: int):
    user = access.current_user(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    _require_not_nurse(user)
    service = _service(request)
    meta = service.source(source_id)
    if meta is None:
        return HTMLResponse("Источник не найден", status_code=404)
    text = service.source_text(source_id) or ""
    return TEMPLATES.TemplateResponse(
        request,
        "source.html",
        {"user": user, "flash": None, "src": {**meta, "text": text}},
    )


@router.post("/sources/upload")
async def upload_sources(request: Request, files: list[UploadFile] = File(...)):
    """Загрузка PDF-источников (head/editor) с фоновым OCR."""
    user = _api_user(request)
    _require_curator(user)
    payloads: list[tuple[str, bytes]] = []
    for f in files:
        name = Path(f.filename or "").name
        if Path(name).suffix.lower() != ".pdf":
            return JSONResponse({"error": "Только .pdf"}, status_code=400)
        data = await f.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            return JSONResponse({"error": "Файл слишком большой (максимум 50 МБ)"}, status_code=400)
        payloads.append((name, data))

    service = _service(request)
    state = request.app.state.compendium
    saved = []
    for name, data in payloads:
        src = service.add_source(user.id, user.role, name, data)
        saved.append(src)
        threading.Thread(target=service.run_ocr, args=(src["id"],), daemon=True).start()
    return {"ok": True, "saved": len(saved), "sources": saved}


@router.delete("/sources/{source_id}")
def source_delete(request: Request, source_id: int):
    user = _api_user(request)
    _require_curator(user)
    try:
        _service(request).delete_source(source_id, user.role)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    return {"ok": True}


# ── статьи ────────────────────────────────────────────────────────────

@router.get("/articles")
def articles_list(request: Request):
    user = _api_user(request)
    _require_not_nurse(user)
    return {"articles": _service(request).articles()}


@router.get("/articles/{article_id}", response_class=HTMLResponse)
def article_page(request: Request, article_id: int):
    """Страница статьи: опубликованную видят все, черновик — только кураторы.

    Кураторы получают кнопки «Править»/«Опубликовать»/«Удалить» и режим
    редактирования прямо на странице. Врачи/старшая сестра — только чтение.
    """
    user = access.current_user(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    _require_not_nurse(user)
    service = _service(request)
    is_curator = access.has(user.role, access.WIKI_CURATE)
    article = service.article(article_id) if is_curator else service.article_public(article_id)
    if article is None:
        return HTMLResponse("Статья не найдена", status_code=404)
    return TEMPLATES.TemplateResponse(
        request,
        "article.html",
        {
            "user": user,
            "flash": None,
            "article": article,
            "body_html": render_html(article["body_md"]),
            "is_curator": is_curator,
        },
    )


@router.post("/articles")
async def article_save(request: Request):
    """Создать/сохранить статью (head/editor): новая ревизия (draft)."""
    user = _api_user(request)
    _require_curator(user)
    body = await request.json()
    article_id = body.get("article_id") or None
    try:
        article = _service(request).save_article(
            user.id, user.role, article_id,
            str(body.get("body_md") or ""),
            str(body.get("change_note") or ""),
            body.get("source_links"),
        )
    except WikiForbidden as exc:
        return JSONResponse({"error": str(exc)}, status_code=403)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return {"article": article}


@router.post("/articles/{article_id}/edit")
async def article_edit(request: Request, article_id: int):
    """Сохранить правку статьи со страницы просмотра (head/editor), редирект назад.

    Форма присылает body_md (multipart/form), создаётся новая ревизия (draft).
    После сохранения — редирект на страницу статьи (303), ошибки — на ту же
    страницу с flash-сообщением.
    """
    from fastapi import Form

    user = access.current_user(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    _require_curator(user)
    form = await request.form()
    body_md = str(form.get("body_md") or "")
    change_note = str(form.get("change_note") or "")
    try:
        _service(request).save_article(user.id, user.role, article_id, body_md, change_note)
    except ValueError as exc:
        request.session["flash"] = str(exc)
    return RedirectResponse(f"/compendium/articles/{article_id}", status_code=303)


@router.post("/articles/{article_id}/publish")
def article_publish(request: Request, article_id: int):
    user = _api_user(request)
    _require_curator(user)
    try:
        article = _service(request).publish(user.id, user.role, article_id)
    except WikiForbidden as exc:
        return JSONResponse({"error": str(exc)}, status_code=403)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return {"article": article}


@router.post("/articles/{article_id}/unpublish")
def article_unpublish(request: Request, article_id: int):
    user = _api_user(request)
    _require_curator(user)
    try:
        article = _service(request).unpublish(user.id, user.role, article_id)
    except WikiForbidden as exc:
        return JSONResponse({"error": str(exc)}, status_code=403)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return {"article": article}


@router.delete("/articles/{article_id}")
def article_delete(request: Request, article_id: int):
    user = _api_user(request)
    _require_curator(user)
    try:
        _service(request).delete_article(user.id, user.role, article_id)
    except WikiForbidden as exc:
        return JSONResponse({"error": str(exc)}, status_code=403)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    return {"ok": True}


@router.get("/articles/{article_id}/revisions")
def article_revisions(request: Request, article_id: int):
    user = _api_user(request)
    _require_curator(user)
    return {"revisions": _service(request).revisions(article_id)}


# ── настройки ─────────────────────────────────────────────────────────

@router.get("/settings")
def settings_get(request: Request):
    user = _api_user(request)
    _require_head(user)
    return _service(request).settings()


@router.post("/settings")
async def settings_update(request: Request):
    user = _api_user(request)
    _require_head(user)
    body = await request.json()
    return _service(request).update_settings(body)


# ── аналитика ─────────────────────────────────────────────────────────

@router.get("/questions")
def questions(request: Request):
    user = _api_user(request)
    _require_head(user)
    params = request.query_params
    from_date = params.get("from") or None
    to_date = params.get("to") or None
    service = _service(request)
    employees = request.app.state.employees
    questions_rows = service.recent_questions(limit=200, from_date=from_date, to_date=to_date)
    for q in questions_rows:
        emp = employees.get_by_id(q["employee_id"])
        q["employee_name"] = emp.full_name if emp else None
    return {
        "questions": questions_rows,
        "totals": service.token_totals(from_date, to_date),
        "by_day": service.token_totals_by_day(from_date, to_date),
    }
