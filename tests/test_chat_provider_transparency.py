"""Provider identity survives chat API, persistence envelope, and rehydration."""
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
from services.message_format import (  # noqa: E402
    decode_message,
    encode_chat_assistant,
    gateway_to_provider_id,
    provider_display_label,
)
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
    yield


@pytest.fixture(autouse=True)
def _active_speaking_engines(tmp_path, monkeypatch):
    """Seed org-scoped engine activations so /chat passes ExecutionPlan enforcement."""
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


def test_gateway_to_provider_id_mapping():
    assert gateway_to_provider_id("openai") == "gpt"
    assert gateway_to_provider_id("anthropic") == "claude"
    assert gateway_to_provider_id("google") == "gemini"
    assert provider_display_label("claude") == "Claude"


def test_encode_decode_chat_provider_metadata():
    raw = encode_chat_assistant(
        "hi",
        model_used="claude-sonnet-4-6",
        cost_usd=0.01,
        provider_id="claude",
        provider_used="anthropic",
    )
    out = decode_message("assistant", raw)
    assert out["provider_id"] == "claude"
    assert out["provider_used"] == "anthropic"
    assert out["model_used"] == "claude-sonnet-4-6"


def test_decode_legacy_chat_without_provider_fields():
    raw = encode_chat_assistant("legacy", model_used="gpt-4o-mini", cost_usd=0.0)
    out = decode_message("assistant", raw)
    assert out.get("provider_id") == ""
    assert out["model_used"] == "gpt-4o-mini"


def test_chat_api_returns_provider_fields(client):
    async def fake_chat(
        message, user_id, tenant_id, tier, *,
        thread_id=None,
        provider_id=None,
        model_override=None,
        preferred_language=None,
        **_k,
    ):
        return {
            "thread_id": str(uuid.uuid4()),
            "response": "ok",
            "model_used": "gemini-2.5-flash",
            "cost_usd": 0.0,
            "provider_id": provider_id or "gemini",
            "provider_used": "google",
        }

    with patch.object(main, "handle_chat", side_effect=fake_chat):
        r = client.post("/chat", json={"message": "hi", "tier": "free", "provider_id": "gemini"})
    assert r.status_code == 200
    body = r.json()
    assert body["provider_id"] == "gemini"
    assert body["provider_used"] == "google"
    assert body["model_used"] == "gemini-2.5-flash"


def test_chat_invalid_provider_still_400(client):
    with patch.object(main, "handle_chat", new_callable=AsyncMock):
        r = client.post("/chat", json={"message": "hi", "tier": "free", "provider_id": "bad"})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_handle_chat_persists_provider_in_envelope(monkeypatch):
    from services.chat_service import handle_chat

    captured: dict = {}

    async def fake_route(message, tenant_id, tier, *, provider_id=None, model_override=None, system=None):
        return {
            "content": "answer",
            "model_used": "gpt-4o-mini",
            "provider_used": "openai",
            "cost_usd": 0.001,
        }

    async def fake_resolve(org, thread_id, *, title):
        return uuid.uuid4()

    class _Session:
        def add_all(self, rows):
            captured["assistant_content"] = rows[1].content

        async def execute(self, *a, **k):
            return None

        async def commit(self):
            return None

        async def flush(self):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

    monkeypatch.setattr("services.chat_service.route_request", fake_route)
    monkeypatch.setattr("services.chat_service.resolve_thread_id", fake_resolve)
    monkeypatch.setattr("services.chat_service.get_db_session", lambda: _Session())

    out = await handle_chat("q", "u", TENANT, "free", provider_id="gpt")
    assert out["provider_id"] == "gpt"
    assert out["provider_used"] == "openai"
    decoded = decode_message("assistant", captured["assistant_content"])
    assert decoded["provider_id"] == "gpt"
    assert decoded["provider_used"] == "openai"
