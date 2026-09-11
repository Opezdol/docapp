"""Vision-клиент: распознавание текста и таблиц со скана (image -> markdown).

Отправляет изображение страницы в мультимодальную модель (OpenAI-совместимый
формат content-частей с data-URL) и просит вернуть текст страницы, включая
таблицы в markdown-нотации. Используется модулем `wiki` («Компендиум») для OCR
сканов PDF, где нет текстового слоя.
"""

from __future__ import annotations

import base64

import httpx

from docapp.ai.config import AIConfig

#: Промпт распознавания страницы: текст дословно + таблицы как markdown.
_OCR_PROMPT = (
    "Распознай текст этой страницы документа (приказа/инструкции) дословно. "
    "Сохрани порядок и нумерацию пунктов. Таблицы оформи в markdown-нотации "
    "(строки с | и разделителем |---|). Не добавляй ничего от себя: только "
    "то, что есть на странице. Отвечай обычным текстом/markdown, без обёртки "
    "в код-блок."
)


class VisionClient:
    """Клиент к мультимодальной модели (RouterAI) для OCR одной страницы.

    image_path — путь к PNG/JPEG страницы; возвращает распознанный текст
    (с таблицами в markdown). transport — точка инъекции для тестов.
    """

    def __init__(
        self, config: AIConfig, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.config = config
        self.transport = transport

    async def ocr_page(self, image_bytes: bytes, mime_type: str = "image/png") -> str:
        """Распознать одну страницу-изображение в markdown-текст.

        mime_type — тип картинки (image/png или image/jpeg). Возвращает
        распознанный текст; при ошибке API поднимает RuntimeError.
        """
        data_url = f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode('ascii')}"
        payload = {
            "model": self.config.vision_model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": _OCR_PROMPT},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
            "temperature": 0.0,
        }
        url = f"{self.config.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        async with httpx.AsyncClient(timeout=180.0, transport=self.transport) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code >= 400:
                body = resp.text[:200]
                raise RuntimeError(
                    f"Vision API ошибка {resp.status_code}: {body}"
                )
            data = resp.json()
            choices = data.get("choices") or []
            if not choices:
                raise RuntimeError("Vision API вернул пустой ответ")
            content = choices[0].get("message", {}).get("content") or ""
            return content
