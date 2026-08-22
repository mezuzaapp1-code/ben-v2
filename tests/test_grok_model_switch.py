"""Manual Grok model selection — exact API ids, cost, persistence, no routing."""
from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:5432/test")

from services.inference.contracts import InferenceUsage
from services.inference.execution_context import begin_execution_context, set_execution_context
from services.inference.pricing import calculate_cost, resolve_pricing_snapshot
from services.model_gateway import (
    _CHAIN,
    normalize_chat_provider_id,
    reset_circuit_breakers_for_tests,
    route_request_stream,
    validate_chat_model_override,
)
from services.providers import get_gateway_provider
from services.providers.base_provider import ProviderStreamEnd
from services.providers.model_registry import is_registered_model, token_rates
from services.providers.xai_provider import XAI_FAST_MODEL, XAI_FLAGSHIP_MODEL
from services.tier1_models import TIER1_GROK_MODEL, tier1_model_for

TENANT = "00000000-0000-0000-0000-000000000001"


@pytest.fixture(autouse=True)
def _reset_cb():
    reset_circuit_breakers_for_tests()
    begin_execution_context(org_id=TENANT, workspace_id="ws-grok", pipeline="chat")
    yield
    set_execution_context(None)
    reset_circuit_breakers_for_tests()


def test_grok_speaking_id_and_default():
    assert normalize_chat_provider_id(" Grok ") == "grok"
    assert tier1_model_for("grok") == XAI_FLAGSHIP_MODEL == TIER1_GROK_MODEL == "grok-4.6"
    assert is_registered_model("xai", "grok-4.6")
    assert is_registered_model("xai", "grok-4.3")
    assert not is_registered_model("xai", "grok-4.20-0309-reasoning")
    assert not is_registered_model("xai", "grok-4.20-multi-agent-0309")


def test_grok_not_in_implicit_fallback_chain():
    assert "xai" not in _CHAIN
    assert _CHAIN == ("openai", "anthropic", "google")


def test_invalid_grok_model_rejected():
    with pytest.raises(ValueError, match="not registered"):
        validate_chat_model_override("grok", "grok-4.20-multi-agent-0309")
    validate_chat_model_override("grok", "grok-4.6")
    validate_chat_model_override("grok", "grok-4.3")


def test_xai_not_bound_to_engine_grok():
    from services.engine_capability_gate import PROVIDER_ENGINE_CATALOG_KEYS, catalog_key_for_provider

    assert PROVIDER_ENGINE_CATALOG_KEYS.get("gpt") == "engine-grok"
    assert "grok" not in PROVIDER_ENGINE_CATALOG_KEYS
    assert catalog_key_for_provider("grok") is None


@pytest.mark.asyncio
async def test_route_uses_exact_selected_grok_model(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "xai-test-key-not-a-secret")
    seen: list[str] = []

    async def fake_stream(cx, *, model, message, tenant_id, system=None):
        seen.append(model)
        yield "ok"
        yield ProviderStreamEnd(
            usage=InferenceUsage(
                input_tokens=10,
                output_tokens=4,
                total_tokens=14,
                usage_status="exact",
            )
        )

    with patch.object(get_gateway_provider("xai"), "stream_message", side_effect=fake_stream):
        async for _chunk, model, prov in route_request_stream(
            "hello",
            TENANT,
            "free",
            provider_id="grok",
            model_override="grok-4.6",
        ):
            assert prov == "xai"
            if model:
                assert model == "grok-4.6"
        async for _chunk, model, prov in route_request_stream(
            "hello",
            TENANT,
            "free",
            provider_id="grok",
            model_override="grok-4.3",
        ):
            assert prov == "xai"
            if model:
                assert model == "grok-4.3"
        async for _chunk, model, prov in route_request_stream(
            "hello",
            TENANT,
            "free",
            provider_id="grok",
            model_override="grok-4.6",
        ):
            assert prov == "xai"
            if model:
                assert model == "grok-4.6"

    assert seen == ["grok-4.6", "grok-4.3", "grok-4.6"]


