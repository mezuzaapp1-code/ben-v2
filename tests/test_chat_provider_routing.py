"""Chat provider_id routing (toolbar → /chat)."""
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

import main  # noqa: E402
from services.model_gateway import normalize_chat_provider_id, route_request  # noqa: E402
from services.providers import get_gateway_provider  # noqa: E402
from services.ops.idempotency import reset_idempotency_registry_for_tests  # noqa: E402
from services.ops.load_governance import reset_load_governor_for_tests  # noqa: E402

TENANT = "00000000-0000-0000-0000-000000000001"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_clerk")
    monkeypatch.setenv("ENFORCE_AUTH", "false")
    monkeypatch.setenv("BEN_ANONYMOUS_ORG_ID", TENANT)
    reset_idempotency_registry_for_tests()
    reset_load_governor_for_tests()
    yield


@pytest.fixture
def client():
    return TestClient(main.app)


def test_normalize_chat_provider_id_valid():
    assert normalize_chat_provider_id("gpt") == "gpt"
    assert normalize_chat_provider_id(" Claude ") == "claude"
    assert normalize_chat_provider_id(None) is None
    assert normalize_chat_provider_id("") is None


def test_normalize_chat_provider_id_invalid():
    with pytest.raises(ValueError, match="gpt, claude, gemini"):
        normalize_chat_provider_id("openai")


def test_chat_invalid_provider_id_400(client):
    with patch.object(main, "handle_chat", new_callable=AsyncMock):
        r = client.post(
            "/chat",
            json={"message": "hi", "tier": "free", "provider_id": "invalid-provider"},
        )
    assert r.status_code == 400
    assert "provider_id" in (r.json().get("detail") or "").lower()


def test_chat_passes_provider_id_to_handler(client):
    captured: dict = {}

    async def fake_chat(message, user_id, tenant_id, tier, *, thread_id=None, provider_id=None):
        captured["provider_id"] = provider_id
        return {"thread_id": str(uuid.uuid4()), "response": "ok", "model_used": "m", "cost_usd": 0.0}

    with patch.object(main, "handle_chat", side_effect=fake_chat):
        r = client.post("/chat", json={"message": "hi", "tier": "free", "provider_id": "claude"})
    assert r.status_code == 200
    assert captured["provider_id"] == "claude"


def test_chat_omitted_provider_id_defaults_none(client):
    captured: dict = {}

    async def fake_chat(message, user_id, tenant_id, tier, *, thread_id=None, provider_id=None):
        captured["provider_id"] = provider_id
        return {"thread_id": str(uuid.uuid4()), "response": "ok", "model_used": "m", "cost_usd": 0.0}

    with patch.object(main, "handle_chat", side_effect=fake_chat):
        r = client.post("/chat", json={"message": "hi", "tier": "free"})
    assert r.status_code == 200
    assert captured["provider_id"] is None


@pytest.mark.asyncio
async def test_route_request_explicit_provider_calls_only_that_gateway(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("GOOGLE_API_KEY", "key")

    seen: list[str] = []

    async def fake_openai(cx, *, model, message, tenant_id):
        seen.append("openai")
        return "gpt-ok", 1, 1, 1

    async def fake_anthropic(cx, *, model, message, tenant_id):
        seen.append("anthropic")
        return "claude-ok", 1, 1, 1

    async def fake_google(cx, *, model, message, tenant_id):
        seen.append("google")
        return "gemini-ok", 1, 1, 1

    with (
        patch.object(get_gateway_provider("openai"), "send_message", side_effect=fake_openai),
        patch.object(get_gateway_provider("anthropic"), "send_message", side_effect=fake_anthropic),
        patch.object(get_gateway_provider("google"), "send_message", side_effect=fake_google),
    ):
        out = await route_request("hello", TENANT, "free", provider_id="gpt")
    assert seen == ["openai"]
    assert "gpt" in (out.get("model_used") or "") or out.get("content") == "gpt-ok"

    seen.clear()
    with (
        patch.object(get_gateway_provider("openai"), "send_message", side_effect=fake_openai),
        patch.object(get_gateway_provider("anthropic"), "send_message", side_effect=fake_anthropic),
        patch.object(get_gateway_provider("google"), "send_message", side_effect=fake_google),
    ):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
        out = await route_request("hello", TENANT, "free", provider_id="claude")
    assert seen == ["anthropic"]

    seen.clear()
    with (
        patch.object(get_gateway_provider("openai"), "send_message", side_effect=fake_openai),
        patch.object(get_gateway_provider("anthropic"), "send_message", side_effect=fake_anthropic),
        patch.object(get_gateway_provider("google"), "send_message", side_effect=fake_google),
    ):
        out = await route_request("hello", TENANT, "free", provider_id="gemini")
    assert seen == ["google"]


def test_council_body_rejects_provider_id(client):
    with patch.object(main, "run_council", new_callable=AsyncMock) as mock_council:
        mock_council.return_value = {
            "question": "q",
            "council": [],
            "synthesis": None,
            "cost_usd": 0.0,
            "room": {"id": "x", "question_id": "y", "status": "complete", "member_count": 0},
        }
        r = client.post("/council", json={"question": "q?", "provider_id": "gpt"})
    assert r.status_code == 422
