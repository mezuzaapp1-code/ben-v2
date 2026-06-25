"""Closed-beta passcode access and auditor sandbox isolation (Task 010)."""
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
from auth.beta_gate import derive_beta_org_id

BETA_CODE = "basalt-closed-beta-2026"


@pytest.fixture(autouse=True)
def _beta_env(monkeypatch):
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_clerk")
    monkeypatch.setenv("ENFORCE_AUTH", "false")
    monkeypatch.setenv("AUTH_SHADOW_MODE", "true")
    monkeypatch.setenv("BEN_LOCAL_BETA_MODE", "true")
    monkeypatch.setenv("BEN_BETA_PASSCODE", BETA_CODE)


def _beta_headers(alias: str) -> dict[str, str]:
    return {
        "X-Basalt-Beta-Passcode": BETA_CODE,
        "X-Basalt-Beta-Alias": alias,
    }


def test_derive_beta_org_id_isolated_per_alias():
    alon = derive_beta_org_id("Alon")
    dana = derive_beta_org_id("Dana")
    assert alon != dana
    assert derive_beta_org_id("alon") == derive_beta_org_id("Alon")


def test_beta_session_resolve_returns_org_id():
    client = TestClient(main.app)
    res = client.post(
        "/api/beta/session",
        json={"passcode": BETA_CODE, "alias": "Alon"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["alias"] == "Alon"
    assert data["org_id"] == str(derive_beta_org_id("Alon"))


def test_projects_create_rejects_missing_alias():
    client = TestClient(main.app)
    res = client.post(
        "/api/projects",
        json={"name": "Beta Blocked"},
        headers={"X-Basalt-Beta-Passcode": BETA_CODE},
    )
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_projects_create_scoped_to_alias_org():
    created_org: list[uuid.UUID] = []

    async def _fake_create(org_id: uuid.UUID, **kwargs):
        created_org.append(org_id)
        return {"id": str(uuid.uuid4()), "org_id": str(org_id), "name": kwargs["name"]}

    with patch("routers.projects.create_project", side_effect=_fake_create), patch(
        "routers.projects.initialize_project_setup", new_callable=AsyncMock
    ):
        client = TestClient(main.app)
        res = client.post(
            "/api/projects",
            json={"name": "Alon Project", "location_base": "Or Akiva"},
            headers=_beta_headers("Alon"),
        )

    assert res.status_code == 200
    assert created_org == [derive_beta_org_id("Alon")]


@pytest.mark.asyncio
async def test_projects_isolated_between_aliases():
    created: dict[str, uuid.UUID] = {}

    async def _fake_create(org_id: uuid.UUID, **kwargs):
        created[kwargs["name"]] = org_id
        return {"id": str(uuid.uuid4()), "org_id": str(org_id), "name": kwargs["name"]}

    with patch("routers.projects.create_project", side_effect=_fake_create), patch(
        "routers.projects.initialize_project_setup", new_callable=AsyncMock
    ):
        client = TestClient(main.app)
        res_a = client.post(
            "/api/projects",
            json={"name": "Sandbox A"},
            headers=_beta_headers("Alon"),
        )
        res_b = client.post(
            "/api/projects",
            json={"name": "Sandbox B"},
            headers=_beta_headers("Dana"),
        )

    assert res_a.status_code == 200
    assert res_b.status_code == 200
    assert created["Sandbox A"] != created["Sandbox B"]


def test_chat_feedback_capture_writes_json(tmp_path, monkeypatch):
    monkeypatch.setattr("services.feedback_capture_service.FEEDBACK_DIR", tmp_path)

    async def _fake_stream(*_args, **_kwargs):
        yield '{"type":"done"}\n'

    client = TestClient(main.app)
    with patch("main.stream_chat_response", _fake_stream):
        res = client.post(
            "/chat/stream",
            json={"message": "Bug: project modal stuck on sign in"},
            headers={
                **_beta_headers("Alon"),
                "X-Basalt-Beta-Theme": "dark",
                "X-Basalt-Beta-Project-Name": "Server Farm",
            },
        )
    assert res.status_code == 200
    files = list(tmp_path.glob("feedback_alon_*.json"))
    assert len(files) == 1
    payload = files[0].read_text(encoding="utf-8")
    assert "Bug: project modal stuck" in payload
    assert "Alon" in payload
    assert "Server Farm" in payload
    assert "dark" in payload
