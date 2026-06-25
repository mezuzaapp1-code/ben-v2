"""Council lifecycle: non-blocking persistence and error humanization."""
from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:5432/test")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-openai")

from services import council_service as cs
from services.message_format import decode_message, encode_council_expert, encode_council_synthesis
from services.ops.idempotency import get_idempotency_registry, reset_idempotency_registry_for_tests


def _humanize(status: int, data: dict) -> str:
    """Mirror frontend humanizeCouncilHttpError for CI."""
    detail = data.get("detail")
    if status == 401:
        return detail if isinstance(detail, str) else "Sign in required to use Council."
    if status == 400:
        return (
            detail
            if isinstance(detail, str)
            else "Organization context missing. Select an organization in Clerk and try again."
        )
    if status == 422:
        return detail if isinstance(detail, str) else "Invalid request. Check your session and try again."
    if status >= 500:
        return "Council is temporarily unavailable. Please try again in a moment."
    return f"Council request failed ({status}). You can retry."


def test_humanize_http_errors():
    assert "Sign in" in _humanize(401, {})
    assert "Organization" in _humanize(400, {})
    assert "Invalid" in _humanize(422, {})
    assert "unavailable" in _humanize(503, {})


def test_council_room_envelope_shared_metadata():
    room_id = str(uuid.uuid4())
    question_id = "client-req-room-1"
    expert_raw = encode_council_expert(
        expert="Legal Advisor",
        response="ok",
        provider="anthropic",
        model="claude-sonnet-4-6",
        outcome="ok",
        room_id=room_id,
        question_id=question_id,
        expert_index=0,
    )
    syn_raw = encode_council_synthesis(
        synthesis={"recommendation": "r"},
        cost_usd=0.1,
        display_text="d",
        room_id=room_id,
        question_id=question_id,
        room_status="complete",
    )
    expert_data = json.loads(expert_raw)
    syn_data = json.loads(syn_raw)
    assert expert_data["room_id"] == room_id
    assert syn_data["room_id"] == room_id
    assert expert_data["question_id"] == question_id
    assert syn_data["question_id"] == question_id
    assert expert_data["expert_index"] == 0
    assert syn_data["room_status"] == "complete"

    expert_decoded = decode_message("assistant", expert_raw)
    syn_decoded = decode_message("assistant", syn_raw)
    assert expert_decoded["room_id"] == room_id
    assert syn_decoded["room_id"] == room_id


@pytest.mark.asyncio
async def test_run_council_copy_paste_payload():
    captured: dict = {}

    async def fake_copy_paste(
        *,
        org_id,
        thread_id,
        tenant_id,
        tier,
        opinion_request,
        provider_id=None,
    ):
        captured["opinion_request"] = opinion_request
        captured["tenant_id"] = tenant_id
        captured["tier"] = tier
        return {"response": "Council copy-paste reply.", "cost_usd": 0.0, "thread_id": None}

    with patch("services.council_service.run_copy_paste_opinion", new=AsyncMock(side_effect=fake_copy_paste)):
        payload = await cs.run_council("Room test?", "00000000-0000-0000-0000-000000000001")

    assert captured["opinion_request"] == "Room test?"
    assert payload["mode"] == "copy_paste"
    assert payload["council"] == []
    assert payload["response"] == "Council copy-paste reply."
    room = payload["room"]
    assert room["id"]
    assert room["status"] == "complete"
    assert room["member_count"] == 0


@pytest.mark.asyncio
async def test_persist_council_transcript_stamps_room_on_all_rows():
    room_id = str(uuid.uuid4())
    question_id = "q-persist-1"
    captured: list[str] = []

    class FakeSession:
        def add_all(self, rows):
            for row in rows:
                captured.append(row.content)

        async def execute(self, *_a, **_k):
            return None

        async def get(self, *_a, **_k):
            return type("T", (), {"org_id": uuid.UUID("11111111-1111-1111-1111-111111111111")})()

        async def commit(self):
            return None

    members = [
        {
            "expert": "Legal Advisor",
            "provider": "anthropic",
            "model": "m",
            "outcome": "ok",
            "response": "L",
            "expert_index": 0,
        },
        {
            "expert": "Business Advisor",
            "provider": "openai",
            "model": "m",
            "outcome": "ok",
            "response": "B",
            "expert_index": 1,
        },
    ]
    syn = {"recommendation": "x"}

    with patch("services.thread_service.get_db_session") as mock_sess:
        mock_sess.return_value.__aenter__ = AsyncMock(return_value=FakeSession())
        mock_sess.return_value.__aexit__ = AsyncMock(return_value=False)
        from services.thread_service import persist_council_transcript

        await persist_council_transcript(
            uuid.UUID("11111111-1111-1111-1111-111111111111"),
            uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
            "Q?",
            council_members=members,
            synthesis=syn,
            total_cost_usd=0.5,
            synthesis_display_text="display",
            room_id=room_id,
            question_id=question_id,
            room_status="complete",
        )

    assistant_rows = [json.loads(c) for c in captured if c.startswith('{"ben":')]
    assert len(assistant_rows) == 3
    for row in assistant_rows:
        assert row["room_id"] == room_id
        assert row["question_id"] == question_id
    synthesis_rows = [r for r in assistant_rows if r.get("kind") == "council_synthesis"]
    assert synthesis_rows[0]["room_status"] == "complete"


@pytest.mark.asyncio
async def test_idempotent_replay_preserves_room_identity():
    reset_idempotency_registry_for_tests()
    reg = get_idempotency_registry()
    room_id = str(uuid.uuid4())
    stored = {
        "question": "Q?",
        "council": [],
        "synthesis": None,
        "cost_usd": 0.0,
        "room": {
            "id": room_id,
            "question_id": "replay-room-1",
            "status": "complete",
            "member_count": 3,
        },
    }
    begin = await reg.begin(
        route="/council",
        tenant_id="00000000-0000-0000-0000-000000000001",
        client_request_id="replay-room-1",
    )
    await reg.complete(begin.store_key, stored)
    second = await reg.begin(
        route="/council",
        tenant_id="00000000-0000-0000-0000-000000000001",
        client_request_id="replay-room-1",
    )
    assert second.replay_response is not None
    assert second.replay_response["room"]["id"] == room_id
    assert second.replay_response["room"]["question_id"] == "replay-room-1"
