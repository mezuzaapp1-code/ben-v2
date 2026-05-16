"""Runtime diagnostics: snapshot, events, no PII/prompt leakage."""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:5432/test")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-openai")

import main  # noqa: E402
from auth.tenant_binding import TenantContext  # noqa: E402
from services.ops.load_governance import reset_load_governor_for_tests  # noqa: E402
from services.ops.runtime_diagnostics import (  # noqa: E402
    anonymize_tenant_id,
    begin_request_diagnostics,
    build_runtime_snapshot,
    detect_dominant_language,
    emit_runtime_event,
    get_runtime_metrics,
    record_provider_call,
    reset_runtime_metrics_for_tests,
)
from services.ops.runtime_events import OVERLOAD_REJECTED, REQUEST_STARTED  # noqa: E402

TENANT = "11111111-1111-1111-1111-111111111111"


@pytest.fixture(autouse=True)
def _reset_stores():
    reset_runtime_metrics_for_tests()
    reset_load_governor_for_tests(max_chat=4, max_council=2, max_total=6)
    yield
    reset_runtime_metrics_for_tests()
    reset_load_governor_for_tests()


@pytest.fixture(autouse=True)
def _auth_env(monkeypatch):
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_clerk")
    monkeypatch.setenv("ENFORCE_AUTH", "false")
    monkeypatch.setenv("AUTH_SHADOW_MODE", "false")
    monkeypatch.setenv("BEN_ANONYMOUS_ORG_ID", "00000000-0000-0000-0000-000000000001")


@pytest.fixture
def client():
    return TestClient(main.app)


def test_anonymize_tenant_id_deterministic():
    a = anonymize_tenant_id(TENANT)
    b = anonymize_tenant_id(TENANT)
    assert a == b
    assert TENANT not in a
    assert len(a) == 12


def test_detect_dominant_language_hebrew():
    assert detect_dominant_language("מה הסיכון?") == "he"


def test_emit_runtime_event_filters_forbidden(caplog):
    with caplog.at_level(logging.INFO, logger="ben.ops"):
        emit_runtime_event(
            REQUEST_STARTED,
            route="/council",
            tenant_hash="abc123",
            question="secret prompt must not appear",
            message="also forbidden",
        )
    assert "secret prompt" not in caplog.text
    assert "also forbidden" not in caplog.text


@pytest.mark.asyncio
async def test_provider_timing_counts():
    await record_provider_call(
        provider="anthropic",
        operation="expert_legal",
        duration_ms=120,
        outcome="timeout",
    )
    await record_provider_call(
        provider="openai",
        operation="synthesis",
        duration_ms=80,
        outcome="ok",
    )
    snap = await get_runtime_metrics().snapshot_fields()
    assert snap["provider_timeout_counts"]["anthropic"] >= 1
    assert snap["provider_ok_counts"]["openai"] >= 1


@pytest.mark.asyncio
async def test_overload_rejected_count():
    await get_runtime_metrics().record_overload_rejected(code="council_busy", route="/council")
    snap = await get_runtime_metrics().snapshot_fields()
    assert snap["overload_rejected_counts"].get("council_busy") == 1


def test_runtime_snapshot_endpoint(client):
    r = client.get("/runtime/snapshot")
    assert r.status_code == 200
    data = r.json()
    assert "active_chat_requests" in data
    assert "active_council_requests" in data
    assert "inflight_total" in data
    assert "provider_timeout_counts" in data
    assert "degraded_council_count" in data
    assert TENANT not in json.dumps(data)


def test_chat_request_lifecycle_events(client, caplog):
    with patch.object(main, "handle_chat", new_callable=AsyncMock) as mock_chat:
        mock_chat.return_value = {"thread_id": "t", "response": "ok", "model_used": "m", "cost_usd": 0}
        with caplog.at_level(logging.INFO, logger="ben.ops"):
            r = client.post("/chat", json={"message": "Hello diagnostics", "tier": "free"})
    assert r.status_code == 200
    assert "request_started" in caplog.text
    assert "request_completed" in caplog.text
    assert "Hello diagnostics" not in caplog.text


def test_council_overload_emits_rejected(client, caplog):
    reset_load_governor_for_tests(max_chat=4, max_council=1, max_total=6)
    import threading

    hold = threading.Event()

    async def slow_council(*_a, **_k):
        for _ in range(150):
            if hold.is_set():
                break
            await __import__("asyncio").sleep(0.02)
        return {"council": [], "cost_usd": 0.0}

    with patch.object(main, "run_council", new_callable=AsyncMock, side_effect=slow_council):
        import threading
        import time

        def first():
            client.post("/council", json={"question": "Snapshot overload test?"})

        t = threading.Thread(target=first)
        t.start()
        time.sleep(0.35)
        with caplog.at_level(logging.WARNING, logger="ben.ops"):
            r2 = client.post("/council", json={"question": "Other question?"})
        hold.set()
        t.join(timeout=8)
    assert r2.status_code in (503, 429)
    assert "request_failed" in caplog.text or "council concurrency" in caplog.text
    snap = client.get("/runtime/snapshot").json()
    assert snap.get("rejected_overload_requests", 0) >= 1


@pytest.mark.asyncio
async def test_begin_request_diagnostics_context():
    ctx = TenantContext(
        tenant_id=TENANT,
        tenant_type="anonymous",
        user_id=None,
        org_id=None,
        email=None,
        auth_source="anonymous",
        auth_present=False,
        org_bound=False,
    )
    begin_request_diagnostics(route="/chat", ctx=ctx, text_hint="test")
    snap = await build_runtime_snapshot()
    assert "active_chat_requests" in snap
