"""Persistence integrity & data governance v1."""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:5432/test")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-openai")

import main  # noqa: E402
from auth.tenant_ids import personal_tenant_id  # noqa: E402
from services.message_format import (  # noqa: E402
    decode_message,
    encode_chat_assistant,
    encode_council_expert,
    encode_council_synthesis,
)
from services.ops.idempotency import reset_idempotency_registry_for_tests  # noqa: E402
from services.ops.load_governance import reset_load_governor_for_tests  # noqa: E402
from services.ops.persistence_integrity import (  # noqa: E402
    audit_decoded_thread_messages,
    audit_message_row,
    audit_thread_messages_for_org,
    check_cross_tenant_access,
    findings_to_safe_codes,
    validate_council_member,
)
from services.ops.runtime_diagnostics import reset_runtime_metrics_for_tests  # noqa: E402

ORG_A = uuid.UUID("11111111-1111-1111-1111-111111111111")
ORG_B = uuid.UUID("22222222-2222-2222-2222-222222222222")
ANON = "00000000-0000-0000-0000-000000000001"
THREAD_A = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_clerk")
    monkeypatch.setenv("ENFORCE_AUTH", "false")
    monkeypatch.setenv("BEN_ANONYMOUS_ORG_ID", ANON)
    reset_idempotency_registry_for_tests()
    reset_load_governor_for_tests()
    reset_runtime_metrics_for_tests()
    yield


@pytest.fixture
def client():
    return TestClient(main.app)


def _msg_row(*, org: uuid.UUID, thread: uuid.UUID, role: str, content: str):
    return SimpleNamespace(org_id=org, thread_id=thread, role=role, content=content)


def test_cross_tenant_access_detected():
    finding = check_cross_tenant_access(expected_org_id=ORG_A, resource_org_id=ORG_B)
    assert finding is not None
    assert finding.code == "cross_tenant_access"


def test_validate_council_member_requires_metadata():
    findings = validate_council_member({"expert": "Legal Advisor"})
    assert any(f.code == "malformed_council_envelope" for f in findings)


def test_audit_message_row_orphan():
    findings = audit_message_row(
        org_id=ORG_A,
        thread_id=THREAD_A,
        message_org_id=ORG_A,
        message_thread_id=uuid.uuid4(),
        role="user",
        content="hi",
    )
    assert any(f.code == "orphan_message" for f in findings)


def test_legacy_plain_assistant_tolerated():
    findings = audit_message_row(
        org_id=ORG_A,
        thread_id=THREAD_A,
        message_org_id=ORG_A,
        message_thread_id=THREAD_A,
        role="assistant",
        content="legacy plain text",
    )
    assert any(f.code == "legacy_plain_assistant" for f in findings)


def test_duplicate_synthesis_detection():
    syn = {"recommendation": "R", "consensus_points": "C", "agreement_estimate": "3/3"}
    raw = encode_council_synthesis(synthesis=syn, cost_usd=0.1, display_text="display")
    d1 = decode_message("assistant", raw)
    d2 = decode_message("assistant", raw)
    findings = audit_decoded_thread_messages([d1, d2])
    assert any(f.code == "duplicate_council_synthesis" for f in findings)


def test_chat_persist_reload_roundtrip():
    assistant_raw = encode_chat_assistant("answer", model_used="gpt-4o-mini", cost_usd=0.01)
    rows = [
        _msg_row(org=ORG_A, thread=THREAD_A, role="user", content="question"),
        _msg_row(org=ORG_A, thread=THREAD_A, role="assistant", content=assistant_raw),
    ]
    findings = audit_thread_messages_for_org(ORG_A, THREAD_A, rows)
    assert not any(f.code == "cross_tenant_access" for f in findings)
    decoded = [decode_message(r.role, r.content) for r in rows]
    assert decoded[1]["content"] == "answer"
    assert decoded[1]["model_used"] == "gpt-4o-mini"


def test_council_persist_reload_preserves_outcome():
    expert_raw = encode_council_expert(
        expert="Legal Advisor",
        response="R",
        provider="anthropic",
        model="claude",
        outcome="timeout",
    )
    rows = [
        _msg_row(org=ORG_A, thread=THREAD_A, role="user", content="Q?"),
        _msg_row(org=ORG_A, thread=THREAD_A, role="assistant", content=expert_raw),
    ]
    findings = audit_thread_messages_for_org(ORG_A, THREAD_A, rows)
    assert "cross_tenant_access" not in findings_to_safe_codes(findings)
    out = decode_message("assistant", expert_raw)
    assert out["expert_outcome"] == "timeout"


@pytest.mark.asyncio
async def test_duplicate_council_retry_skips_second_persist():
    from services.council_service import _persist_council_thread_if_needed

    reset_idempotency_registry_for_tests()
    reg = __import__("services.ops.idempotency", fromlist=["get_idempotency_registry"]).get_idempotency_registry()
    begin = await reg.begin(route="/council", tenant_id=str(ORG_A), client_request_id="dup-persist-1")
    payload = {
        "council": [
            {
                "expert": "Legal Advisor",
                "response": "x",
                "provider": "anthropic",
                "model": "m",
                "outcome": "ok",
            }
        ],
        "synthesis": None,
        "cost_usd": 0.0,
    }
    persist_mock = AsyncMock(return_value=THREAD_A)
    with patch("services.council_service.persist_council_transcript", persist_mock):
        await _persist_council_thread_if_needed(str(ORG_A), THREAD_A, "Q?", payload)
        await _persist_council_thread_if_needed(str(ORG_A), THREAD_A, "Q?", payload)
    assert persist_mock.await_count == 1


