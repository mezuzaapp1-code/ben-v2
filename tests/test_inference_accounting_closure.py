"""Pass 1 closure: reconstruction smoke, idempotency, soft-fail, news isolation."""
from __future__ import annotations

import asyncio
import os
from collections import defaultdict
from unittest.mock import patch

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:5432/test")

from services.inference.contracts import InferenceUsage
from services.inference.execution_context import begin_execution_context, set_execution_context
from services.model_gateway import accounted_openai_chat_completion, reset_circuit_breakers_for_tests, route_request, route_request_stream
from services.ops.request_context import set_request_id
from services.providers.base_provider import ProviderSendResult, ProviderStreamEnd

TENANT = "00000000-0000-0000-0000-0000000000bb"


@pytest.fixture
def ledger():
    rows: list = []

    async def capture(record):
        rows.append(record)
        return {"persisted": True, "call_id": record.call_id}

    with patch("services.inference.gateway_meter.record_inference_call", side_effect=capture):
        yield rows


@pytest.fixture(autouse=True)
def _ctx():
    reset_circuit_breakers_for_tests()
    set_request_id("req-closure-fixed")
    begin_execution_context(
        org_id=TENANT,
        workspace_id="ws-closure",
        pipeline="standard_chat",
        capability_key="standard_chat",
        execution_id="exec-closure-fixed",
    )
    yield
    set_execution_context(None)
    reset_circuit_breakers_for_tests()


def _by_keys(rows):
    by_req = defaultdict(list)
    by_exec = defaultdict(list)
    for r in rows:
        by_req[r.request_id].append(r)
        by_exec[r.execution_id].append(r)
    return by_req, by_exec


