"""Clerk org required path when REQUIRE_ORG_FOR_SIGNED_IN=true (tenant mode v2 default: personal)."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:5432/test")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-openai")

import main
from auth.org_errors import CLERK_ORG_REQUIRED_CODE, clerk_org_required_detail, is_clerk_org_required_detail
from auth.tenant_binding import TenantContext, build_tenant_context, validate_body_tenant_matches_context
from auth.tenant_ids import personal_tenant_id
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
    monkeypatch.setenv("REQUIRE_ORG_FOR_SIGNED_IN", "false")


def test_clerk_org_required_detail_shape():
    d = clerk_org_required_detail()
    assert d["code"] == CLERK_ORG_REQUIRED_CODE
    assert d["recoverable"] is True
    assert "organization" in d["message"].lower()
    assert is_clerk_org_required_detail(d)


def test_build_tenant_context_signed_no_org_personal_by_default():
    ctx = build_tenant_context(
        "auth_valid",
        {"user_id": "user_1", "email": "a@b.com", "org_id": None},
        True,
    )
    assert ctx.tenant_type == "personal"
    assert ctx.tenant_id == personal_tenant_id("user_1")


def test_build_tenant_context_signed_no_org_raises_when_policy_on(monkeypatch):
    monkeypatch.setenv("REQUIRE_ORG_FOR_SIGNED_IN", "true")
    with pytest.raises(HTTPException) as exc:
        build_tenant_context(
            "auth_valid",
            {"user_id": "user_1", "email": "a@b.com", "org_id": None},
            True,
        )
    assert exc.value.status_code == 403
    assert is_clerk_org_required_detail(exc.value.detail)


def test_jwt_missing_org_returns_403_when_require_org(monkeypatch):
    monkeypatch.setenv("REQUIRE_ORG_FOR_SIGNED_IN", "true")

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
    assert r.status_code == 403
    body = r.json()
    assert body["detail"]["code"] == CLERK_ORG_REQUIRED_CODE
    assert body["detail"]["recoverable"] is True


def test_jwt_missing_org_council_returns_403_when_require_org(monkeypatch):
    monkeypatch.setenv("REQUIRE_ORG_FOR_SIGNED_IN", "true")

    def no_org(*_a, **_k):
        return {"sub": "usr", "email": "e@e.com", "org_id": None}

    with patch("auth.tenant_binding._clerk_verify_token", side_effect=no_org):
        with patch.object(main, "run_council", new_callable=AsyncMock):
            with TestClient(main.app) as client:
                r = client.post(
                    "/council",
                    json={"question": "hi"},
                    headers={"Authorization": "Bearer tok"},
                )
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == CLERK_ORG_REQUIRED_CODE


@pytest.mark.asyncio
async def test_unsigned_anonymous_chat_still_works(monkeypatch):
    captured: dict[str, str] = {}

    async def capture_chat(message, user_id, tenant_id, tier, thread_id=None):
        captured["tenant_id"] = tenant_id
        return {"thread_id": ANON, "response": "ok", "model_used": "m", "cost_usd": 0.0}

    with patch.object(main, "handle_chat", side_effect=capture_chat):
        with TestClient(main.app) as client:
            r = client.post("/chat", json={"message": "hi", "tier": "free"})
    assert r.status_code == 200
    assert captured["tenant_id"] == ANON


@pytest.mark.asyncio
async def test_signed_with_org_chat_uses_jwt_org(monkeypatch):
    captured: dict[str, str] = {}

    def with_org(*_a, **_k):
        return {"sub": "usr", "email": "e@e.com", "org_id": ORG_A}

    async def capture_chat(message, user_id, tenant_id, tier, thread_id=None):
        captured["tenant_id"] = tenant_id
        return {"thread_id": ORG_A, "response": "ok", "model_used": "m", "cost_usd": 0.0}

    with patch("auth.tenant_binding._clerk_verify_token", side_effect=with_org):
        with patch.object(main, "handle_chat", side_effect=capture_chat):
            with TestClient(main.app) as client:
                r = client.post(
                    "/chat",
                    json={"message": "hi", "tier": "free"},
                    headers={"Authorization": "Bearer tok"},
                )
    assert r.status_code == 200
    assert captured["tenant_id"] == ORG_A


def test_forged_body_tenant_still_rejected_with_org_jwt():
    ctx = TenantContext(
        tenant_id=ORG_A,
        tenant_type="organization",
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


def test_get_threads_missing_org_403_only_when_require_org(monkeypatch):
    monkeypatch.setenv("REQUIRE_ORG_FOR_SIGNED_IN", "true")

    def no_org(*_a, **_k):
        return {"sub": "usr", "email": "e@e.com", "org_id": None}

    with patch("auth.tenant_binding._clerk_verify_token", side_effect=no_org):
        with TestClient(main.app) as client:
            r = client.get("/api/threads", headers={"Authorization": "Bearer tok"})
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == CLERK_ORG_REQUIRED_CODE
