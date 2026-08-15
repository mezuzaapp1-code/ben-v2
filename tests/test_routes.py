"""HTTP route integration tests — thread deletion cascade."""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:5432/test")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-openai")

import main
from services.thread_service import delete_thread

ORG = "00000000-0000-0000-0000-000000000001"
THREAD_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


@pytest.fixture(autouse=True)
def _auth_env(monkeypatch):
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_clerk")
    monkeypatch.setenv("ENFORCE_AUTH", "false")
    monkeypatch.setenv("AUTH_SHADOW_MODE", "false")
    monkeypatch.setenv("BEN_ANONYMOUS_ORG_ID", ORG)


@pytest.fixture(autouse=True)
def _gate_a_customer():
    from tests.helpers_auth import patch_main_persistent_tenant

    with patch_main_persistent_tenant(ORG):
        yield


def test_delete_thread_route_returns_200():
    payload = {
        "deleted": True,
        "thread_id": THREAD_ID,
        "project_slug": "demo-project",
        "project_folder_removed": True,
    }
    with patch("main.delete_thread", new=AsyncMock(return_value=payload)):
        with TestClient(main.app) as client:
            r = client.delete(f"/api/threads/{THREAD_ID}")
    assert r.status_code == 200
    body = r.json()
    assert body["deleted"] is True
    assert body["thread_id"] == THREAD_ID
    assert body["project_folder_removed"] is True


def test_delete_thread_route_invalid_id_422():
    with TestClient(main.app) as client:
        r = client.delete("/api/threads/not-a-uuid")
    assert r.status_code == 422


def test_delete_thread_route_not_found_404():
    with patch(
        "main.delete_thread",
        new=AsyncMock(side_effect=HTTPException(status_code=404, detail="Thread not found")),
    ):
        with TestClient(main.app) as client:
            r = client.delete(f"/api/threads/{THREAD_ID}")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_thread_service_cascade():
    org_id = uuid.UUID(ORG)
    thread_id = uuid.UUID(THREAD_ID)

    mock_row = MagicMock()
    mock_row.title = "Demo Project"

    mock_pg = MagicMock()
    mock_pg.org_id = org_id

    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=mock_pg)
    mock_session.delete = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_session.execute = AsyncMock()

    mock_slug_path = MagicMock()
    mock_slug_path.exists.return_value = True
    mock_projects_root = MagicMock()
    mock_projects_root.__truediv__ = MagicMock(return_value=mock_slug_path)

    with patch("services.thread_service.get_thread_for_org", new=AsyncMock(return_value=mock_row)), patch(
        "services.thread_service.get_thread_project_slug", return_value="demo-project"
    ), patch("services.thread_service.get_db_session") as mock_sess, patch(
        "services.thread_service.release_thread_database_files"
    ) as mock_release, patch("services.thread_service.delete_thread_metadata") as mock_meta, patch(
        "services.project_tools.delete_project_directory"
    ) as mock_folder, patch(
        "services.project_tools.projects_root", return_value=mock_projects_root
    ):
        mock_sess.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_sess.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await delete_thread(org_id, thread_id)

    assert result["deleted"] is True
    assert result["thread_id"] == str(thread_id)
    assert result["project_slug"] == "demo-project"
    assert result["project_folder_removed"] is True

    mock_session.delete.assert_awaited_once_with(mock_pg)
    mock_session.commit.assert_awaited_once()
    mock_release.assert_called_once_with(str(thread_id))
    mock_meta.assert_called_once_with(str(thread_id), str(org_id))
    mock_folder.assert_called_once_with("demo-project")


def test_promote_thread_route_returns_200():
    payload = {
        "promoted": True,
        "thread": {
            "thread_id": THREAD_ID,
            "project_slug": "basalt-hq",
            "session_type": "project_setup",
        },
    }
    with patch("main.promote_thread_to_project", new=AsyncMock(return_value=payload)):
        with TestClient(main.app) as client:
            r = client.post(
                f"/api/threads/{THREAD_ID}/promote",
                json={"project_slug": "basalt-hq"},
            )
    assert r.status_code == 200
    assert r.json()["promoted"] is True
    assert r.json()["thread"]["project_slug"] == "basalt-hq"
