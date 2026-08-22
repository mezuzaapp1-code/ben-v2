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
from services.model_gateway import (  # noqa: E402
    normalize_chat_provider_id,
    normalize_model_override,
    reset_circuit_breakers_for_tests,
    resolve_dispatch_model,
    route_request,
    route_request_stream,
    validate_chat_model_override,
)
from services.providers import get_gateway_provider  # noqa: E402
from services.providers.base_provider import ProviderSendResult  # noqa: E402
from services.ops.idempotency import reset_idempotency_registry_for_tests  # noqa: E402
from services.ops.load_governance import reset_load_governor_for_tests  # noqa: E402
from services.global_service_store import connect_global_channel, init_global_service_schema  # noqa: E402

TENANT = "00000000-0000-0000-0000-000000000001"

_ACTIVE_ENGINE_CATALOG = (
    ("engine-grok", "Grok Compute Grid"),
    ("engine-claude", "Claude Reasoning Core"),
    ("engine-gemini", "Gemini Multimodal"),
)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_clerk")
    monkeypatch.setenv("ENFORCE_AUTH", "false")
    monkeypatch.setenv("BEN_ANONYMOUS_ORG_ID", TENANT)
    reset_idempotency_registry_for_tests()
    reset_load_governor_for_tests()
    reset_circuit_breakers_for_tests()
    yield
    reset_circuit_breakers_for_tests()

@pytest.fixture(autouse=True)
def _active_speaking_engines(tmp_path, monkeypatch):
    """Seed org-scoped engine activations so chat routes pass the capability gate."""
    system_db = tmp_path / "system_main.db"
    monkeypatch.setenv("BEN_SYSTEM_DB_PATH", str(system_db))
    init_global_service_schema()
    for catalog_key, name in _ACTIVE_ENGINE_CATALOG:
        connect_global_channel(
            TENANT,
            name=name,
            source_type="external_library",
            source_metadata={"catalog_key": catalog_key},
        )


@pytest.fixture(autouse=True)
def _gate_a_customer():
    from tests.helpers_auth import patch_main_persistent_tenant

    with patch_main_persistent_tenant(TENANT):
        yield


@pytest.fixture
def client():
    return TestClient(main.app)


def test_normalize_chat_provider_id_valid():
    assert normalize_chat_provider_id("gpt") == "gpt"
    assert normalize_chat_provider_id(" Claude ") == "claude"
    assert normalize_chat_provider_id(" grok ") == "grok"
    assert normalize_chat_provider_id(None) is None
    assert normalize_chat_provider_id("") is None


def test_normalize_chat_provider_id_invalid():
    with pytest.raises(ValueError, match="claude, gemini, gpt, grok"):
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

    async def fake_chat(message, user_id, tenant_id, tier, *, thread_id=None, provider_id=None, model_override=None, preferred_language=None):
        captured["provider_id"] = provider_id
        return {"thread_id": str(uuid.uuid4()), "response": "ok", "model_used": "m", "cost_usd": 0.0}

    with patch.object(main, "handle_chat", side_effect=fake_chat):
        r = client.post("/chat", json={"message": "hi", "tier": "free", "provider_id": "claude"})
    assert r.status_code == 200
    assert captured["provider_id"] == "claude"


def test_chat_omitted_provider_id_defaults_none(client):
    captured: dict = {}

    async def fake_chat(message, user_id, tenant_id, tier, *, thread_id=None, provider_id=None, model_override=None, preferred_language=None):
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

    async def fake_openai(cx, *, model, message, tenant_id, system=None):
        seen.append("openai")
        return ProviderSendResult.from_token_counts("gpt-ok", 1, 1)

    async def fake_anthropic(cx, *, model, message, tenant_id, system=None):
        seen.append("anthropic")
        return ProviderSendResult.from_token_counts("claude-ok", 1, 1)

    async def fake_google(cx, *, model, message, tenant_id, system=None):
        seen.append("google")
        return ProviderSendResult.from_token_counts("gemini-ok", 1, 1)

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

    seen.clear()
    async def fake_xai(cx, *, model, message, tenant_id, system=None):
        seen.append("xai")
        return ProviderSendResult.from_token_counts("grok-ok", 1, 1)

    with (
        patch.object(get_gateway_provider("openai"), "send_message", side_effect=fake_openai),
        patch.object(get_gateway_provider("anthropic"), "send_message", side_effect=fake_anthropic),
        patch.object(get_gateway_provider("google"), "send_message", side_effect=fake_google),
        patch.object(get_gateway_provider("xai"), "send_message", side_effect=fake_xai),
    ):
        monkeypatch.setenv("XAI_API_KEY", "xai-test")
        out = await route_request("hello", TENANT, "free", provider_id="grok")
    assert seen == ["xai"]


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


