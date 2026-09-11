"""Реестр модулей: контракт модуля и сборка приложения (ADR-0017).

Модуль объявляет о себе ровно то, что нужно приложению:

    MODULE = Module(
        name="needs",          # имя модуля и ключ в schema_migrations
        schema=SCHEMA,         # свои таблицы (None — своих таблиц нет)
        build=build,           # AppContext -> NeedsContainer
        router=needs_router,   # HTTP-адаптер (None — модуля нет в HTTP)
    )

`create_app(..., modules=MODULES)` идёт по реестру: строит контейнеры, кладёт их в
`app.state.containers` и подключает роутеры. Добавить раздел — это один файл модуля
плюс строка в `docapp/modules.py`.

Роутер достаёт контейнер через `container_of(request, "needs", NeedsContainer)` —
типизированно и с понятной ошибкой, если модуль не подключён (раньше был словарь
`app.state.needs["service"]`, где опечатка в ключе всплыла бы только на вызове).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, Sequence, TypeVar

from fastapi import APIRouter, FastAPI
from starlette.requests import Request

from docapp.core.db import Schema

T = TypeVar("T")


@dataclass(frozen=True)
class AppContext:
    """Что приложение передаёт модулям при сборке.

    `db_path` — путь к единой БД (ADR-0016): модули больше не знают, где лежит
    «их» файл, — файл один на всех.

    `containers` — уже собранные контейнеры модулей. Через него модуль берёт
    **интерфейс** соседа, объявленного в реестре раньше («Распределение» читает записи
    анестезий у модуля `records`). Своя таблица соседа при этом недоступна —
    именно этого требует ADR-0017.
    """

    db_path: Path
    containers: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class Module:
    """Контракт модуля: имя, владение схемой, сборка контейнера, HTTP-адаптер.

    build и router могут быть None: модуль-владелец только данных (ядро приложения)
    объявляет одну схему и ничем не подключается к HTTP.
    """

    name: str
    schema: Schema | None = None
    build: Callable[[AppContext], object] | None = None
    router: APIRouter | None = None


def build_containers(
    app: FastAPI, modules: Sequence[Module], context: AppContext
) -> dict[str, object]:
    """Собрать контейнеры модулей и положить их в состояние приложения.

    Контейнеры собираются в порядке реестра и складываются в общий словарь по
    мере готовности: следующий модуль видит интерфейсы уже собранных соседей
    (`context.containers`). Иначе «Распределению» пришлось бы читать чужую таблицу
    напрямую — то, что ADR-0017 запрещает.
    """
    containers: dict[str, object] = {}
    app.state.containers = containers
    context = replace(context, containers=containers)
    for module in modules:
        if module.build is None:
            continue
        containers[module.name] = module.build(context)
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


def databases(modules: Sequence[Module], db_path: Path) -> list[tuple[str, Path, Schema]]:
    """Реестр баз: (имя модуля, файл, схема) — для `docapp migrate` и бэкапа.

    База одна на все модули (ADR-0016), поэтому путь у всех записей один:
    реестр нужен, чтобы пройти по схемам каждого модуля.
    """
    result: list[tuple[str, Path, Schema]] = []
    for module in modules:
        if module.schema is None:
            continue
        result.append((module.name, Path(db_path), module.schema))
    return result
