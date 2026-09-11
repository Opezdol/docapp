"""Реестр модулей: контракт модуля и сборка приложения (ADR-0017).

Модуль объявляет о себе ровно то, что нужно приложению:

    MODULE = Module(
        name="needs",              # имя модуля и ключ в schema_migrations
        schema=SCHEMA,             # свои таблицы (None — своих таблиц нет)
        db_path=lambda: load_needs_config().db_path,
        build=build,               # () -> NeedsContainer
        router=needs_router,       # HTTP-адаптер (None — модуля нет в HTTP)
    )

`create_app(..., modules=MODULES)` идёт по реестру: строит контейнеры, кладёт их в
`app.state.containers` и подключает роутеры. Добавить раздел — это один файл модуля
плюс строка в `docapp/modules.py`.

Роутер достаёт контейнер через `container_of(request, "needs", NeedsContainer)` —
типизированно и с понятной ошибкой, если модуль не подключён (раньше был словарь
`app.state.needs["service"]`, где опечатка в ключе всплыла бы только на вызове).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence, TypeVar

from fastapi import APIRouter, FastAPI
from starlette.requests import Request

from docapp.core.db import Schema

T = TypeVar("T")


@dataclass(frozen=True)
class Module:
    """Контракт модуля: имя, владение схемой, сборка контейнера, HTTP-адаптер.

    build и router могут быть None: модуль-владелец только данных (ядро приложения)
    объявляет одну схему и ничем не подключается к HTTP.
    """

    name: str
    schema: Schema | None = None
    db_path: Callable[[], Path] | None = None
    build: Callable[[], object] | None = None
    router: APIRouter | None = None


def build_containers(app: FastAPI, modules: Sequence[Module]) -> dict[str, object]:
    """Собрать контейнеры модулей и положить их в состояние приложения."""
    containers: dict[str, object] = {}
    for module in modules:
        if module.build is None:
            continue
        containers[module.name] = module.build()
    app.state.containers = containers
    return containers


def include_routers(app: FastAPI, modules: Sequence[Module]) -> None:
    """Подключить HTTP-адаптеры модулей."""
    for module in modules:
        if module.router is not None:
            app.include_router(module.router)


def container_of(request: Request, name: str, expected: type[T]) -> T:
    """Контейнер модуля из состояния приложения (типизированно).

    Модуль не подключён к приложению — это ошибка сборки, а не пользователя,
    поэтому RuntimeError, а не HTTP-ответ.
    """
    containers: dict[str, object] = getattr(request.app.state, "containers", {})
    value = containers.get(name)
    if value is None:
        raise RuntimeError(f"Модуль «{name}» не подключён к приложению")
    if not isinstance(value, expected):
        raise RuntimeError(
            f"Контейнер модуля «{name}»: ожидался {expected.__name__}, "
            f"получен {type(value).__name__}"
        )
    return value


def databases(modules: Sequence[Module]) -> list[tuple[str, Path, Schema]]:
    """Реестр баз: (имя модуля, файл, схема) — для `docapp migrate` и бэкапа."""
    result: list[tuple[str, Path, Schema]] = []
    for module in modules:
        if module.schema is None or module.db_path is None:
            continue
        result.append((module.name, Path(module.db_path()), module.schema))
    return result
