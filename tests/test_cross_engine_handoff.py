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
async def test_stream_chat_response_ignores_engine_names_in_message():
    """Engine selection is button-only: a message that names another engine must
    NOT reroute the primary chat turn. The provider stays the UI selection and the
    turn uses the standard chat context (no natural-language handoff)."""
    from services.chat_service import stream_chat_response

    org = uuid.uuid4()
    tid = uuid.uuid4()
    seen: dict = {}

    async def fake_stream(message, tenant_id, tier, provider_id=None, model_override=None, system=None):
        seen["provider_id"] = provider_id
        seen["model_override"] = model_override
        seen["system"] = system
        yield ("Gemini reply", "gemini-3.5-flash", "google")

    with (
        patch("services.chat_service.resolve_thread_id", new=AsyncMock(return_value=tid)),
        patch(
            "services.chat_service.build_chat_message_with_thread_context",
            new=AsyncMock(side_effect=lambda _o, _t, m: m),
        ),
        patch("services.chat_service.inject_knowledge_few_shot", new=AsyncMock(side_effect=lambda _m, p: p)),
        patch("services.chat_service.apply_language_context", side_effect=lambda msg, _lang: msg),
        patch("services.chat_service.route_request_stream", side_effect=fake_stream),
        patch("services.chat_service._schedule_chat_persist"),
    ):
        events = []
        async for line in stream_chat_response(
            "Hey Claude, please switch to GPT and continue",
            "user-1",
            str(org),
            "free",
            thread_id=tid,
            provider_id="gemini",
            model_override="gemini-3.5-flash",
        ):
            events.append(json.loads(line))

    # Provider dispatched to the model gateway is the button selection, untouched.
    assert seen["provider_id"] == "gemini"
    assert seen["model_override"] == "gemini-3.5-flash"
    # Standard chat turn: no cross-engine handoff system prompt.
    assert seen["system"] is None

    meta = events[0]
    assert meta["type"] == "meta"
    assert meta["mode"] == "chat"
    assert meta["provider_id"] == "gemini"
    done = next(e for e in events if e["type"] == "done")
    assert done["provider_id"] == "gemini"
    assert done["mode"] == "chat"
    assert done["response"] == "Gemini reply"
