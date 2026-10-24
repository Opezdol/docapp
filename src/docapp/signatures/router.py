"""HTTP-адаптер подписей: страница «Моя подпись» и загрузка снимка (ADR-0029).

Правил здесь нет: как готовить PNG — в `png.py`, куда класть файл — в `service.py`,
кому можно — в таблице `core/access`. Роутер только читает запрос и отдаёт ответ.

`employee_id` берётся только из сессии: подпись принадлежит вошедшему, и подставить
в запрос чужой идентификатор нечем.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from docapp.core import access, web
from docapp.core.registry import container_of
from docapp.domain.employee import Employee
from docapp.signatures.container import SignaturesContainer
from docapp.signatures.errors import SignatureError
from docapp.signatures.service import SignatureService

TEMPLATES = web.templates(Path(__file__).parent / "templates")

router = APIRouter(prefix="/signatures")


def _service(request: Request) -> SignatureService:
    """Сервис подписей из контейнера модуля (шов реестра, ADR-0017)."""
    return container_of(request, "signatures", SignaturesContainer).service


def _page_user(request: Request) -> Employee | None:
    """Пользователь страницы: нет сессии — None (уводим на вход), нет прав — 403."""
    user = access.current_user(request)
    if user is not None:
        access.ensure(
            user,
            access.SIGNATURES_OWN,
            message="Подпись доступна врачу и заведующему отделением",
        )
    return user


def _api_user(request: Request) -> Employee:
    """Пользователь API: 401 без сессии, 403 без права на свою подпись."""
    user = access.api_user(request)
    access.ensure(
        user,
        access.SIGNATURES_OWN,
        message="Подпись доступна врачу и заведующему отделением",
    )
    assert user.id is not None  # вошедший сотрудник всегда с id из БД
    return user


@router.get("", response_class=HTMLResponse)
def signature_page(request: Request):
    """Моя подпись: превью загруженной (если есть) и выбор снимка."""
    user = _page_user(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    assert user.id is not None  # id пришёл из базы вместе с сессией
    stored = _service(request).signature(user.id)
    return TEMPLATES.TemplateResponse(
        request,
        "signature.html",
        {
            "user": user,
            "signature": stored,
            "stamp": stored.uploaded_at[:16].replace("T", " ") if stored else "",
            "kilobytes": round(stored.size / 1024) if stored else 0,
        },
    )


@router.post("/api/upload")
async def upload(request: Request, image: Annotated[UploadFile, File()]):
    """Принять снимок подписи: сервер готовит PNG и заменяет прежнюю подпись."""
    user = _api_user(request)
    assert user.id is not None  # id пришёл из базы вместе с сессией
    data = await image.read()
    try:
        signature = _service(request).save(user.id, data)
    except SignatureError as error:
        return JSONResponse({"detail": str(error)}, status_code=400)
    return {
        "ok": True,
        "uploaded_at": signature.uploaded_at,
        "size": signature.size,
    }


@router.get("/api/image")
def own_image(request: Request):
    """Файл своей подписи (PNG с прозрачным фоном) — для превью и для документов."""
    user = _api_user(request)
    assert user.id is not None  # id пришёл из базы вместе с сессией
    path = _service(request).image_path(user.id)
    if path is None:
        return JSONResponse({"detail": "Подписи нет"}, status_code=404)
    return Response(
        content=path.read_bytes(),
        media_type="image/png",
        headers={"Cache-Control": "no-store"},
    )
