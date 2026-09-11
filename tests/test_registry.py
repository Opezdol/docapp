"""Тесты реестра модулей (core/registry.py) — критерий шага 3.

Главное здесь: приложение собирается из реестра, а с подмножеством модулей —
без правок кода. Это то, что делает пятый-шестой модуль дешёвым.
"""

from dataclasses import dataclass, replace
from pathlib import Path

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from docapp.core.db import Schema
from docapp.core.registry import (
    AppContext,
    Module,
    build_containers,
    container_of,
    databases,
    include_routers,
)
from docapp.duty.module import MODULE as DUTY
from docapp.modules import MODULES
from docapp.needs.module import MODULE as NEEDS
from docapp.wiki.module import MODULE as WIKI


@dataclass(frozen=True)
class _Container:
    value: str = "готово"


def _router(path: str) -> APIRouter:
    router = APIRouter()

    @router.get(path)
    def endpoint():
        return {"ok": True}

    return router


class TestModuleContract:
    """Контракт модуля: имя, схема, база, сборка, роутер."""

    def test_app_modules_are_declared(self):
        """Порядок значим: модуль берёт интерфейс соседа, объявленного раньше."""
        names = [module.name for module in MODULES]
        assert names == ["people", "records", "wiki", "needs", "duty", "distribution"]

    def test_every_module_with_schema_has_version(self):
        """Схема модуля — объект Schema с версией (файл у всех один, ADR-0016)."""
        for module in MODULES:
            if module.schema is not None:
                assert isinstance(module.schema, Schema)
                assert module.schema.version >= 1

    def test_no_module_without_build(self):
        """Каждый модуль собирает контейнер; схемы без сборки больше нет."""
        for module in MODULES:
            assert module.build is not None, module.name

    def test_distribution_has_no_schema(self):
        """«Распределение» своей таблицы не имеет — оно и есть проверка каркаса."""
        distribution = next(module for module in MODULES if module.name == "distribution")
        assert distribution.schema is None
        assert distribution.router is not None

    def test_app_modules_have_container_and_router(self):
        """У модуля есть сборка; HTTP-адаптер — у всех, кроме справочника."""
        for module in MODULES:
            assert module.build is not None, module.name
            if module.name == "people":
                # Сотрудников заводят командой CLI, страницы у модуля нет.
                assert module.router is None
            else:
                assert module.router is not None, module.name

    def test_module_schema_versions(self):
        """Версии схем модулей в единой БД (ADR-0016)."""
        versions = {module.name: module.schema.version for module in MODULES if module.schema}
        assert versions == {"people": 1, "records": 3, "wiki": 2, "needs": 2, "duty": 1}

    def test_databases_covers_all_schemas(self):
        """Все схемы объявлены в одном файле — путь у записей одинаковый."""
        path = Path("/tmp/app.db")
        rows = databases(MODULES, path)
        assert [name for name, _, _ in rows] == ["people", "records", "wiki", "needs", "duty"]
        for _, row_path, schema in rows:
            assert row_path == path
            assert isinstance(schema, Schema)


class TestBuildContainers:
    """Сборка контейнеров: модули без build пропускаются, ошибок нет."""

    def test_builds_only_modules_with_build(self):
        from fastapi import FastAPI

        app = FastAPI()
        skipped = Module(name="only-schema", schema=Schema(module="x", sql="", version=1))
        built = Module(name="with-build", build=lambda context: _Container())
        context = AppContext(db_path=Path("/tmp/app.db"))
        containers = build_containers(app, (skipped, built), context)
        assert set(containers) == {"with-build"}
        assert app.state.containers == containers

    def test_includes_only_modules_with_router(self):
        from fastapi import FastAPI

        app = FastAPI()
        include_routers(
            app,
            (
                Module(name="no-router"),
                Module(name="with-router", router=_router("/probe")),
            ),
        )
        with TestClient(app) as client:
            assert client.get("/probe").status_code == 200


class TestContainerOf:
    """Доступ роутера к контейнеру: типизированно и с понятной ошибкой."""

    def test_returns_container(self):
        from fastapi import FastAPI

        app = FastAPI()
        app.state.containers = {"x": _Container(value="моё")}
        request = _fake_request(app)
        assert container_of(request, "x", _Container).value == "моё"

    def test_missing_module_raises(self):
        from fastapi import FastAPI

        app = FastAPI()
        app.state.containers = {}
        with pytest.raises(RuntimeError, match="не подключён"):
            container_of(_fake_request(app), "needs", _Container)

    def test_wrong_type_raises(self):
        from fastapi import FastAPI

        app = FastAPI()
        app.state.containers = {"x": "не контейнер"}
        with pytest.raises(RuntimeError, match="ожидался"):
            container_of(_fake_request(app), "x", _Container)


def _fake_request(app):
    from starlette.requests import Request

    return Request({"type": "http", "app": app})


class TestSubsetOfModules:
    """Критерий шага 3: приложение собирается с одним модулем из реестра.

    Не нужно патчить переменные окружения трёх модулей, чтобы проверить
    четвёртый: остальные просто не подключены.
    """

    def test_app_with_only_needs(self, tmp_path, monkeypatch):
        from docapp.web.app import create_app

        monkeypatch.setenv("NEEDS_CATALOG", str(tmp_path / "catalog.yaml"))
        (tmp_path / "catalog.yaml").write_text(
            "bases:\n  Ленская: [травма]\ngroups:\n  Растворы:\n    Рингер: фл\n",
            encoding="utf-8",
        )

        app = create_app(db_path=tmp_path / "web.db", secret="test", modules=(NEEDS,))
        assert set(app.state.containers) == {"needs"}

        with TestClient(app, follow_redirects=False) as client:
            # страница модуля жива (303 → /login — сессии нет)
            assert client.get("/needs").status_code == 303
            # остальные модули не подключены вовсе
            assert client.get("/duty").status_code == 404
            assert client.get("/compendium").status_code == 404

    def test_app_with_two_modules(self, tmp_path, monkeypatch):
        from docapp.web.app import create_app

        monkeypatch.setenv("NEEDS_CATALOG", str(tmp_path / "catalog.yaml"))
        (tmp_path / "catalog.yaml").write_text(
            "bases:\n  Ленская: [травма]\ngroups:\n  Растворы:\n    Рингер: фл\n",
            encoding="utf-8",
        )

        app = create_app(
            db_path=tmp_path / "web.db", secret="test", modules=(NEEDS, DUTY)
        )
        assert set(app.state.containers) == {"needs", "duty"}

        with TestClient(app, follow_redirects=False) as client:
            assert client.get("/needs").status_code == 303
            assert client.get("/duty").status_code == 303
            assert client.get("/compendium").status_code == 404


class TestContainerIsolation:
    """Контейнер модуля неизменяем: подмена в тесте — через replace."""

    def test_container_is_frozen(self):
        from dataclasses import FrozenInstanceError

        from fastapi import FastAPI

        app = FastAPI()
        app.state.containers = {"x": _Container(value="реальное")}
        container = container_of(_fake_request(app), "x", _Container)
        with pytest.raises(FrozenInstanceError):
            container.value = "фейк"  # type: ignore[misc]

    def test_swapped_container_is_visible(self):
        from fastapi import FastAPI

        app = FastAPI()
        app.state.containers = {"x": _Container(value="реальное")}
        container = container_of(_fake_request(app), "x", _Container)
        app.state.containers["x"] = replace(container, value="фейк")
        assert container_of(_fake_request(app), "x", _Container).value == "фейк"
