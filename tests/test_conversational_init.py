"""Conversational project init route — JIT schema provisioning."""
from __future__ import annotations

import os
import sqlite3
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
from services.project_tools import projects_root, slugify_project_name

BETA_CODE = "basalt-closed-beta-2026"


@pytest.fixture(autouse=True)
def _beta_env(monkeypatch, tmp_path):
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_clerk")
    monkeypatch.setenv("ENFORCE_AUTH", "false")
    monkeypatch.setenv("AUTH_SHADOW_MODE", "true")
    monkeypatch.setenv("BEN_LOCAL_BETA_MODE", "true")
    monkeypatch.setenv("BEN_BETA_PASSCODE", BETA_CODE)
    monkeypatch.setenv("BEN_PROJECTS_DATA_DIR", str(tmp_path / "projects"))


def _beta_headers(alias: str) -> dict[str, str]:
    return {
        "X-Basalt-Beta-Passcode": BETA_CODE,
        "X-Basalt-Beta-Alias": alias,
    }


@pytest.mark.asyncio
async def test_conversational_init_returns_schema_blueprint():
    project_id = uuid.uuid4()

    async def _fake_create(org_id: uuid.UUID, *, name, description=None, status="active"):
        return {
            "id": str(project_id),
            "org_id": str(org_id),
            "name": name,
            "description": description,
            "status": status,
        }

    with patch("routers.projects.create_project", side_effect=_fake_create), patch(
        "routers.projects.initialize_project_setup", new_callable=AsyncMock
    ), patch("routers.projects._seed_initial_tactical_tasks", new_callable=AsyncMock), patch(
        "routers.projects.load_project_memory", new_callable=AsyncMock, return_value={}
    ), patch(
        "routers.projects.save_project_memory", new_callable=AsyncMock
    ):
        client = TestClient(main.app)
        response = client.post(
            "/api/projects/conversational-init",
            json={
                "name": "Field Ops Tracker",
                "software_description": (
                    "Table: field_logs with columns id integer PRIMARY KEY, crew text, notes text"
                ),
            },
            headers=_beta_headers("Alon"),
        )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(project_id)
    assert body["project_slug"] == slugify_project_name("Field Ops Tracker")
    assert body["tables_created"] >= 1
    assert body["schema_blueprint"][0]["name"] == "field_logs"

    db_path = projects_root() / body["project_slug"] / "project_context.db"
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='field_logs'"
        ).fetchone()


@pytest.mark.asyncio
async def test_conversational_init_rejects_invalid_schema():
    async def _fake_create(org_id: uuid.UUID, *, name, description=None, status="active"):
        return {"id": str(uuid.uuid4()), "org_id": str(org_id), "name": name}

    with patch("routers.projects.create_project", side_effect=_fake_create), patch(
        "routers.projects.initialize_project_setup", new_callable=AsyncMock
    ), patch("routers.projects._seed_initial_tactical_tasks", new_callable=AsyncMock):
        client = TestClient(main.app)
        response = client.post(
            "/api/projects/conversational-init",
            json={
                "name": "Unsafe",
                "software_description": "noop",
                "schema_tables": [
                    {
                        "name": "bad;drop",
                        "columns": [{"name": "id", "data_type": "integer", "primary_key": True}],
                    }
                ],
            },
            headers=_beta_headers("Alon"),
        )

    assert response.status_code == 422
