"""Модуль подписей для реестра приложения (ADR-0017, ADR-0029).

Своя схема (таблица `signatures`), свой файл на диске (`data/signatures/`), своя
страница `/signatures`. Пункта в меню нет: это не раздел, а действие в личном
кабинете — ссылка стоит на странице «Мои данные».

Подпись — картинка с прозрачным фоном; готовит её сервис модуля, а раздел «Кровь»
берёт готовый файл через контейнер, а не из таблицы.
"""

from __future__ import annotations

from docapp import config
from docapp.core.registry import AppContext, Module
from docapp.signatures.container import SignaturesContainer
from docapp.signatures.router import router
from docapp.signatures.service import SignatureService
from docapp.signatures.store import SCHEMA, SqliteSignatureStore


def build(context: AppContext) -> SignaturesContainer:
    """Собрать контейнер: хранилище на общей БД и папка файлов из настроек.

    Путь к папке модуль читает сам (`DOCAPP_SIGNATURES_DIR` или `data/signatures`) —
    так же, как «Чат» читает папку PDF-источников (ADR-0020). Сама папка
    создаётся при первой загрузке, а не при сборке приложения.
    """
    store = SqliteSignatureStore(context.db_path)
    return SignaturesContainer(
        service=SignatureService(store, config.signatures_dir())
    )


MODULE = Module(name="signatures", schema=SCHEMA, build=build, router=router)
