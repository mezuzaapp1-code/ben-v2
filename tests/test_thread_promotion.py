"""Thread promotion to portable project portfolios."""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:5432/test")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-openai")

from database import thread_store
from services.project_tool_router import conditional_project_tools, is_project_workspace_thread, resolve_project_thread_db_path

ORG = "00000000-0000-0000-0000-000000000001"
THREAD_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


@pytest.fixture()
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(thread_store, "_DATA_ROOT", tmp_path)
    monkeypatch.setattr(thread_store, "_SYSTEM_DB", tmp_path / "system_main.db")
    monkeypatch.setattr(thread_store, "_THREADS_DIR", tmp_path / "threads")
    from services import project_tools

    monkeypatch.setattr(project_tools, "_PROJECTS_ROOT", tmp_path / "projects")
    return tmp_path


@pytest.mark.asyncio
async def test_promote_thread_to_project_service(storage):
    from services.thread_service import promote_thread_to_project

    thread_id = uuid.UUID(THREAD_ID)
    org_id = uuid.UUID(ORG)
    thread_store.upsert_thread_metadata(thread_id=str(thread_id), org_id=str(org_id), title="Chat")
    thread_store.insert_thread_message(str(thread_id), role="user", content="hello")

    mock_row = MagicMock()
    mock_row.title = "Chat"

    with patch("services.thread_service.get_thread_for_org", new=AsyncMock(return_value=mock_row)), patch(
        "services.thread_service.is_project_setup_thread", return_value=False
    ):
        result = await promote_thread_to_project(org_id, thread_id, project_slug="basalt-hq")

    assert result["promoted"] is True
    assert result["thread"]["project_slug"] == "basalt-hq"
    assert is_project_workspace_thread(thread_id) is True
    assert "basalt-hq" in resolve_project_thread_db_path(thread_id)
    assert thread_store.list_thread_messages(str(thread_id))[0].content == "hello"


def test_conditional_tools_after_promotion(storage):
    thread_store.upsert_thread_metadata(
        thread_id=THREAD_ID,
        org_id=ORG,
        title="Promoted",
        session_type="project_setup",
        project_slug="demo",
    )
    tools = conditional_project_tools(thread_id=THREAD_ID, provider_id="gpt")
    assert tools is not None
    assert tools[0]["function"]["name"] == "initialize_project_files"
