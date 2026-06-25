"""Native tools REST endpoints — enforced auth and tenant-scoped org_id."""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:5432/test")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-openai")

import main

ORG_A = "11111111-1111-1111-1111-111111111111"
PROJECT_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_clerk")
    monkeypatch.setenv("ENFORCE_AUTH", "false")
    monkeypatch.setenv("AUTH_SHADOW_MODE", "true")


def _auth_headers():
    return {"Authorization": "Bearer test-token"}


def _patch_valid_jwt():
    claims = {"user_id": "user_1", "email": "a@b.com", "org_id": ORG_A}
    return patch(
        "auth.beta_gate.authenticate_request",
        return_value=("auth_valid", claims, True),
    )


def test_members_post_requires_auth():
    client = TestClient(main.app)
    res = client.post(
        f"/api/projects/{PROJECT_ID}/members",
        json={"name": "Ada", "member_type": "EMPLOYEE"},
    )
    assert res.status_code == 401


def test_tasks_post_requires_auth():
    client = TestClient(main.app)
    res = client.post(
        f"/api/projects/{PROJECT_ID}/tasks",
        json={"title": "Ship feature"},
    )
    assert res.status_code == 401


def test_ledger_post_requires_auth():
    client = TestClient(main.app)
    res = client.post(
        f"/api/projects/{PROJECT_ID}/ledger",
        json={"entry_type": "EXPENSE", "amount": 100.0},
    )
    assert res.status_code == 401


def test_add_member_passes_server_org_id():
    captured: list[uuid.UUID] = []

    async def _fake_add(org_id, project_id, **kwargs):
        captured.append(org_id)
        return {"id": str(uuid.uuid4()), "org_id": str(org_id), "name": kwargs["name"]}

    with _patch_valid_jwt(), patch("routers.projects.add_project_member", side_effect=_fake_add):
        client = TestClient(main.app)
        res = client.post(
            f"/api/projects/{PROJECT_ID}/members",
            json={"name": "Vendor Co", "member_type": "VENDOR", "hourly_rate": 120},
            headers=_auth_headers(),
        )

    assert res.status_code == 200
    assert captured == [uuid.UUID(ORG_A)]


def test_create_task_passes_server_org_id():
    captured: list[uuid.UUID] = []

    async def _fake_create(org_id, project_id, **kwargs):
        captured.append(org_id)
        return {"id": str(uuid.uuid4()), "org_id": str(org_id), "title": kwargs["title"]}

    with _patch_valid_jwt(), patch("routers.projects.create_project_task", side_effect=_fake_create):
        client = TestClient(main.app)
        res = client.post(
            f"/api/projects/{PROJECT_ID}/tasks",
            json={"title": "Allocate work", "status": "todo"},
            headers=_auth_headers(),
        )

    assert res.status_code == 200
    assert captured == [uuid.UUID(ORG_A)]


def test_create_ledger_passes_server_org_id():
    captured: list[uuid.UUID] = []

    async def _fake_create(org_id, project_id, **kwargs):
        captured.append(org_id)
        return {"id": str(uuid.uuid4()), "org_id": str(org_id), "amount": kwargs["amount"]}

    with _patch_valid_jwt(), patch("routers.projects.create_ledger_entry", side_effect=_fake_create):
        client = TestClient(main.app)
        res = client.post(
            f"/api/projects/{PROJECT_ID}/ledger",
            json={"entry_type": "INCOME", "amount": 5000, "currency": "USD"},
            headers=_auth_headers(),
        )

    assert res.status_code == 200
    assert captured == [uuid.UUID(ORG_A)]
