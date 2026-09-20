"""Astra is a GPT/OpenAI model — exact dispatch, no new speaking provider."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:5432/test")

from services.model_gateway import (  # noqa: E402
    _CHAIN,
    normalize_chat_provider_id,
    reset_circuit_breakers_for_tests,
    resolve_dispatch_model,
    route_request,
    route_request_stream,
    selected_chat_attempt,
    validate_chat_model_override,
)
from services.providers import get_gateway_provider  # noqa: E402
from services.providers.base_provider import ProviderSendResult, ProviderStreamEnd  # noqa: E402
from services.inference.usage_normalize import usage_missing  # noqa: E402
from services.providers.model_registry import (  # noqa: E402
    is_exact_dispatch,
    is_registered_model,
    model_capabilities,
    model_display_name,
    model_identity,
    resolve_api_model,
    token_rates,
)
from services.providers.openai_provider import (  # noqa: E402
    OPENAI_ASTRA_API_MODEL,
    OPENAI_ASTRA_REASONING_EFFORT,
    OPENAI_CHAT_FAST_MODEL,
    openai_chat_payload,
)
from services.tier1_models import tier1_model_for  # noqa: E402

TENANT = "00000000-0000-0000-0000-000000000001"


@pytest.fixture(autouse=True)
def _reset_cb():
    reset_circuit_breakers_for_tests()
    yield
    reset_circuit_breakers_for_tests()


@pytest.fixture(autouse=True)
def _no_db_accounting():
    """Astra tests never persist accounting and never touch a live database."""

    async def fake_persist(record):
        return {"persisted": False, "call_id": record.call_id}

    with patch("services.inference.gateway_meter.record_inference_call", side_effect=fake_persist):
        yield


def test_astra_registered_under_openai_not_new_provider():
    assert is_registered_model("openai", "gpt-6-astra")
    assert normalize_chat_provider_id("gpt") == "gpt"
    with pytest.raises(ValueError, match="claude, gemini, gpt, grok"):
        normalize_chat_provider_id("astra")
    ident = model_identity("openai", "gpt-6-astra")
    assert ident["speaking_provider"] == "gpt"
    assert ident["api_model"] == "gpt-6-astra"
    assert ident["exact_dispatch"] is True
    assert model_display_name("openai", "gpt-6-astra") == "Astra"


def test_gpt_selection_accepts_astra_and_rejects_unknown():
    validate_chat_model_override("gpt", "gpt-6-astra")
    validate_chat_model_override("gpt", "gpt-4o")
    validate_chat_model_override("gpt", "gpt-4o-mini")
    with pytest.raises(ValueError, match="not registered"):
        validate_chat_model_override("gpt", "gpt-not-a-real-model")


def test_astra_dispatch_is_exact_even_with_openai_env_aliases(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("OPENAI_CHAT_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("OPENAI_REASONING_API_MODEL", "gpt-4o")
    monkeypatch.setenv("SYNTHESIS_MODEL", "gpt-4o")
    assert is_exact_dispatch("openai", "gpt-6-astra")
    assert resolve_api_model("openai", "gpt-6-astra") == "gpt-6-astra"
    assert resolve_dispatch_model("openai", "gpt-6-astra") == "gpt-6-astra"
    assert resolve_api_model("openai", OPENAI_CHAT_FAST_MODEL) == "gpt-4o-mini"


def test_existing_gpt_defaults_and_remaps_unchanged(monkeypatch):
    monkeypatch.delenv("OPENAI_CHAT_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_REASONING_API_MODEL", raising=False)
    monkeypatch.delenv("SYNTHESIS_MODEL", raising=False)
    assert tier1_model_for("gpt") == "gpt-4o"
    assert tier1_model_for("claude") == "claude-opus-4.8"
    assert tier1_model_for("gemini") == "gemini-3.8-flash"
    assert tier1_model_for("grok") == "grok-4.6"
    assert resolve_api_model("openai", OPENAI_CHAT_FAST_MODEL) == "gpt-4o-mini"
    assert resolve_api_model("openai", "gpt-4o") == "gpt-4o"
    assert "xai" not in _CHAIN
    assert _CHAIN == ("openai", "anthropic", "google")


def test_astra_pricing_and_capabilities_are_explicit():
    inp, out = token_rates("openai", "gpt-6-astra")
    assert inp == 10e-6
    assert out == 50e-6
    high_inp, high_out = token_rates("openai", "gpt-6-astra", prompt_tokens=272000)
    assert high_inp == 20e-6
    assert high_out == 75e-6
    caps = model_capabilities("openai", "gpt-6-astra")
    assert "vision.analyze" in caps
    assert "tools" not in caps
    assert "function_calling" not in caps


def test_astra_reasoning_effort_does_not_leak_to_other_openai_models():
    astra = openai_chat_payload(
        model=OPENAI_ASTRA_API_MODEL,
        messages=[{"role": "user", "content": "hi"}],
    )
    gpt4o = openai_chat_payload(
        model="gpt-4o",
        messages=[{"role": "user", "content": "hi"}],
    )
    mini = openai_chat_payload(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "hi"}],
        stream=True,
    )
    instant = openai_chat_payload(
        model=OPENAI_CHAT_FAST_MODEL,
        messages=[{"role": "user", "content": "hi"}],
    )
    assert astra["model"] == "gpt-6-astra"
    assert astra["reasoning_effort"] == OPENAI_ASTRA_REASONING_EFFORT == "low"
    assert "temperature" not in astra
    assert "reasoning_effort" not in gpt4o
    assert "reasoning_effort" not in mini
    assert "reasoning_effort" not in instant
    assert mini["stream"] is True
    assert mini["stream_options"] == {"include_usage": True}


def test_project_agent_does_not_select_astra(monkeypatch):
    from services.project_agent_service import _openai_model

    monkeypatch.delenv("OPENAI_CHAT_FAST_MODEL", raising=False)
    assert _openai_model() != "gpt-6-astra"
    assert _openai_model() == OPENAI_CHAT_FAST_MODEL


@pytest.mark.asyncio
async def test_independent_openai_model_overrides_do_not_change_global_default(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-openai")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("OPENAI_CHAT_MODEL", "gpt-4o-mini")
    seen: list[str] = []

    async def fake_stream(cx, *, model, message, tenant_id, system=None):
        seen.append(model)
        yield f"ok-{model}"
        yield ProviderStreamEnd(usage=usage_missing())

    with patch.object(get_gateway_provider("openai"), "stream_message", side_effect=fake_stream):
        async for _chunk, model, prov in route_request_stream(
            "one",
            TENANT,
            "free",
            provider_id="gpt",
            model_override="gpt-4o-mini",
        ):
            if prov:
                assert prov == "openai"
            if model:
                assert model == "gpt-4o-mini"
        async for _chunk, model, prov in route_request_stream(
            "two",
            TENANT,
            "free",
            provider_id="gpt",
            model_override="gpt-6-astra",
        ):
            if prov:
                assert prov == "openai"
            if model:
                assert model == "gpt-6-astra"
        async for _chunk, model, prov in route_request_stream(
            "three",
            TENANT,
            "free",
            provider_id="gpt",
            model_override="gpt-4o-mini",
        ):
            if prov:
                assert prov == "openai"
            if model:
                assert model == "gpt-4o-mini"

    assert seen == ["gpt-4o-mini", "gpt-6-astra", "gpt-4o-mini"]
    assert resolve_api_model("openai", OPENAI_CHAT_FAST_MODEL) == "gpt-4o-mini"
    assert tier1_model_for("gpt") == "gpt-4o"


@pytest.mark.asyncio
async def test_claude_gemini_grok_routing_unchanged(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
    monkeypatch.setenv("GOOGLE_API_KEY", "g-test")
    monkeypatch.setenv("XAI_API_KEY", "xai-test")
    seen: dict[str, str] = {}

    async def fake_openai(cx, *, model, message, tenant_id, system=None):
        seen["openai"] = model
        yield "g"

    async def fake_anthropic(cx, *, model, message, tenant_id, system=None):
        seen["anthropic"] = model
        yield "c"

    async def fake_google(cx, *, model, message, tenant_id, system=None):
        seen["google"] = model
        yield "m"

    async def fake_xai(cx, *, model, message, tenant_id, system=None):
        seen["xai"] = model
        yield "x"

    with (
        patch.object(get_gateway_provider("openai"), "stream_message", side_effect=fake_openai),
        patch.object(get_gateway_provider("anthropic"), "stream_message", side_effect=fake_anthropic),
        patch.object(get_gateway_provider("google"), "stream_message", side_effect=fake_google),
        patch.object(get_gateway_provider("xai"), "stream_message", side_effect=fake_xai),
    ):
        async for _ in route_request_stream(
            "hi", TENANT, "free", provider_id="claude", model_override="claude-opus-4.8"
        ):
            pass
        async for _ in route_request_stream(
            "hi", TENANT, "free", provider_id="gemini", model_override="gemini-3.5-flash"
        ):
            pass
        async for _ in route_request_stream(
            "hi", TENANT, "free", provider_id="grok", model_override="grok-4.6"
        ):
            pass

    assert seen["anthropic"] == resolve_dispatch_model("anthropic", "claude-opus-4.8")
    assert seen["google"] == resolve_dispatch_model("google", "gemini-3.5-flash")
    assert seen["xai"] == resolve_dispatch_model("xai", "grok-4.6")
    assert "openai" not in seen


def test_requested_canonical_api_model_chain_is_exact():
    requested = "gpt-6-astra"
    gateway, canonical = selected_chat_attempt(
        "free", provider_id="gpt", model_override=requested
    )
    assert gateway == "openai"
    assert canonical == "gpt-6-astra"
    api_model = resolve_dispatch_model(gateway, canonical)
    assert api_model == "gpt-6-astra"
    assert get_gateway_provider(gateway).provider_name == "openai"


@pytest.mark.asyncio
async def test_gpt_astra_selection_reaches_openai_provider(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-openai")
    seen: list[tuple[str, str]] = []

    async def fake_send(self, cx, *, model, message, tenant_id, system=None, user_content=None):
        seen.append((self.provider_name, model))
        return ProviderSendResult.from_token_counts("ok", 1, 1, usage=usage_missing())

    with patch("services.providers.openai_provider.OpenAIProvider.send_message", new=fake_send):
        out = await route_request(
            "hello",
            TENANT,
            "free",
            provider_id="gpt",
            model_override="gpt-6-astra",
        )
    assert out["content"] == "ok"
    assert out["provider_used"] == "openai"
    assert out["model_used"] == "gpt-6-astra"
    assert seen == [("openai", "gpt-6-astra")]


@pytest.mark.asyncio
async def test_openai_provider_http_payload_astra_vs_gpt4o(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-openai")

    class _FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "id": "chatcmpl-test",
                "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }

    class _FakeClient:
        def __init__(self):
            self.bodies: list[dict] = []

        async def post(self, url, *, headers, json):
            assert url == "https://api.openai.com/v1/chat/completions"
            self.bodies.append(json)
            return _FakeResponse()

    adapter = get_gateway_provider("openai")
    cx = _FakeClient()
    await adapter.send_message(cx, model="gpt-6-astra", message="a", tenant_id=TENANT)
    await adapter.send_message(cx, model="gpt-4o", message="a", tenant_id=TENANT)
    astra_body, gpt4o_body = cx.bodies
    assert astra_body["model"] == "gpt-6-astra"
    assert astra_body["reasoning_effort"] == "low"
    assert gpt4o_body["model"] == "gpt-4o"
    assert "reasoning_effort" not in gpt4o_body


@pytest.mark.asyncio
async def test_astra_stream_uses_existing_chat_completions_parser(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-openai")
    captured: dict[str, dict] = {}

    class _FakeStreamResponse:
        def raise_for_status(self):
            return None

        async def aiter_lines(self):
            yield 'data: {"id":"chatcmpl-astra","choices":[{"delta":{"content":"Hel"}}]}'
            yield 'data: {"choices":[{"delta":{"content":"lo"},"finish_reason":"stop"}]}'
            yield 'data: {"usage":{"prompt_tokens":3,"completion_tokens":2,"total_tokens":5}}'
            yield "data: [DONE]"

    class _FakeStreamContext:
        async def __aenter__(self):
            return _FakeStreamResponse()

        async def __aexit__(self, *_args):
            return None

    class _FakeAsyncClient:
        def stream(self, method, url, *, headers, json):
            captured["url"] = url
            captured["body"] = json
            return _FakeStreamContext()

    adapter = get_gateway_provider("openai")
    chunks: list = []
    async for item in adapter.stream_message(
        _FakeAsyncClient(),
        model="gpt-6-astra",
        message="hi",
        tenant_id=TENANT,
    ):
        chunks.append(item)

    assert captured["url"] == "https://api.openai.com/v1/chat/completions"
    assert captured["body"]["model"] == "gpt-6-astra"
    assert captured["body"]["reasoning_effort"] == "low"
    assert captured["body"]["stream"] is True
    assert chunks[0] == "Hel"
    assert chunks[1] == "lo"
    assert isinstance(chunks[-1], ProviderStreamEnd)
    assert chunks[-1].usage.usage_status == "exact"
    assert "".join(c for c in chunks if isinstance(c, str)) == "Hello"
