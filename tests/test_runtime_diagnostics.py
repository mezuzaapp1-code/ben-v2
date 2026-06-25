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
    attach_execution_plan_to_request_diagnostics,
    begin_request_diagnostics,
    build_runtime_snapshot,
    detect_dominant_language,
    emit_runtime_event,
    get_request_diagnostics,
    get_runtime_metrics,
    record_expert_budget,
    record_provider_call,
    record_transcript_persist_timeout,
    reset_runtime_metrics_for_tests,
)
from services.execution_plan import resolve_execution_plan  # noqa: E402
from services.global_service_store import connect_global_channel, init_global_service_schema  # noqa: E402
from services.workspace_resolver import resolve_workspace_context  # noqa: E402
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
    assert data.get("council_room_budget_envelope_ms") == 25_000
    assert "expert_legal_timeout_count" in data
    assert "expert_business_timeout_count" in data
    assert "expert_strategy_timeout_count" in data
    assert "transcript_persist_timeout_count" in data
    assert "council_room_budget_pressure_count" in data
    assert TENANT not in json.dumps(data)


@pytest.mark.asyncio
async def test_expert_budget_timeout_metrics():
    await record_expert_budget(label="Legal", duration_ms=11_900, outcome="timeout")
    await record_expert_budget(label="Business", duration_ms=4_000, outcome="ok")
    snap = await get_runtime_metrics().snapshot_fields()
    assert snap["expert_legal_timeout_count"] == 1
    assert snap["expert_business_timeout_count"] == 0
    assert snap["expert_legal_latency_p95_ms"] == 11_900


@pytest.mark.asyncio
async def test_transcript_persist_timeout_metric():
    await record_transcript_persist_timeout()
    snap = await get_runtime_metrics().snapshot_fields()
    assert snap["transcript_persist_timeout_count"] == 1


@pytest.mark.asyncio
async def test_persist_council_transcript_bounded_timeout():
    import asyncio
    import uuid
    from unittest.mock import AsyncMock, patch

    from services.thread_service import persist_council_transcript

    org = uuid.UUID(TENANT)
    tid = uuid.uuid4()

    class FakeSession:
        async def execute(self, *_a, **_k):
            return None

        async def get(self, *_a, **_k):
            return type("T", (), {"org_id": org})()

        def add_all(self, _rows):
            return None

        async def commit(self):
            await asyncio.sleep(1.0)

    with patch("services.thread_service.DB_OPERATION_TIMEOUT_S", 0.05):
        with patch("services.thread_service.get_db_session") as mock_sess:
            mock_sess.return_value.__aenter__ = AsyncMock(return_value=FakeSession())
            mock_sess.return_value.__aexit__ = AsyncMock(return_value=False)
            with pytest.raises(asyncio.TimeoutError):
                await persist_council_transcript(
                    org,
                    tid,
                    "Q?",
                    council_members=[],
                    synthesis=None,
                    total_cost_usd=0.0,
                    synthesis_display_text="",
                )
    snap = await get_runtime_metrics().snapshot_fields()
    assert snap["transcript_persist_timeout_count"] == 1


def test_chat_request_lifecycle_events(client, caplog):
    import logging

    ops_logger = logging.getLogger("ben.ops")
    saved_handlers = list(ops_logger.handlers)
    saved_propagate = ops_logger.propagate
    saved_level = ops_logger.level

    try:
        client.get("/health")
        caplog.set_level(logging.INFO, logger="ben.ops")
        if caplog.handler not in ops_logger.handlers:
            ops_logger.addHandler(caplog.handler)
        caplog.clear()

        with patch.object(main, "handle_chat", new_callable=AsyncMock) as mock_chat:
            mock_chat.return_value = {"thread_id": "t", "response": "ok", "model_used": "m", "cost_usd": 0}
            r = client.post("/chat", json={"message": "Hello diagnostics", "tier": "free"})

        assert r.status_code == 200
        lifecycle_events = [
            str(getattr(record, "event", None) or record.getMessage())
            for record in caplog.records
            if record.name == "ben.ops"
        ]
        assert "request_started" in lifecycle_events
        assert "request_completed" in lifecycle_events
        assert "Hello diagnostics" not in caplog.text

        snap = client.get("/runtime/snapshot").json()
        assert snap.get("active_chat_requests", 0) == 0
        assert snap.get("inflight_total", 0) == 0
    finally:
        ops_logger.handlers.clear()
        for handler in saved_handlers:
            ops_logger.addHandler(handler)
        ops_logger.propagate = saved_propagate
        ops_logger.setLevel(saved_level)