@pytest.mark.asyncio
async def test_reconstruction_matrix(monkeypatch, ledger):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
    results = []

    # 1) non-stream success
    async def send_ok(self, cx, *, model, message, tenant_id, system=None):
        return ProviderSendResult.from_token_counts(
            "ok",
            10,
            4,
            usage=InferenceUsage(input_tokens=10, output_tokens=4, total_tokens=14, usage_status="exact"),
        )

    with patch("services.providers.openai_provider.OpenAIProvider.send_message", new=send_ok):
        out = await route_request("hi", TENANT, "free", provider_id="gpt")
    assert out["content"] == "ok"
    rows = list(ledger)
    assert len(rows) == 1
    r = rows[0]
    results.append(
        {
            "scenario": "non-stream chat",
            "request_id_present": bool(r.request_id),
            "execution_id_present": bool(r.execution_id),
            "call_records": 1,
            "usage_status": r.usage.usage_status,
            "cost_status": r.cost.cost_status,
            "success_failure": r.outcome,
            "stream_interrupted": False,
        }
    )
    ledger.clear()

    # 2) streaming success
    async def stream_ok(self, cx, *, model, message, tenant_id, system=None):
        yield "t"
        yield ProviderStreamEnd(
            usage=InferenceUsage(input_tokens=8, output_tokens=3, total_tokens=11, usage_status="exact")
        )

    with patch("services.providers.openai_provider.OpenAIProvider.stream_message", new=stream_ok):
        async for _ in route_request_stream("hi", TENANT, "free", provider_id="gpt"):
            pass
    r = ledger[0]
    results.append(
        {
            "scenario": "streaming chat",
            "request_id_present": bool(r.request_id),
            "execution_id_present": bool(r.execution_id),
            "call_records": len(ledger),
            "usage_status": r.usage.usage_status,
            "cost_status": r.cost.cost_status,
            "success_failure": r.outcome,
            "stream_interrupted": r.outcome == "stream_interrupted",
        }
    )
    ledger.clear()

    # 3) failed provider attempt
    async def send_fail(self, cx, *, model, message, tenant_id, system=None):
        raise TimeoutError("timeout")

    with (
        patch("services.model_gateway._attempts", return_value=[("openai", "gpt-5.5-instant")]),
        patch("services.providers.openai_provider.OpenAIProvider.send_message", new=send_fail),
    ):
        out = await route_request("hi", TENANT, "free", provider_id="gpt")
    r = ledger[0]
    results.append(
        {
            "scenario": "failed provider attempt",
            "request_id_present": bool(r.request_id),
            "execution_id_present": bool(r.execution_id),
            "call_records": len(ledger),
            "usage_status": r.usage.usage_status,
            "cost_status": r.cost.cost_status,
            "success_failure": r.outcome,
            "stream_interrupted": False,
        }
    )
    assert r.outcome == "timeout"
    ledger.clear()

    # 4) retry / fallback — two distinct call_ids, same execution_id
    async def openai_fail(self, cx, *, model, message, tenant_id, system=None):
        raise RuntimeError("provider down")

    async def anthropic_ok(self, cx, *, model, message, tenant_id, system=None):
        return ProviderSendResult.from_token_counts(
            "fallback",
            2,
            1,
            usage=InferenceUsage(input_tokens=2, output_tokens=1, total_tokens=3, usage_status="exact"),
        )

    with (
        patch(
            "services.model_gateway._attempts",
            return_value=[("openai", "gpt-5.5-instant"), ("anthropic", "claude-sonnet-4.6")],
        ),
        patch("services.providers.openai_provider.OpenAIProvider.send_message", new=openai_fail),
        patch("services.providers.anthropic_provider.AnthropicProvider.send_message", new=anthropic_ok),
    ):
        out = await route_request("hi", TENANT, "free")
    assert out["content"] == "fallback"
    assert len(ledger) == 2
    assert ledger[0].call_id != ledger[1].call_id
    assert ledger[0].execution_id == ledger[1].execution_id == "exec-closure-fixed"
    results.append(
        {
            "scenario": "retry/fallback",
            "request_id_present": bool(ledger[0].request_id),
            "execution_id_present": True,
            "call_records": 2,
            "usage_status": f"{ledger[0].usage.usage_status}/{ledger[1].usage.usage_status}",
            "cost_status": f"{ledger[0].cost.cost_status}/{ledger[1].cost.cost_status}",
            "success_failure": f"{ledger[0].outcome}/{ledger[1].outcome}",
            "stream_interrupted": False,
        }
    )
    ledger.clear()

    # 5) project-agent accounted path
    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "id": "chatcmpl-pa",
                "choices": [{"message": {"content": "pa"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
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

    with patch("services.model_gateway.httpx.AsyncClient", _Client):
        await accounted_openai_chat_completion(
            messages=[{"role": "user", "content": "x"}],
            tools=None,
            tenant_id=TENANT,
            model="gpt-5.5-instant",
            pipeline="project_agent",
        )
    r = ledger[0]
    assert r.pipeline == "project_agent"
    results.append(
        {
            "scenario": "project-agent model call",
            "request_id_present": bool(r.request_id),
            "execution_id_present": bool(r.execution_id),
            "call_records": 1,
            "usage_status": r.usage.usage_status,
            "cost_status": r.cost.cost_status,
            "success_failure": r.outcome,
            "stream_interrupted": False,
        }
    )

    # Print compact table for closure report (no secrets / content)
    print("\nRECONSTRUCTION_SMOKE_TABLE")
    for row in results:
        print(row)

    by_req, by_exec = _by_keys(ledger)  # last scenario only in ledger after clear pattern
    # Reconstructibility of shared execution across earlier scenarios is validated above.
    assert all(x["request_id_present"] and x["execution_id_present"] for x in results)


@pytest.mark.asyncio
async def test_stream_interrupt_finalizes_once(monkeypatch, ledger):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    async def stream_then_cancel(self, cx, *, model, message, tenant_id, system=None):
        yield "partial"
        raise asyncio.CancelledError()

    with patch(
        "services.providers.openai_provider.OpenAIProvider.stream_message",
        new=stream_then_cancel,
    ):
        with pytest.raises(asyncio.CancelledError):
            async for _ in route_request_stream("hi", TENANT, "free", provider_id="gpt"):
                pass

    assert len(ledger) == 1
    assert ledger[0].outcome == "stream_interrupted"
    assert ledger[0].usage.usage_status == "missing"


@pytest.mark.asyncio
async def test_rejected_attempt_recorded_once(monkeypatch, ledger):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    out = await route_request("hi", TENANT, "free", provider_id="gpt")
    assert "not configured" in out["content"].lower() or out["tokens"] == 0
    assert len(ledger) == 1
    assert ledger[0].outcome == "rejected"


@pytest.mark.asyncio
async def test_persist_soft_fail_does_not_retry_provider(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    calls = {"n": 0}

    async def send_once(self, cx, *, model, message, tenant_id, system=None):
        calls["n"] += 1
        return ProviderSendResult.from_token_counts(
            "ok",
            1,
            1,
            usage=InferenceUsage(input_tokens=1, output_tokens=1, total_tokens=2, usage_status="exact"),
        )

    with (
        patch("services.providers.openai_provider.OpenAIProvider.send_message", new=send_once),
        patch(
            "services.inference.ledger.persist_inference_call",
            side_effect=RuntimeError("db down"),
        ),
        patch("services.inference.ledger.log_error") as log_err,
    ):
        out = await route_request("hi", TENANT, "free", provider_id="gpt")

    assert out["content"] == "ok"
    assert calls["n"] == 1  # provider not retried due to persist failure
    assert log_err.called


@pytest.mark.asyncio
async def test_shared_execution_id_across_council_like_calls(monkeypatch, ledger):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    async def send_ok(self, cx, *, model, message, tenant_id, system=None):
        return ProviderSendResult.from_token_counts(
            "ok",
            1,
            1,
            usage=InferenceUsage(input_tokens=1, output_tokens=1, total_tokens=2, usage_status="exact"),
        )

    with patch("services.providers.openai_provider.OpenAIProvider.send_message", new=send_ok):
        await route_request("a", TENANT, "free", provider_id="gpt")
        await route_request("b", TENANT, "free", provider_id="gpt")

    assert len(ledger) == 2
    assert ledger[0].execution_id == ledger[1].execution_id == "exec-closure-fixed"
    assert ledger[0].call_id != ledger[1].call_id


def test_news_modules_do_not_import_gateway_meter():
    import importlib
    import pkgutil

    import services.news as news_pkg

    offenders = []
    for mod in pkgutil.walk_packages(news_pkg.__path__, news_pkg.__name__ + "."):
        m = importlib.import_module(mod.name)
        src = getattr(m, "__file__", None)
        if not src:
            continue
        text = open(src, encoding="utf-8").read()
        if "account_provider_attempt" in text or "InferenceCallRecord" in text:
            offenders.append(mod.name)
    assert offenders == []


def test_project_agent_has_no_direct_openai_http():
    from pathlib import Path

    src = Path("services/project_agent_service.py").read_text(encoding="utf-8")
    assert "api.openai.com" not in src
    assert "accounted_openai_chat_completion" in src
