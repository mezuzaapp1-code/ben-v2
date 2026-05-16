"""Runtime recovery & idempotency v1."""
from __future__ import annotations

import asyncio
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
from services.ops.idempotency import (  # noqa: E402
    get_idempotency_registry,
    reset_idempotency_registry_for_tests,
)
from services.ops.load_governance import reset_load_governor_for_tests  # noqa: E402
from services.ops.runtime_state import COUNCIL_COMPLETED, derive_council_runtime_state  # noqa: E402

TENANT = "00000000-0000-0000-0000-000000000001"
CLIENT_ID = "test-client-req-001"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_clerk")
    monkeypatch.setenv("ENFORCE_AUTH", "false")
    monkeypatch.setenv("BEN_ANONYMOUS_ORG_ID", TENANT)
    reset_idempotency_registry_for_tests()
    reset_load_governor_for_tests()
    yield
    reset_idempotency_registry_for_tests()


@pytest.fixture
def client():
    return TestClient(main.app)


def test_derive_council_runtime_state_degraded():
    members = [{"outcome": "ok"}, {"outcome": "timeout"}]
    assert derive_council_runtime_state(members, {"recommendation": "x"}) == "council_degraded"


@pytest.mark.asyncio
async def test_duplicate_client_id_rejected_while_pending():
    reg = get_idempotency_registry()
    first = await reg.begin(route="/council", tenant_id=TENANT, client_request_id=CLIENT_ID)
    assert first.active
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await reg.begin(route="/council", tenant_id=TENANT, client_request_id=CLIENT_ID)
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "idempotency_rejected"


@pytest.mark.asyncio
async def test_replay_after_complete():
    reg = get_idempotency_registry()
    begin = await reg.begin(route="/council", tenant_id=TENANT, client_request_id="replay-1")
    payload = {
        "council": [{"expert": "Legal Advisor", "outcome": "ok", "response": "ok"}],
        "synthesis": {"recommendation": "go"},
        "runtime_state": COUNCIL_COMPLETED,
    }
    await reg.complete(begin.store_key, payload)
    second = await reg.begin(route="/council", tenant_id=TENANT, client_request_id="replay-1")
    assert not second.active
    assert second.replay_response is not None
    assert second.replay_response.get("runtime_state") == COUNCIL_COMPLETED


@pytest.mark.asyncio
async def test_persistence_dedupe_marker():
    reg = get_idempotency_registry()
    begin = await reg.begin(route="/council", tenant_id=TENANT, client_request_id="persist-1")
    assert await reg.should_persist(begin.store_key, "council_transcript")
    await reg.mark_persisted(begin.store_key, "council_transcript")
    assert not await reg.should_persist(begin.store_key, "council_transcript")


def test_council_api_replay(client):
    payload = {
        "question": "Idempotent council?",
        "client_request_id": "api-replay-council-1",
        "council": [],
        "synthesis": None,
        "cost_usd": 0.0,
        "runtime_state": COUNCIL_COMPLETED,
    }

    async def fake_council(*_a, **_k):
        return {
            "question": "Idempotent council?",
            "council": [{"expert": "Legal Advisor", "outcome": "ok", "response": "x", "provider": "openai", "model": "m"}],
            "synthesis": {"recommendation": "done"},
            "cost_usd": 0.01,
        }

    with patch.object(main, "run_council", new_callable=AsyncMock, side_effect=fake_council):
        with patch(
            "services.council_service._persist_council_thread_if_needed",
            new_callable=AsyncMock,
            return_value=None,
        ), patch("services.council_service._persist_synthesis_ko", new_callable=AsyncMock):
            r1 = client.post(
                "/council",
                json={"question": "Idempotent council?", "client_request_id": "api-replay-council-1"},
            )
            r2 = client.post(
                "/council",
                json={"question": "Idempotent council?", "client_request_id": "api-replay-council-1"},
            )

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r2.json().get("idempotent_replay") is True
    assert "Idempotent council?" not in str(r2.json())


def test_chat_idempotency_replay(client):
    with patch.object(main, "handle_chat", new_callable=AsyncMock) as mock_chat:
        mock_chat.return_value = {"thread_id": "t1", "response": "hi", "model_used": "m", "cost_usd": 0}
        r1 = client.post(
            "/chat",
            json={"message": "hello", "tier": "free", "client_request_id": "chat-replay-1"},
        )
        r2 = client.post(
            "/chat",
            json={"message": "hello", "tier": "free", "client_request_id": "chat-replay-1"},
        )
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r2.json().get("idempotent_replay") is True
    assert mock_chat.await_count == 1
