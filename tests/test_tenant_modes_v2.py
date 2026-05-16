"""Tenant mode v2: personal, organization, and anonymous workspaces."""
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
from auth.org_errors import CLERK_ORG_REQUIRED_CODE
from auth.tenant_binding import TenantContext, build_tenant_context, validate_body_tenant_matches_context
from auth.tenant_ids import personal_tenant_id
from main import ChatBody

ORG_A = "11111111-1111-1111-1111-111111111111"
ORG_B = "22222222-2222-2222-2222-222222222222"
ANON = "00000000-0000-0000-0000-000000000001"
USER_A = "user_clerk_aaa"


@pytest.fixture(autouse=True)
def _auth_env(monkeypatch):
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_clerk")
    monkeypatch.setenv("ENFORCE_AUTH", "false")
    monkeypatch.setenv("AUTH_SHADOW_MODE", "false")
    monkeypatch.setenv("BEN_ANONYMOUS_ORG_ID", ANON)
    monkeypatch.setenv("REQUIRE_ORG_FOR_SIGNED_IN", "false")


def test_signed_in_no_org_personal_tenant():
    ctx = build_tenant_context(
        "auth_valid",
        {"user_id": USER_A, "email": "a@b.com", "org_id": None},
        True,
    )
    assert ctx.tenant_type == "personal"
    assert ctx.org_id is None
    assert ctx.org_bound is False
    assert ctx.tenant_id == personal_tenant_id(USER_A)
    assert ctx.user_id == USER_A


def test_signed_in_with_org_organization_tenant():
    ctx = build_tenant_context(
        "auth_valid",
        {"user_id": USER_A, "email": "a@b.com", "org_id": ORG_A},
        True,
    )
    assert ctx.tenant_type == "organization"
    assert ctx.org_id == ORG_A
    assert ctx.tenant_id == ORG_A
    assert ctx.org_bound is True


def test_personal_tenants_isolated():
    ctx_a = build_tenant_context("auth_valid", {"user_id": "user_a", "org_id": None}, True)
    ctx_b = build_tenant_context("auth_valid", {"user_id": "user_b", "org_id": None}, True)
    assert ctx_a.tenant_id != ctx_b.tenant_id


def test_require_org_flag_blocks_no_org(monkeypatch):
    monkeypatch.setenv("REQUIRE_ORG_FOR_SIGNED_IN", "true")
    with pytest.raises(HTTPException) as exc:
        build_tenant_context("auth_valid", {"user_id": USER_A, "org_id": None}, True)
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == CLERK_ORG_REQUIRED_CODE


def test_jwt_no_org_chat_personal_200(monkeypatch):
    def no_org(*_a, **_k):
        return {"sub": USER_A, "email": "e@e.com", "org_id": None}

    captured: dict[str, str] = {}

    async def capture_chat(message, user_id, tenant_id, tier, thread_id=None):
        captured["tenant_id"] = tenant_id
        captured["user_id"] = user_id
        return {"thread_id": personal_tenant_id(USER_A), "response": "ok", "model_used": "m", "cost_usd": 0.0}

    with patch("auth.tenant_binding._clerk_verify_token", side_effect=no_org):
        with patch.object(main, "handle_chat", side_effect=capture_chat):
            with TestClient(main.app) as client:
                r = client.post(
                    "/chat",
                    json={"message": "hi", "tier": "free"},
                    headers={"Authorization": "Bearer tok"},
                )
    assert r.status_code == 200
    assert captured["tenant_id"] == personal_tenant_id(USER_A)
    assert captured["user_id"] == USER_A


def test_jwt_no_org_council_personal(monkeypatch):
    def no_org(*_a, **_k):
        return {"sub": USER_A, "email": "e@e.com", "org_id": None}

    captured: dict[str, str] = {}

    async def capture_run(question, tenant_id, *, thread_id=None):
        captured["tenant_id"] = tenant_id
        return {"question": question, "council": [], "synthesis": None, "cost_usd": 0.0}

    with patch("auth.tenant_binding._clerk_verify_token", side_effect=no_org):
        with patch.object(main, "run_council", side_effect=capture_run):
            with TestClient(main.app) as client:
                r = client.post(
                    "/council",
                    json={"question": "hi"},
                    headers={"Authorization": "Bearer tok"},
                )
    assert r.status_code == 200
    assert captured["tenant_id"] == personal_tenant_id(USER_A)


def test_get_threads_personal_tenant(monkeypatch):
    def no_org(*_a, **_k):
        return {"sub": USER_A, "email": "e@e.com", "org_id": None}

    with patch("auth.tenant_binding._clerk_verify_token", side_effect=no_org):
        with patch.object(main, "list_threads", new_callable=AsyncMock) as list_mock:
            list_mock.return_value = {"threads": [], "request_id": "x"}
            with TestClient(main.app) as client:
                r = client.get("/api/threads", headers={"Authorization": "Bearer tok"})
    assert r.status_code == 200
    list_mock.assert_awaited_once()
    assert str(list_mock.await_args[0][0]) == personal_tenant_id(USER_A)


def test_forged_body_rejected_personal(monkeypatch):
    ctx = build_tenant_context("auth_valid", {"user_id": USER_A, "org_id": None}, True)
    body = ChatBody(message="hi", tenant_id=ORG_B, tier="free")
    with pytest.raises(HTTPException) as exc:
        validate_body_tenant_matches_context(body, ctx)
    assert exc.value.status_code == 422


def test_health_tenant_mode_flags():
    with TestClient(main.app) as client:
        data = client.get("/health").json()
    checks = data.get("checks") or {}
    assert checks.get("tenant_modes_enabled") is True
    assert checks.get("require_org_for_signed_in") is False


def test_anonymous_unchanged():
    ctx = build_tenant_context("auth_missing", None, False)
    assert ctx.tenant_type == "anonymous"
    assert ctx.tenant_id == ANON
    assert ctx.org_id is None
