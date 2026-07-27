"""Pass 1 — immutable inference accounting (one provider call == one ledger event)."""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:5432/test")

from services.inference.contracts import ExecutionContext, InferenceUsage
from services.inference.execution_context import begin_execution_context, set_execution_context
from services.inference.gateway_meter import (
    account_provider_attempt,
    classify_call_outcome,
    get_last_accounted_call,
)
from services.inference.pricing import calculate_cost, resolve_pricing_snapshot
from services.inference.usage_normalize import (
    merge_anthropic_stream_usage,
    normalize_anthropic_usage,
    normalize_gemini_usage,
    normalize_openai_usage,
    usage_missing,
)
from services.model_gateway import (
    accounted_openai_chat_completion,
    reset_circuit_breakers_for_tests,
    route_request,
    route_request_stream,
)
from services.providers.base_provider import ProviderSendResult, ProviderStreamEnd


TENANT = "00000000-0000-0000-0000-0000000000aa"


@pytest.fixture(autouse=True)
def _execution_ctx():
    reset_circuit_breakers_for_tests()
    begin_execution_context(
        org_id=TENANT,
        workspace_id="ws-test",
        pipeline="chat",
        capability_key="standard_chat",
    )
    yield
    set_execution_context(None)
    reset_circuit_breakers_for_tests()


def test_normalize_openai_usage_with_cached_and_reasoning():
    usage = normalize_openai_usage(
        {
            "prompt_tokens": 100,
            "completion_tokens": 40,
            "total_tokens": 140,
            "prompt_tokens_details": {"cached_tokens": 20},
            "completion_tokens_details": {"reasoning_tokens": 10},
        }
    )
    assert usage.usage_status == "exact"
    assert usage.input_tokens == 100
    assert usage.output_tokens == 40
    assert usage.cached_input_tokens == 20
    assert usage.reasoning_tokens == 10


def test_normalize_missing_usage():
    assert normalize_openai_usage(None).usage_status == "missing"
    assert normalize_anthropic_usage({}).usage_status == "missing"
    assert normalize_gemini_usage(None).usage_status == "missing"


def test_merge_anthropic_stream_usage():
    start = normalize_anthropic_usage({"input_tokens": 50, "output_tokens": 0})
    merged = merge_anthropic_stream_usage(start, {"output_tokens": 12})
    assert merged.input_tokens == 50
    assert merged.output_tokens == 12
    assert merged.usage_status == "exact"


def test_calculate_cost_unknown_when_usage_missing():
    snap = resolve_pricing_snapshot(provider="openai", model="gpt-5.5-instant")
    cost = calculate_cost(usage_missing(), snap)
    assert cost.cost_status == "unknown"
    assert cost.amount_usd is None
    assert cost.pricing_version


def test_calculate_cost_priced():
    snap = resolve_pricing_snapshot(provider="openai", model="gpt-5.5-instant")
    usage = InferenceUsage(
        input_tokens=1000,
        output_tokens=500,
        usage_status="exact",
        total_tokens=1500,
    )
    cost = calculate_cost(usage, snap)
    assert cost.cost_status == "priced"
    assert cost.amount_usd is not None
    assert cost.amount_usd > 0


def test_classify_outcomes():
    assert classify_call_outcome(None) == "success"
    assert classify_call_outcome(TimeoutError()) == "timeout"
    assert classify_call_outcome(RuntimeError("x")) == "error"


@pytest.mark.asyncio
async def test_account_provider_attempt_writes_once():
    persisted: list = []

    async def fake_persist(record):
        persisted.append(record)
        return {"persisted": True}

    with patch("services.inference.gateway_meter.record_inference_call", side_effect=fake_persist):
        await account_provider_attempt(
            provider="openai",
            model="gpt-5.5-instant",
            api_model="gpt-4o-mini",
            outcome="success",
            usage=InferenceUsage(input_tokens=10, output_tokens=5, usage_status="exact", total_tokens=15),
            latency_ms=12.5,
            stream=False,
        )
        await account_provider_attempt(
            provider="openai",
            model="gpt-5.5-instant",
            api_model="gpt-4o-mini",
            outcome="error",
            usage=usage_missing(),
            latency_ms=3.0,
            stream=False,
            error_class="HTTPStatusError",
        )

    assert len(persisted) == 2
    assert persisted[0].outcome == "success"
    assert persisted[1].outcome == "error"
    assert persisted[0].call_id != persisted[1].call_id
    last = get_last_accounted_call()
    assert last is not None
    assert last["outcome"] == "error"
    assert last["execution_id"]


