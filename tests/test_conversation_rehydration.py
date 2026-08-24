"""Conversation rehydration v1: thread_id continuity, message encoding, API wiring."""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:5432/test")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-openai")

import main
from services.message_format import (
    build_synthesis_display_text,
    decode_message,
    encode_chat_assistant,
    encode_council_expert,
    encode_council_synthesis,
    encode_user_turn,
)

ORG = "00000000-0000-0000-0000-000000000001"
THREAD_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
THREAD_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


@pytest.fixture(autouse=True)
def _auth_env(monkeypatch):
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_clerk")
    monkeypatch.setenv("ENFORCE_AUTH", "false")
    monkeypatch.setenv("AUTH_SHADOW_MODE", "false")
    monkeypatch.setenv("BEN_ANONYMOUS_ORG_ID", ORG)


@pytest.fixture(autouse=True)
def _gate_a_customer():
    from tests.helpers_auth import patch_main_persistent_tenant

    with patch_main_persistent_tenant(ORG):
        yield


def test_encode_decode_user_turn_does_not_leak_envelope():
    paste = "P" * 12_000
    raw = encode_user_turn(
        [
            {"type": "text", "text": "Look: "},
            {
                "type": "large_paste",
                "id": "p1",
                "label": "Pasted text",
                "text": paste,
                "char_count": 12_000,
            },
        ]
    )
    out = decode_message("user", raw)
    assert out["kind"] == "user_turn"
    assert paste not in out["content"]
    assert '{"ben":' not in out["content"]
    assert out["parts"][1]["text"] == paste


def test_encode_decode_chat_assistant():
    raw = encode_chat_assistant(
        "hello",
        model_used="gpt-4o-mini",
        cost_usd=0.001,
        provider_id="gpt",
        provider_used="openai",
    )
    out = decode_message("assistant", raw)
    assert out["content"] == "hello"
    assert out["model_used"] == "gpt-4o-mini"
    assert out["cost_usd"] == 0.001
    assert out["provider_id"] == "gpt"
    assert out["provider_used"] == "openai"


def test_encode_decode_council_expert_metadata():
    raw = encode_council_expert(
        expert="Legal Advisor",
        response="Risk noted.",
        provider="anthropic",
        model="claude-sonnet-4-6",
        outcome="timeout",
    )
    out = decode_message("assistant", raw)
    assert out["expert_outcome"] == "timeout"
    assert out["expert_status"] == "Unavailable: timeout"
    assert "Legal Advisor" in out["content"]


def test_encode_decode_council_synthesis():
    syn = {"recommendation": "R", "consensus_points": "C", "agreement_estimate": "3/3 available"}
    display = build_synthesis_display_text(syn, any_expert_failed=False)
    raw = encode_council_synthesis(synthesis=syn, cost_usd=0.5, display_text=display)
    out = decode_message("assistant", raw)
    assert out["kind"] == "council_synthesis"
    assert out["synthesis"]["recommendation"] == "R"


def test_chat_passes_thread_id_to_handler():
    captured: dict = {}

    async def fake_chat(
        message, user_id, tenant_id, tier, *,
        thread_id=None,
        provider_id=None,
        model_override=None,
        preferred_language=None,
        **_k,
    ):
        captured["thread_id"] = thread_id
        return {"thread_id": THREAD_A, "response": "ok", "model_used": "m", "cost_usd": 0.0}

    with patch.object(main, "handle_chat", side_effect=fake_chat):
        with TestClient(main.app) as client:
            r = client.post(
                "/chat",
                json={"message": "hi", "tier": "free", "thread_id": THREAD_A},
            )
    assert r.status_code == 200
    assert captured["thread_id"] == uuid.UUID(THREAD_A)


def test_council_passes_thread_id():
    captured: dict = {}

    async def fake_council(question, tenant_id, *, thread_id=None, force_codebase=False):
        captured["thread_id"] = thread_id
        return {
            "question": question,
            "council": [],
            "synthesis": None,
            "cost_usd": 0.0,
        }

    with patch.object(main, "run_council", side_effect=fake_council):
        with TestClient(main.app) as client:
            r = client.post("/council", json={"question": "q?", "thread_id": THREAD_A})
    assert r.status_code == 200
    assert captured["thread_id"] == uuid.UUID(THREAD_A)


def test_council_passes_force_codebase_to_handler():
    captured: dict = {}

    async def fake_council(question, tenant_id, *, thread_id=None, force_codebase=False):
        captured["force_codebase"] = force_codebase
        return {
            "question": question,
            "council": [],
            "synthesis": None,
            "cost_usd": 0.0,
        }

    with patch.object(main, "run_council", side_effect=fake_council):
        with TestClient(main.app) as client:
            r = client.post("/council", json={"question": "q?", "force_codebase": True})
    assert r.status_code == 200
    assert captured["force_codebase"] is True


def test_list_threads_api():
    fake_payload = {"threads": [{"id": THREAD_A, "title": "T", "created_at": None, "updated_at": None}]}

    with patch("main.list_threads", new=AsyncMock(return_value=fake_payload)):
        with TestClient(main.app) as client:
            r = client.get("/api/threads")
    assert r.status_code == 200
    assert r.json()["threads"][0]["id"] == THREAD_A


def test_get_thread_404_other_org():
    with patch(
        "main.get_thread_detail",
        new=AsyncMock(side_effect=__import__("fastapi").HTTPException(404, "Thread not found")),
    ):
        with TestClient(main.app) as client:
            r = client.get(f"/api/threads/{THREAD_B}")
    assert r.status_code == 404


def test_invalid_thread_id_422():
    with patch.object(main, "handle_chat", new_callable=AsyncMock):
        with TestClient(main.app) as client:
            r = client.post("/chat", json={"message": "hi", "tier": "free", "thread_id": "not-a-uuid"})
    assert r.status_code == 422