@pytest.mark.asyncio
async def test_grok_model_switch_persists_actual_model_same_thread(monkeypatch):
    from services.chat_service import stream_chat_response
    from services.inference.usage_normalize import normalize_openai_usage

    monkeypatch.setenv("XAI_API_KEY", "xai-test-key-not-a-secret")
    tid = uuid.uuid4()
    org = uuid.uuid4()
    persisted: list[dict] = []

    async def fake_stream(cx, *, model, message, tenant_id, system=None):
        yield f"reply-{model}"
        yield ProviderStreamEnd(
            usage=normalize_openai_usage(
                {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25}
            )
        )

    def fake_sqlite(thread_id, *, user_text, assistant_content, provider=None):
        persisted.append(
            {
                "thread_id": thread_id,
                "user_text": user_text,
                "assistant_content": assistant_content,
                "provider": provider,
            }
        )
        return (len(persisted), len(persisted) + 10)

    with (
        patch("services.chat_service.resolve_thread_id", new=AsyncMock(return_value=tid)),
        patch("services.chat_service.is_project_setup_thread", return_value=False),
        patch(
            "services.chat_service.build_chat_message_with_thread_context",
            new=AsyncMock(side_effect=lambda _o, _t, m: m),
        ),
        patch("services.chat_service.inject_knowledge_few_shot", new=AsyncMock(side_effect=lambda _m, p: p)),
        patch("services.chat_service.apply_language_context", side_effect=lambda msg, _lang: msg),
        patch.object(get_gateway_provider("xai"), "stream_message", side_effect=fake_stream),
        patch("services.chat_service.persist_chat_exchange_sqlite", side_effect=fake_sqlite),
        patch("services.chat_service._schedule_chat_persist"),
        patch("services.chat_service.load_ready_files_context", new=AsyncMock(return_value=None)),
        patch("services.chat_service.run_copilot_preamble", new=AsyncMock(return_value=[])),
    ):
        for override in ("grok-4.6", "grok-4.3", "grok-4.6"):
            async for _line in stream_chat_response(
                f"turn-{override}",
                "user-1",
                str(org),
                "free",
                thread_id=tid,
                provider_id="grok",
                model_override=override,
            ):
                pass

    assert [row["thread_id"] for row in persisted] == [str(tid)] * 3
    assert [row["provider"] for row in persisted] == ["grok", "grok", "grok"]
    models = []
    for row in persisted:
        payload = json.loads(row["assistant_content"])
        models.append(payload["model_used"])
        assert payload["provider_id"] == "grok"
        assert payload["cost_usd"] > 0
    assert models == ["grok-4.6", "grok-4.3", "grok-4.6"]
    costs = [json.loads(row["assistant_content"])["cost_usd"] for row in persisted]
    assert costs[0] == costs[2]
    assert costs[1] != costs[0]


def test_grok_cost_is_per_model_and_uses_200k_threshold():
    low_46 = token_rates("xai", "grok-4.6", prompt_tokens=1000)
    high_46 = token_rates("xai", "grok-4.6", prompt_tokens=200_000)
    low_43 = token_rates("xai", "grok-4.3", prompt_tokens=1000)
    high_43 = token_rates("xai", "grok-4.3", prompt_tokens=200_000)
    assert low_46 == (2e-6, 6e-6)
    assert high_46 == (4e-6, 12e-6)
    assert low_43 == (1.25e-6, 2.5e-6)
    assert high_43 == (2.5e-6, 5e-6)

    usage_small = InferenceUsage(
        input_tokens=1000, output_tokens=100, total_tokens=1100, usage_status="exact"
    )
    usage_long = InferenceUsage(
        input_tokens=200_000, output_tokens=100, total_tokens=200_100, usage_status="exact"
    )
    cost_46_small = calculate_cost(
        usage_small, resolve_pricing_snapshot(provider="xai", model="grok-4.6", usage=usage_small)
    )
    cost_43_small = calculate_cost(
        usage_small, resolve_pricing_snapshot(provider="xai", model="grok-4.3", usage=usage_small)
    )
    cost_46_long = calculate_cost(
        usage_long, resolve_pricing_snapshot(provider="xai", model="grok-4.6", usage=usage_long)
    )
    assert cost_46_small.amount_usd == pytest.approx(1000 * 2e-6 + 100 * 6e-6)
    assert cost_43_small.amount_usd == pytest.approx(1000 * 1.25e-6 + 100 * 2.5e-6)
    assert cost_46_long.amount_usd == pytest.approx(200_000 * 4e-6 + 100 * 12e-6)
    assert cost_46_small.amount_usd != cost_43_small.amount_usd


