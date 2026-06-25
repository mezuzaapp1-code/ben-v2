"""Tests for cross-engine thread handoff context injection."""
from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from database.models import Message
from services.message_format import encode_chat_assistant
from services.thread_service import format_full_thread_history_for_handoff


def _assistant(text: str, *, provider_id: str) -> str:
    return encode_chat_assistant(text, model_used="test", cost_usd=0.0, provider_id=provider_id)


def test_format_full_thread_history_labels_providers():
    org = uuid.uuid4()
    tid = uuid.uuid4()
    messages = [
        Message(role="user", content="Question one", org_id=org, thread_id=tid),
        Message(
            role="assistant",
            content=_assistant("Gemini answer", provider_id="gemini"),
            org_id=org,
            thread_id=tid,
        ),
    ]
    history = format_full_thread_history_for_handoff(messages)
    assert "User: Question one" in history
    assert "Gemini: Gemini answer" in history


@pytest.mark.asyncio
async def test_stream_chat_response_cross_engine_uses_full_history():
    from services.chat_service import stream_chat_response

    org = uuid.uuid4()
    tid = uuid.uuid4()
    handoff_prompt = (
        "<conversation_history>\nUser: Q\n\nGemini: A\n</conversation_history>\n\n"
        "<user_message>\nHey Claude, continue\n</user_message>"
    )

    async def fake_stream(message, tenant_id, tier, provider_id=None, model_override=None, system=None):
        assert message == handoff_prompt
        assert system is not None
        yield ("Claude reply", "claude-opus-4.8", "anthropic")

    with (
        patch("services.chat_service.resolve_thread_id", new=AsyncMock(return_value=tid)),
        patch(
            "services.chat_service.build_cross_engine_thread_prompt",
            new=AsyncMock(return_value=handoff_prompt),
        ),
        patch("services.chat_service.inject_knowledge_few_shot", new=AsyncMock(side_effect=lambda _m, p: p)),
        patch("services.chat_service.apply_language_context", side_effect=lambda msg, _lang: msg),
        patch("services.chat_service.route_request_stream", side_effect=fake_stream),
        patch("services.chat_service._schedule_chat_persist"),
    ):
        events = []
        async for line in stream_chat_response(
            "Hey Claude, continue",
            "user-1",
            str(org),
            "free",
            thread_id=tid,
            provider_id="gemini",
            model_override="gemini-3.5-flash",
        ):
            events.append(json.loads(line))

    meta = events[0]
    assert meta["type"] == "meta"
    assert meta["mode"] == "handoff"
    assert meta["provider_id"] == "claude"
    done = next(e for e in events if e["type"] == "done")
    assert done["response"] == "Claude reply"
