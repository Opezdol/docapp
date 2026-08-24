"""Тесты конфигурации консультанта: дефолты моделей RouterAI и env-переопределения.

RouterAI принимает только полные идентификаторы «провайдер/модель»
(openai/gpt-4o-mini, openai/text-embedding-3-small); голые имена без
префикса провайдера дают 400 «Model ... not found» (регрессия: T-консультанта).
"""

import pytest

from docapp.consult.config import ConsultConfig, load_consult_config


def test_default_models_use_routerai_namespaced_ids():
    """Дефолтные модели — с префиксом провайдера, как требует RouterAI."""
    config = ConsultConfig()
    assert config.llm_model == "openai/gpt-4o-mini"
    assert config.embed_model == "openai/text-embedding-3-small"


def test_load_consult_config_respects_env_overrides(monkeypatch, tmp_path):
    """CONSULT_* из окружения переопределяют дефолты, каталоги создаются."""
    monkeypatch.setenv("CONSULT_API_KEY", "key")
    monkeypatch.setenv("CONSULT_LLM_MODEL", "deepseek/deepseek-chat")
    monkeypatch.setenv("CONSULT_EMBED_MODEL", "intfloat/multilingual-e5-large")
    monkeypatch.setenv("CONSULT_INDEX_DIR", str(tmp_path / "idx"))
    monkeypatch.setenv("CONSULT_DOCS_DIR", str(tmp_path / "docs"))

    config = load_consult_config()

    assert config.api_key == "key"
    assert config.llm_model == "deepseek/deepseek-chat"
    assert config.embed_model == "intfloat/multilingual-e5-large"
    assert config.index_dir == tmp_path / "idx"
    assert config.docs_dir == tmp_path / "docs"
    assert (tmp_path / "idx").is_dir()
    assert (tmp_path / "docs").is_dir()


def test_load_consult_config_defaults_without_env(monkeypatch):
    """Без CONSULT_* — дефолты с полными идентификаторами моделей."""
    monkeypatch.delenv("CONSULT_LLM_MODEL", raising=False)
    monkeypatch.delenv("CONSULT_EMBED_MODEL", raising=False)
    config = load_consult_config()
    assert config.llm_model == "openai/gpt-4o-mini"
    assert config.embed_model == "openai/text-embedding-3-small"