def test_council_overload_emits_rejected(client):
    reset_load_governor_for_tests(max_chat=4, max_council=1, max_total=6)
    import threading

    hold = threading.Event()

    async def slow_council(question, tenant_id, *, thread_id=None, force_codebase=False):
        for _ in range(150):
            if hold.is_set():
                break
            await __import__("asyncio").sleep(0.02)
        return {"question": question, "council": [], "cost_usd": 0.0}

    with patch.object(main, "run_council", new_callable=AsyncMock, side_effect=slow_council):
        import threading
        import time

        def first():
            client.post("/council", json={"question": "Snapshot overload test?"})

        t = threading.Thread(target=first)
        t.start()
        time.sleep(0.35)
        r2 = client.post("/council", json={"question": "Other question?"})
        hold.set()
        t.join(timeout=8)

    assert r2.status_code in (503, 429)
    snap = client.get("/runtime/snapshot").json()
    assert snap.get("rejected_overload_requests", 0) >= 1


@pytest.mark.asyncio
async def test_begin_request_diagnostics_context():
    ctx = TenantContext(
        tenant_id=TENANT,
        tenant_type="anonymous",
        user_id=None,
        org_id=None,
        org_role=None,
        email=None,
        auth_source="anonymous",
        auth_present=False,
        org_bound=False,
    )
    begin_request_diagnostics(route="/chat", ctx=ctx, text_hint="test")
    snap = await build_runtime_snapshot()
    assert "active_chat_requests" in snap


def _org_ctx_for_diagnostics():
    return TenantContext(
        tenant_id="00000000-0000-0000-0000-000000000001",
        tenant_type="organization",
        user_id="user-test-1",
        org_id="00000000-0000-0000-0000-000000000001",
        org_role="admin",
        email="test@example.com",
        auth_source="clerk_jwt",
        auth_present=True,
        org_bound=True,
    )


@pytest.fixture
def _engine_seed(tmp_path, monkeypatch):
    system_db = tmp_path / "system_main.db"
    monkeypatch.setenv("BEN_SYSTEM_DB_PATH", str(system_db))
    init_global_service_schema()
    org = "00000000-0000-0000-0000-000000000001"
    for catalog_key, name in (
        ("engine-grok", "Grok"),
        ("engine-claude", "Claude"),
        ("engine-gemini", "Gemini"),
    ):
        connect_global_channel(
            org,
            name=name,
            source_type="external_library",
            source_metadata={"catalog_key": catalog_key},
        )


def test_chat_execution_plan_diagnostics_ownership_contract(_engine_seed):
    ctx = _org_ctx_for_diagnostics()
    workspace = resolve_workspace_context(ctx)
    begin_request_diagnostics(route="/chat", ctx=ctx, text_hint="hello")
    plan = resolve_execution_plan(workspace, "standard_chat", requested_resource="claude")
    attach_execution_plan_to_request_diagnostics(plan)

    diag = get_request_diagnostics()
    assert diag is not None
    assert diag.execution_enforcement_owner == "execution_plan"
    assert diag.execution_requested_capability == "engine-claude"
    assert diag.execution_workspace_context_id == workspace.context_id
    assert diag.execution_org_policy_allowed is True
    assert diag.execution_workspace_intent_enabled is None
    assert diag.execution_allowed is True
    assert diag.execution_denial_reason is None


def test_council_execution_plan_diagnostics_ownership_contract():
    ctx = _org_ctx_for_diagnostics()
    workspace = resolve_workspace_context(ctx)
    begin_request_diagnostics(route="/council", ctx=ctx, text_hint="question")
    plan = resolve_execution_plan(workspace, "council")
    attach_execution_plan_to_request_diagnostics(plan)

    diag = get_request_diagnostics()
    assert diag is not None
    assert diag.execution_enforcement_owner == "execution_plan"
    assert diag.execution_requested_capability == "council"
    assert diag.execution_org_policy_allowed is None
    assert diag.execution_workspace_intent_enabled is None
    assert diag.execution_allowed is True
    assert diag.execution_denial_reason is None