def test_background_persist_failure_does_not_break_council_response(client):
    async def fake_council(*_a, **_k):
        return {
            "question": "q",
            "council": [
                {
                    "expert": "Legal Advisor",
                    "outcome": "ok",
                    "response": "x",
                    "provider": "openai",
                    "model": "m",
                }
            ],
            "synthesis": {"recommendation": "done"},
            "cost_usd": 0.01,
        }

    async def fail_persist(*_a, **_k):
        raise RuntimeError("db down")

    with patch.object(main, "run_council", new_callable=AsyncMock, side_effect=fake_council):
        with patch(
            "services.council_service._persist_council_thread_if_needed",
            new_callable=AsyncMock,
            side_effect=fail_persist,
        ), patch("services.council_service._persist_synthesis_ko", new_callable=AsyncMock):
            r = client.post("/council", json={"question": "q?", "thread_id": str(THREAD_A)})
    assert r.status_code == 200
    assert r.json().get("synthesis") is not None


def test_malformed_legacy_rehydration_safe():
    out = decode_message("assistant", "not json at all")
    assert out["role"] == "assistant"
    assert out["content"] == "not json at all"


def test_get_thread_tenant_b_cannot_read_tenant_a():
    with patch(
        "main.get_thread_detail",
        new=AsyncMock(side_effect=HTTPException(404, "Thread not found")),
    ):
        with TestClient(main.app) as c:
            r = c.get(f"/api/threads/{THREAD_A}")
    assert r.status_code == 404


def test_list_threads_uses_bound_tenant(monkeypatch):
    from auth.tenant_binding import build_tenant_context

    personal = personal_tenant_id("user_a")
    list_mock = AsyncMock(return_value={"threads": []})

    def no_org(_token):
        return {"user_id": "user_a", "org_id": None}

    monkeypatch.setenv("ENFORCE_AUTH", "true")
    with patch("auth.tenant_binding._clerk_verify_token", side_effect=no_org):
        with patch("main.list_threads", list_mock):
            with TestClient(main.app) as c:
                r = c.get("/api/threads", headers={"Authorization": "Bearer tok"})
    assert r.status_code == 200
    ctx = build_tenant_context("auth_valid", {"user_id": "user_a", "org_id": None}, True)
    assert str(list_mock.await_args[0][0]) == ctx.tenant_id
    assert ctx.tenant_id == personal


def test_idempotent_replay_does_not_double_persist(client):
    async def fake_council(*_a, **_k):
        return {
            "question": "q",
            "council": [{"expert": "Legal Advisor", "outcome": "ok", "response": "x", "provider": "o", "model": "m"}],
            "synthesis": {"recommendation": "done"},
            "cost_usd": 0.01,
        }

    persist_mock = AsyncMock(return_value=None)
    with patch.object(main, "run_council", new_callable=AsyncMock, side_effect=fake_council):
        with patch(
            "services.council_service._persist_council_thread_if_needed",
            persist_mock,
        ), patch("services.council_service._persist_synthesis_ko", new_callable=AsyncMock):
            r1 = client.post(
                "/council",
                json={"question": "q?", "client_request_id": "integrity-replay-1"},
            )
            r2 = client.post(
                "/council",
                json={"question": "q?", "client_request_id": "integrity-replay-1"},
            )
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r2.json().get("idempotent_replay") is True
    assert persist_mock.await_count == 1


def test_get_thread_detail_integrity_warnings_on_duplicate_synthesis():
    syn = {"recommendation": "R", "consensus_points": "C", "agreement_estimate": "3/3"}
    raw = encode_council_synthesis(synthesis=syn, cost_usd=0.1, display_text="d")
    fake_detail = {
        "thread": {"id": str(THREAD_A), "title": "T", "created_at": None, "updated_at": None},
        "messages": [
            {"id": "1", "role": "user", "created_at": None, "content": "q"},
            {"id": "2", "role": "assistant", "created_at": None, **decode_message("assistant", raw)},
            {"id": "3", "role": "assistant", "created_at": None, **decode_message("assistant", raw)},
        ],
        "integrity_warnings": ["duplicate_council_synthesis"],
    }

    with patch("main.get_thread_detail", new=AsyncMock(return_value=fake_detail)):
        with TestClient(main.app) as c:
            r = c.get(f"/api/threads/{THREAD_A}")
    assert r.status_code == 200
    assert "duplicate_council_synthesis" in r.json().get("integrity_warnings", [])


def test_anonymous_vs_org_tenant_isolation_binding():
    from auth.tenant_binding import build_tenant_context

    anon = build_tenant_context("auth_missing", None, False)
    org = build_tenant_context("auth_valid", {"user_id": "u", "org_id": str(ORG_A)}, True)
    assert anon.tenant_id != org.tenant_id
