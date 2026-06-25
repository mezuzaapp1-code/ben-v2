"""Rolling context pipeline — sequential turn accumulation for expert opinions."""
from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from database.models import Message
from services.message_format import encode_chat_assistant
from services.rolling_context import (
    DEFAULT_OPINION_REQUEST,
    build_rolling_context_prompt,
    build_rolling_stream_prompt,
)


def _assistant(text: str) -> str:
    return encode_chat_assistant(text, model_used="test-model", cost_usd=0.0, provider_id="gpt")


def test_build_rolling_context_prompt_appends_all_turns_sequentially():
    messages = [
        Message(role="user", content="What is the best approach?", org_id=uuid.uuid4(), thread_id=uuid.uuid4()),
        Message(role="assistant", content=_assistant("Start with a prototype."), org_id=uuid.uuid4(), thread_id=uuid.uuid4()),
        Message(role="user", content="Add another perspective.", org_id=uuid.uuid4(), thread_id=uuid.uuid4()),
        Message(role="assistant", content=_assistant("Consider scalability early."), org_id=uuid.uuid4(), thread_id=uuid.uuid4()),
    ]
    prompt = build_rolling_context_prompt(messages, opinion_request="Give your expert opinion.")
    assert prompt == (
        "What is the best approach?\n\n"
        "Start with a prototype.\n\n"
        "Add another perspective.\n\n"
        "Consider scalability early.\n\n"
        "Give your expert opinion."
    )


def test_build_rolling_context_prompt_uses_default_opinion_request():
    messages = [
        Message(role="user", content="Hello", org_id=uuid.uuid4(), thread_id=uuid.uuid4()),
    ]
    prompt = build_rolling_context_prompt(messages, opinion_request="")
    assert prompt.endswith(DEFAULT_OPINION_REQUEST)


@pytest.mark.asyncio
async def test_build_rolling_stream_prompt_loads_db_history():
    org = uuid.uuid4()
    tid = uuid.uuid4()
    history = [
        Message(role="user", content="Original question", org_id=org, thread_id=tid),
        Message(role="assistant", content=_assistant("First answer"), org_id=org, thread_id=tid),
    ]

    with patch("services.rolling_context._load_chat_history_messages", new=AsyncMock(return_value=history)):
        prompt = await build_rolling_stream_prompt(org, tid, "Latest opinion request")

    assert "Original question" in prompt
    assert "First answer" in prompt
    assert prompt.endswith("Latest opinion request")


@pytest.mark.asyncio
async def test_stream_chat_response_expert_opinion_uses_rolling_prompt(monkeypatch):
    from services.chat_service import stream_chat_response

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-openai")

    org = uuid.uuid4()
    tid = uuid.uuid4()
    rolling_prompt = "User Q\n\nAnswer\n\nOpinion please"
    stream_args: dict = {}

    async def fake_stream(message, tenant_id, tier, *, provider_id=None, model_override=None, system=None):
        stream_args["message"] = message
        stream_args["system"] = system
        yield ("Token", "model-x", "openai")

    async def passthrough_knowledge(_message, compiled_payload):
        return compiled_payload

    with (
        patch("services.chat_service.resolve_thread_id", new=AsyncMock(return_value=tid)),
        patch("services.chat_service.is_project_setup_thread", return_value=False),
        patch(
            "services.chat_service.build_rolling_stream_prompt",
            new=AsyncMock(return_value=rolling_prompt),
        ) as mock_rolling,
        patch("services.chat_service.inject_knowledge_few_shot", new=passthrough_knowledge),
        patch("services.chat_service.persist_chat_exchange_sqlite", return_value=(1, 2)),
        patch("services.chat_service.route_request_stream", new=fake_stream),
        patch("services.chat_service._schedule_chat_persist") as mock_persist,
    ):
        events = []
        async for line in stream_chat_response(
            "Opinion please",
            "user-1",
            str(org),
            "free",
            thread_id=tid,
            provider_id="gpt",
            expert_opinion=True,
        ):
            events.append(json.loads(line))

    assert events[0]["type"] == "meta"
    assert events[0]["mode"] == "rolling"
    mock_rolling.assert_awaited_once()
    assert stream_args["system"] is not None
    assert "Opinion please" in stream_args["message"]
    chunk_events = [e for e in events if e["type"] == "chunk"]
    assert chunk_events[0]["content"] == "Token"
    done = next(e for e in events if e["type"] == "done")
    assert done["response"] == "Token"
    assert done["mode"] == "rolling"
    mock_persist.assert_called_once()
