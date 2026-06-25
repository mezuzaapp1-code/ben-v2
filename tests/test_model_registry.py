"""Frontier model registry allowlist and API resolution."""
from __future__ import annotations

import pytest

from services.providers.model_registry import (
    allowed_models,
    assert_model_registered,
    is_registered_model,
    resolve_api_model,
    token_rates,
)
from services.providers.openai_provider import OPENAI_CHAT_FAST_MODEL, OPENAI_REASONING_MODEL


def test_frontier_openai_models_registered():
    assert is_registered_model("openai", OPENAI_CHAT_FAST_MODEL)
    assert is_registered_model("openai", OPENAI_REASONING_MODEL)
    assert is_registered_model("openai", "gpt-4o-mini")


def test_assert_model_registered_passes_frontier():
    assert assert_model_registered("openai", OPENAI_CHAT_FAST_MODEL) == OPENAI_CHAT_FAST_MODEL


def test_assert_model_registered_rejects_unknown():
    with pytest.raises(ValueError, match="not registered"):
        assert_model_registered("openai", "unknown-model-xyz")


def test_token_rates_for_frontier_chat():
    inp, out = token_rates("openai", OPENAI_CHAT_FAST_MODEL)
    assert inp > 0
    assert out > 0


def test_resolve_api_model_env_override(monkeypatch):
    monkeypatch.setenv("OPENAI_CHAT_MODEL", "gpt-4o-mini")
    assert resolve_api_model("openai", OPENAI_CHAT_FAST_MODEL) == "gpt-4o-mini"


def test_resolve_api_model_default_fallback(monkeypatch):
    monkeypatch.delenv("OPENAI_CHAT_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    assert resolve_api_model("openai", OPENAI_CHAT_FAST_MODEL) == "gpt-4o-mini"
    assert resolve_api_model("openai", OPENAI_REASONING_MODEL) == "gpt-4o"


def test_allowed_models_includes_all_providers():
    providers = {p for p, _ in allowed_models()}
    assert "openai" in providers
    assert "anthropic" in providers
    assert "google" in providers