@pytest.mark.asyncio
async def test_route_request_accounts_success(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    records: list = []

    async def fake_persist(record):
        records.append(record)
        return {"persisted": True, "call_id": record.call_id}

    async def fake_send(self, cx, *, model, message, tenant_id, system=None):
        return ProviderSendResult.from_token_counts(
            "hello",
            11,
            7,
            usage=InferenceUsage(input_tokens=11, output_tokens=7, total_tokens=18, usage_status="exact"),
        )

    with (
        patch("services.inference.gateway_meter.record_inference_call", side_effect=fake_persist),
        patch(
            "services.providers.openai_provider.OpenAIProvider.send_message",
            new=fake_send,
        ),
    ):
        out = await route_request("hi", TENANT, "free", provider_id="gpt")

    assert out["content"] == "hello"
    assert len(records) == 1
    assert records[0].outcome == "success"
    assert records[0].stream is False
    assert records[0].usage.input_tokens == 11
    assert records[0].usage.output_tokens == 7
    assert out["cost_usd"] >= 0
    assert out["execution_id"] == records[0].execution_id


@pytest.mark.asyncio
async def test_route_request_accounts_failure_then_success(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
    records: list = []
    calls = {"n": 0}

    async def fake_persist(record):
        records.append(record)
        return {"persisted": True}

    async def openai_fail(self, cx, *, model, message, tenant_id, system=None):
        raise TimeoutError("boom")

    async def anthropic_ok(self, cx, *, model, message, tenant_id, system=None):
        calls["n"] += 1
        return ProviderSendResult.from_token_counts(
            "ok",
            3,
            2,
            usage=InferenceUsage(input_tokens=3, output_tokens=2, total_tokens=5, usage_status="exact"),
        )

    # free tier may only try openai — force multi-attempt via paid path or monkeypatch attempts
    with (
        patch("services.inference.gateway_meter.record_inference_call", side_effect=fake_persist),
        patch("services.model_gateway._attempts", return_value=[("openai", "gpt-5.5-instant"), ("anthropic", "claude-sonnet-4.6")]),
        patch("services.providers.openai_provider.OpenAIProvider.send_message", new=openai_fail),
        patch("services.providers.anthropic_provider.AnthropicProvider.send_message", new=anthropic_ok),
    ):
        out = await route_request("hi", TENANT, "free")

    assert out["content"] == "ok"
    assert len(records) == 2
    assert records[0].outcome == "timeout"
    assert records[1].outcome == "success"
    assert records[0].provider == "openai"
    assert records[1].provider == "anthropic"


@pytest.mark.asyncio
async def test_route_request_stream_accounts_usage(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    records: list = []

    async def fake_persist(record):
        records.append(record)
        return {"persisted": True}

    async def fake_stream(self, cx, *, model, message, tenant_id, system=None):
        yield "tok"
        yield ProviderStreamEnd(
            usage=InferenceUsage(input_tokens=9, output_tokens=4, total_tokens=13, usage_status="exact"),
            provider_request_id="chatcmpl-x",
            finish_reason="stop",
        )

    with (
        patch("services.inference.gateway_meter.record_inference_call", side_effect=fake_persist),
        patch(
            "services.providers.openai_provider.OpenAIProvider.stream_message",
            new=fake_stream,
        ),
    ):
        chunks = []
        async for chunk, model, prov in route_request_stream(
            "hello",
            TENANT,
            "free",
            provider_id="gpt",
            model_override="gpt-4o",
        ):
            chunks.append((chunk, model, prov))

    assert chunks[0][0] == "tok"
    assert chunks[0][1] == "gpt-4o"
    assert len(records) == 1
    assert records[0].stream is True
    assert records[0].usage.input_tokens == 9
    assert records[0].usage.output_tokens == 4
    assert records[0].provider_request_id == "chatcmpl-x"
    last = get_last_accounted_call()
    assert last is not None
    assert last["cost_usd"] is not None or last["usage_status"] == "exact"


@pytest.mark.asyncio
async def test_route_request_stream_missing_usage_still_accounts(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    records: list = []

    async def fake_persist(record):
        records.append(record)
        return {"persisted": True}

    async def fake_stream(self, cx, *, model, message, tenant_id, system=None):
        yield "partial"
        # no ProviderStreamEnd — usage missing

    with (
        patch("services.inference.gateway_meter.record_inference_call", side_effect=fake_persist),
        patch(
            "services.providers.openai_provider.OpenAIProvider.stream_message",
            new=fake_stream,
        ),
    ):
        async for _ in route_request_stream("hello", TENANT, "free", provider_id="gpt"):
            pass

    assert len(records) == 1
    assert records[0].usage.usage_status == "missing"
    assert records[0].cost.cost_status == "unknown"


@pytest.mark.asyncio
async def test_no_double_count_on_single_success(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    records: list = []

    async def fake_persist(record):
        records.append(record)
        return {"persisted": True}

    async def fake_send(self, cx, *, model, message, tenant_id, system=None):
        return ProviderSendResult.from_token_counts(
            "ok",
            1,
            1,
            usage=InferenceUsage(input_tokens=1, output_tokens=1, total_tokens=2, usage_status="exact"),
        )

    with (
        patch("services.inference.gateway_meter.record_inference_call", side_effect=fake_persist),
        patch("services.providers.openai_provider.OpenAIProvider.send_message", new=fake_send),
    ):
        await route_request("hi", TENANT, "free", provider_id="gpt")

    assert len(records) == 1


@pytest.mark.asyncio
async def test_accounted_openai_chat_completion_project_agent(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    records: list = []

    async def fake_persist(record):
        records.append(record)
        return {"persisted": True}

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "id": "chatcmpl-pa",
                "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 8, "completion_tokens": 3, "total_tokens": 11},
            }

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            return _Resp()

    with (
        patch("services.inference.gateway_meter.record_inference_call", side_effect=fake_persist),
        patch("services.model_gateway.httpx.AsyncClient", _Client),
    ):
        data = await accounted_openai_chat_completion(
            messages=[{"role": "user", "content": "hi"}],
            tools=None,
            tenant_id=TENANT,
            model="gpt-5.5-instant",
            pipeline="project_agent",
        )

    assert data["choices"][0]["message"]["content"] == "hi"
    assert len(records) == 1
    assert records[0].pipeline == "project_agent"
    assert records[0].usage.input_tokens == 8
    assert records[0].outcome == "success"


def test_execution_context_carries_ids():
    ctx = begin_execution_context(
        org_id="org",
        workspace_id="ws",
        user_id="u1",
        capability_key="standard_chat",
        pipeline="standard_chat",
    )
    assert ctx.execution_id
    assert ctx.org_id == "org"
    assert isinstance(ctx, ExecutionContext)