@pytest.mark.asyncio
async def test_missing_xai_key_is_honest(monkeypatch):
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    chunks = []
    async for chunk, model, prov in route_request_stream(
        "hi", TENANT, "free", provider_id="grok", model_override="grok-4.6"
    ):
        chunks.append((chunk, model, prov))
    assert chunks
    assert "Grok is not configured" in chunks[0][0]
    assert chunks[0][1] == ""
    assert chunks[0][2] == "xai"


@pytest.mark.asyncio
async def test_failed_xai_request_does_not_persist(monkeypatch):
    from services.chat_service import stream_chat_response

    monkeypatch.setenv("XAI_API_KEY", "xai-test-key-not-a-secret")
    persist_calls = {"n": 0}

    async def boom(cx, *, model, message, tenant_id, system=None):
        raise httpx.HTTPStatusError(
            "server",
            request=httpx.Request("POST", "https://api.x.ai/v1/chat/completions"),
            response=httpx.Response(500),
        )
        yield  # pragma: no cover

    def fake_sqlite(*_a, **_k):
        persist_calls["n"] += 1
        return (1, 2)

    tid = uuid.uuid4()
    org = uuid.uuid4()
    with (
        patch("services.chat_service.resolve_thread_id", new=AsyncMock(return_value=tid)),
        patch("services.chat_service.is_project_setup_thread", return_value=False),
        patch(
            "services.chat_service.build_chat_message_with_thread_context",
            new=AsyncMock(side_effect=lambda _o, _t, m: m),
        ),
        patch("services.chat_service.inject_knowledge_few_shot", new=AsyncMock(side_effect=lambda _m, p: p)),
        patch("services.chat_service.apply_language_context", side_effect=lambda msg, _lang: msg),
        patch.object(get_gateway_provider("xai"), "stream_message", side_effect=boom),
        patch("services.chat_service.persist_chat_exchange_sqlite", side_effect=fake_sqlite),
        patch("services.chat_service._schedule_chat_persist"),
    ):
        events = []
        async for line in stream_chat_response(
            "hello",
            "user-1",
            str(org),
            "free",
            thread_id=tid,
            provider_id="grok",
            model_override="grok-4.6",
        ):
            events.append(json.loads(line))

    assert persist_calls["n"] == 0
    assert any(e.get("type") == "error" for e in events)


@pytest.mark.asyncio
async def test_existing_provider_dropdowns_unchanged(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
    monkeypatch.setenv("GOOGLE_API_KEY", "g-test")
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

    with (
        patch.object(get_gateway_provider("openai"), "stream_message", side_effect=fake_openai),
        patch.object(get_gateway_provider("anthropic"), "stream_message", side_effect=fake_anthropic),
        patch.object(get_gateway_provider("google"), "stream_message", side_effect=fake_google),
    ):
        async for _ in route_request_stream(
            "hi", TENANT, "free", provider_id="gpt", model_override="gpt-4o-mini"
        ):
            pass
        async for _ in route_request_stream(
            "hi", TENANT, "free", provider_id="claude", model_override="claude-opus-4.8"
        ):
            pass
        async for _ in route_request_stream(
            "hi", TENANT, "free", provider_id="gemini", model_override="gemini-3.5-flash"
        ):
            pass

    from services.model_gateway import resolve_dispatch_model

    assert seen["openai"] == resolve_dispatch_model("openai", "gpt-4o-mini")
    assert seen["anthropic"] == resolve_dispatch_model("anthropic", "claude-opus-4.8")
    assert seen["google"] == resolve_dispatch_model("google", "gemini-3.5-flash")
