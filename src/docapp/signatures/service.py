"""Сервис подписей: принять снимок, подготовить PNG, положить файл (ADR-0029).

Правил доступа здесь нет — их проверяет роутер по таблице `core/access`.
Правило владения одно и оно тут: подпись принадлежит сотруднику, и `employee_id`
всегда приходит из сессии, никогда из запроса.
"""

from __future__ import annotations

from pathlib import Path

from docapp.signatures import png
from docapp.signatures.errors import SignatureError
from docapp.signatures.store import Signature, SqliteSignatureStore

#: Предел размера загружаемого файла: телефон присылает 200–400 КБ, 4 МБ — с запасом.
MAX_BYTES = 4 * 1024 * 1024


class SignatureService:
    """Подписи сотрудников: приём снимка, подготовка и файл на диске."""

    def __init__(self, store: SqliteSignatureStore, directory: str | Path) -> None:
        self._store = store
        self._dir = Path(directory)

    def signature(self, employee_id: int) -> Signature | None:
        """Загруженная подпись сотрудника или None."""
        return self._store.get(employee_id)

    def image_path(self, employee_id: int) -> Path | None:
        """Путь к файлу подписи; None, если её нет или файл пропал."""
        stored = self._store.get(employee_id)
        if stored is None:
            return None
        path = self._dir / stored.file_name
        return path if path.exists() else None

    def save(self, employee_id: int, data: bytes) -> Signature:
        """Принять снимок подписи, подготовить PNG и записать файл вместе со строкой.

        Файл пишется во временное имя и переименовывается: оборванная загрузка не
        должна оставить полупустой PNG, который потом попадёт в документ.
        """
        if not data:
            raise SignatureError("Пустой файл")
        if len(data) > MAX_BYTES:
            raise SignatureError("Файл больше 4 МБ — сожмите фотографию")

        prepared = png.prepare(data)
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._dir / f"{employee_id}.png"
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_bytes(prepared)
        temporary.replace(path)
        return self._store.save(employee_id, path.name, len(prepared))

    def forget(self, employee_id: int) -> None:
        """Удалить подпись сотрудника вместе с файлом (на случай замены/отказа)."""
        path = self.image_path(employee_id)
        self._store.delete(employee_id)
        if path is not None:
            path.unlink(missing_ok=True)
