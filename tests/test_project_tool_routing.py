"""Conditional project workspace tool injection."""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:5432/test")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-openai")

from services.model_gateway import llm_tools_for_thread_session
from services.project_tool_router import conditional_project_tools, is_project_workspace_thread

THREAD = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def test_conditional_tools_none_for_standard_chat(monkeypatch):
    monkeypatch.setattr(
        "services.project_tool_router.get_thread_session_type",
        lambda _tid: "chat",
    )
    assert is_project_workspace_thread(THREAD) is False
    assert conditional_project_tools(thread_id=THREAD) is None
    assert llm_tools_for_thread_session(thread_id=THREAD) is None


def test_conditional_tools_present_for_project_workspace(monkeypatch):
    monkeypatch.setattr(
        "services.project_tool_router.get_thread_session_type",
        lambda _tid: "project_setup",
    )
    tools = conditional_project_tools(thread_id=THREAD, provider_id="gpt")
    assert tools is not None
    assert len(tools) >= 1
    assert tools[0]["function"]["name"] == "initialize_project_files"

    anthropic_tools = conditional_project_tools(thread_id=THREAD, provider_id="claude")
    assert anthropic_tools[0]["name"] == "initialize_project_files"
    assert "input_schema" in anthropic_tools[0]


@pytest.mark.asyncio
async def test_project_agent_emits_ben_log_on_tool_done(monkeypatch):
    from services.project_agent_service import stream_project_agent_response

    monkeypatch.setattr(
        "services.project_tool_router.get_thread_session_type",
        lambda _tid: "project_setup",
    )

    completion = {
        "choices": [
            {
                "message": {
                    "content": "Workspace is ready.",
                    "tool_calls": [],
                }
            }
        ]
    }

    tool_completion = {
        "choices": [
            {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "function": {
                                "name": "initialize_project_files",
                                "arguments": (
                                    '{"project_slug":"demo","architecture_markdown":"# A",'
                                    '"roadmap_markdown":"# R"}'
                                ),
                            },
                        }
                    ],
                }
            }
        ]
    }

    responses = [tool_completion, completion]

    async def fake_completion(**_kwargs):
        return responses.pop(0)

    log_id = uuid.uuid4()

    with patch(
        "services.project_agent_service._openai_chat_completion",
        side_effect=fake_completion,
    ), patch(
        "services.project_agent_service.execute_project_agent_tool",
        return_value='{"status":"ok","project_slug":"demo","files":["specs/architecture.md"]}',
    ), patch(
        "services.project_agent_service.append_event",
        new=AsyncMock(return_value=log_id),
    ) as mock_log:
        events = []
        async for line in stream_project_agent_response(
            user_message="Create the workspace",
            tenant_id="00000000-0000-0000-0000-000000000001",
            thread_id=uuid.UUID(THREAD),
        ):
            events.append(__import__("json").loads(line))

    tool_done = next(evt for evt in events if evt.get("type") == "tool_done")
    assert tool_done["ben_log_id"] == str(log_id)
    assert "log_summary" in tool_done
    mock_log.assert_awaited()