def test_normalize_model_override():
    assert normalize_model_override(" gpt-4o ") == "gpt-4o"
    assert normalize_model_override(None) is None
    assert normalize_model_override("") is None


def test_chat_stream_passes_model_override(client):
    captured: dict = {}

    async def fake_stream(*args, **kwargs):
        captured.update(kwargs)
        yield '{"type":"done","response":"ok"}\n'

    with patch.object(main, "stream_chat_response", side_effect=fake_stream):
        r = client.post(
            "/chat/stream",
            json={
                "message": "hi",
                "tier": "free",
                "provider_id": "gpt",
                "model_override": "gpt-4o-mini",
            },
        )
    assert r.status_code == 200
    assert captured.get("model_override") == "gpt-4o-mini"
    assert captured.get("provider_id") == "gpt"


@pytest.mark.asyncio
async def test_route_request_stream_model_override_resolves_registry(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    seen: dict[str, str] = {}

    async def fake_openai(cx, *, model, message, tenant_id, system=None):
        seen["model"] = model
        yield "tok"

    with patch.object(get_gateway_provider("openai"), "stream_message", side_effect=fake_openai):
        chunks = []
        async for chunk, model, prov in route_request_stream(
            "hello",
            TENANT,
            "free",
            provider_id="gpt",
            model_override="gpt-4o",
        ):
            chunks.append((chunk, model, prov))
    assert seen["model"] == resolve_dispatch_model("openai", "gpt-4o")
    assert chunks[0][1] == "gpt-4o"


def test_validate_chat_model_override_rejects_unknown():
    with pytest.raises(ValueError, match="not registered"):
        validate_chat_model_override("claude", "claude-3-5-sonnet-latest")


def test_validate_chat_model_override_accepts_tier1():
    validate_chat_model_override("claude", "claude-opus-4.8")
    validate_chat_model_override("gemini", "gemini-3.5-flash")
    validate_chat_model_override("grok", "grok-4.6")
    validate_chat_model_override("grok", "grok-4.3")


def test_chat_stream_rejects_unknown_model_override(client):
    r = client.post(
        "/chat/stream",
        json={
            "message": "hi",
            "tier": "free",
            "provider_id": "gemini",
            "model_override": "gemini-1.5-pro",
        },
    )
    assert r.status_code == 400
    assert "not registered" in (r.json().get("detail") or "").lower()


def test_chat_allows_inactive_engine_via_execution_plan(client, tmp_path, monkeypatch):
  """Phase 1 — Switchboard inactive must not block chat before model gateway."""
  system_db = tmp_path / "empty_switchboard.db"
  monkeypatch.setenv("BEN_SYSTEM_DB_PATH", str(system_db))
  init_global_service_schema()

  with patch.object(main, "handle_chat", new_callable=AsyncMock) as handle_mock:
    handle_mock.return_value = {
      "thread_id": "t1",
      "response": "ok",
      "model_used": "m",
      "cost_usd": 0.0,
    }
    response = client.post(
      "/chat",
      json={"message": "hi", "tier": "free", "provider_id": "claude"},
    )

  assert response.status_code == 200
  handle_mock.assert_awaited()


def test_chat_stream_allows_nl_routed_inactive_engine_via_execution_plan(client, tmp_path, monkeypatch):
    """Phase 1 — stream must not 403 when NL routes to a Switchboard-inactive engine."""
    system_db = tmp_path / "gpt_only_switchboard.db"
    monkeypatch.setenv("BEN_SYSTEM_DB_PATH", str(system_db))
    init_global_service_schema()
    connect_global_channel(
        TENANT,
        name="Grok Compute Grid",
        source_type="external_library",
        source_metadata={"catalog_key": "engine-grok"},
    )

    async def _fake_stream(*_a, **_k):
        yield '{"type":"done"}\n'

    with patch.object(main, "stream_chat_response", new=_fake_stream):
        response = client.post(
            "/chat/stream",
            json={
                "message": "Hey Claude, summarize this thread",
                "tier": "free",
                "provider_id": "gpt",
            },
        )

    assert response.status_code == 200
    assert "CapabilityInactiveException" not in (response.text or "")
    assert "done" in response.text
