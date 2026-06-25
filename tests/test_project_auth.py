"""Project routes require enforced Clerk auth regardless of global ENFORCE_AUTH."""
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

ORG_A = "11111111-1111-1111-1111-111111111111"


@pytest.fixture(autouse=True)
def _project_auth_env(monkeypatch):
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_clerk")
    monkeypatch.setenv("ENFORCE_AUTH", "false")
    monkeypatch.setenv("AUTH_SHADOW_MODE", "true")
    monkeypatch.setenv("BEN_ANONYMOUS_ORG_ID", "00000000-0000-0000-0000-000000000001")


def test_projects_list_requires_auth_even_when_global_enforce_off():
    client = TestClient(main.app)
    res = client.get("/api/projects")
    assert res.status_code == 401


def test_projects_create_requires_auth_even_when_global_enforce_off():
    client = TestClient(main.app)
    res = client.post("/api/projects", json={"name": "Alpha"})
    assert res.status_code == 401


def test_projects_create_rejects_forged_tenant_id():
    client = TestClient(main.app)
    claims = {"user_id": "user_1", "email": "a@b.com", "org_id": ORG_A}

    with patch(
        "auth.beta_gate.authenticate_request",
        return_value=("auth_valid", claims, True),
    ):
        res = client.post(
            "/api/projects",
            json={
                "name": "Alpha",
                "tenant_id": "22222222-2222-2222-2222-222222222222",
            },
            headers={"Authorization": "Bearer test-token"},
        )
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_projects_create_uses_server_tenant_id(monkeypatch):
    created_org: list[uuid.UUID] = []

    async def _fake_create(org_id: uuid.UUID, **kwargs):
        created_org.append(org_id)
        return {"id": str(uuid.uuid4()), "org_id": str(org_id), "name": kwargs["name"]}

    claims = {"user_id": "user_1", "email": "a@b.com", "org_id": ORG_A, "org_role": "org:admin"}

    with patch(
        "auth.beta_gate.authenticate_request",
        return_value=("auth_valid", claims, True),
    ), patch("routers.projects.create_project", side_effect=_fake_create), patch(
        "routers.projects.initialize_project_setup", new_callable=AsyncMock
    ):
        client = TestClient(main.app)
        res = client.post(
            "/api/projects",
            json={"name": "Scoped Project"},
            headers={"Authorization": "Bearer test-token"},
        )

    assert res.status_code == 200
    assert created_org == [uuid.UUID(ORG_A)]


def test_projects_create_rejects_non_admin_org_member():
    claims = {"user_id": "user_1", "email": "a@b.com", "org_id": ORG_A, "org_role": "org:member"}

    with patch(
        "auth.beta_gate.authenticate_request",
        return_value=("auth_valid", claims, True),
    ):
        client = TestClient(main.app)
        res = client.post(
            "/api/projects",
            json={"name": "Blocked Project"},
            headers={"Authorization": "Bearer test-token"},
        )

    assert res.status_code == 403
