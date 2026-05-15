"""Tenant binding v1: JWT-authoritative org; body tenant_id untrusted when unsigned."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:5432/test")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-openai")

import main
from auth.tenant_binding import TenantContext, build_tenant_context, validate_body_tenant_matches_context
from fastapi import HTTPException
from main import ChatBody


ORG_A = "11111111-1111-1111-1111-111111111111"
ORG_B = "22222222-2222-2222-2222-222222222222"
ANON = "00000000-0000-0000-0000-000000000001"


@pytest.fixture(autouse=True)
def _auth_env(monkeypatch):
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_clerk")
    monkeypatch.setenv("ENFORCE_AUTH", "false")
    monkeypatch.setenv("AUTH_SHADOW_MODE", "false")
    monkeypatch.setenv("BEN_ANONYMOUS_ORG_ID", ANON)


def test_build_tenant_context_jwt():
    ctx = build_tenant_context(
        "auth_valid",
        {"user_id": "user_1", "email": "a@b.com", "org_id": ORG_A},
        True,
    )
    assert ctx.org_id == ORG_A
    assert ctx.user_id == "user_1"
    assert ctx.email == "a@b.com"
    assert ctx.auth_source == "clerk_jwt"
    assert ctx.org_bound is True


def test_build_tenant_context_anonymous():
    ctx = build_tenant_context("auth_missing", None, False)
    assert ctx.org_id == ANON
    assert ctx.auth_source == "anonymous"
    assert ctx.org_bound is False


def test_forged_body_rejected_for_jwt():
    ctx = TenantContext(
        org_id=ORG_A,
        user_id="u",
        email=None,
        auth_source="clerk_jwt",
        auth_present=True,
        org_bound=True,
    )
    body = ChatBody(message="hi", tenant_id=ORG_B, tier="free")
    with pytest.raises(HTTPException) as exc:
        validate_body_tenant_matches_context(body, ctx)
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_unsigned_chat_ignores_body_tenant(monkeypatch):
    captured: dict[str, str] = {}

    async def capture_chat(message, user_id, tenant_id, tier, thread_id=None):
        captured["tenant_id"] = tenant_id
        captured["user_id"] = user_id
        return {
            "thread_id": ANON,
            "response": "ok",
            "model_used": "m",
            "cost_usd": 0.0,
        }

    monkeypatch.setenv("ENFORCE_AUTH", "false")
    with patch.object(main, "handle_chat", side_effect=capture_chat):
        with TestClient(main.app) as client:
            r = client.post(
                "/chat",
                json={"message": "hi", "tenant_id": ORG_B, "tier": "free"},
            )
    assert r.status_code == 200
    assert captured["tenant_id"] == ANON
    assert captured["user_id"] == "anonymous"


@pytest.mark.asyncio
async def test_signed_jwt_org_used_when_body_tenant_omitted(monkeypatch):
    captured: dict[str, str] = {}

    async def capture_chat(message, user_id, tenant_id, tier, thread_id=None):
        captured["tenant_id"] = tenant_id
        captured["user_id"] = user_id
        return {
            "thread_id": ORG_A,
            "response": "ok",
            "model_used": "m",
            "cost_usd": 0.0,
        }

    claims = {"sub": "usr", "email": None, "org_id": ORG_A}

    def fake_verify(*_a, **_k):
        return {
            "sub": claims["sub"],
            "email": claims["email"],
            "org_id": ORG_A,
        }

    with patch("auth.tenant_binding._clerk_verify_token", side_effect=fake_verify):
        with patch.object(main, "handle_chat", side_effect=capture_chat):
            with TestClient(main.app) as client:
                r = client.post(
                    "/chat",
                    json={"message": "hi", "tier": "free"},
                    headers={"Authorization": "Bearer fake.jwt.token"},
                )
    assert r.status_code == 200
    assert captured["tenant_id"] == ORG_A
    assert captured["user_id"] == "usr"


@pytest.mark.asyncio
async def test_signed_jwt_forged_body_tenant_returns_422(monkeypatch):
    def fake_verify(*_a, **_k):
        return {"sub": "usr", "email": None, "org_id": ORG_A}

    with patch("auth.tenant_binding._clerk_verify_token", side_effect=fake_verify):
        with patch.object(main, "handle_chat", new_callable=AsyncMock):
            with TestClient(main.app) as client:
                r = client.post(
                    "/chat",
                    json={"message": "hi", "tenant_id": ORG_B, "tier": "free"},
                    headers={"Authorization": "Bearer fake.jwt.token"},
                )
    assert r.status_code == 422
    assert "match" in (r.json().get("detail") or "").lower()


def test_chat_rejects_unknown_json_fields():
    with patch.object(main, "handle_chat", new_callable=AsyncMock):
        with TestClient(main.app) as client:
            r = client.post(
                "/chat",
                json={"message": "hi", "tier": "free", "org_id": ORG_B},
            )
    assert r.status_code == 422


def test_enforce_auth_invalid_jwt_401(monkeypatch):
    monkeypatch.setenv("ENFORCE_AUTH", "true")

    def bad_verify(*_a, **_k):
        from clerk_backend_api.security.types import TokenVerificationError

        raise TokenVerificationError("bad")

    with patch("auth.tenant_binding._clerk_verify_token", side_effect=bad_verify):
        with patch.object(main, "handle_chat", new_callable=AsyncMock):
            with TestClient(main.app) as client:
                r = client.post(
                    "/chat",
                    json={"message": "hi", "tier": "free"},
                    headers={"Authorization": "Bearer bad"},
                )
    assert r.status_code == 401


def test_jwt_missing_org_returns_400(monkeypatch):
    def no_org(*_a, **_k):
        return {"sub": "usr", "email": "e@e.com", "org_id": None}

    with patch("auth.tenant_binding._clerk_verify_token", side_effect=no_org):
        with patch.object(main, "handle_chat", new_callable=AsyncMock):
            with TestClient(main.app) as client:
                r = client.post(
                    "/chat",
                    json={"message": "hi", "tier": "free"},
                    headers={"Authorization": "Bearer tok"},
                )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_council_passes_bound_org(monkeypatch):
    captured: dict[str, str] = {}

    async def capture_run(question, tenant_id):
        captured["org"] = tenant_id
        return {
            "question": question,
            "council": [],
            "synthesis": None,
            "cost_usd": 0.0,
        }

    with patch.object(main, "run_council", side_effect=capture_run):
        with TestClient(main.app) as client:
            r = client.post("/council", json={"question": "q?"})
    assert r.status_code == 200
    assert captured["org"] == ANON


def test_health_includes_tenant_binding_flags():
    with TestClient(main.app) as client:
        r = client.get("/health")
    data = r.json()
    checks = data.get("checks") or {}
    assert checks.get("tenant_binding_enabled") is True
    assert checks.get("enforce_auth") is False
