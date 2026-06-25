"""Passive stream performance metrics on /chat/stream."""
from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from services.chat_service import _finalize_stream_perf, stream_chat_response


def test_finalize_stream_perf_computes_ttft_and_tps():
    metrics = _finalize_stream_perf(
        stream_started=100.0,
        first_token_at=100.14,
        last_token_at=100.64,
        output_text="word " * 20,
    )
    assert metrics["ttft_ms"] == 140.0
    assert metrics["tps"] > 0


def test_finalize_stream_perf_empty_when_no_tokens():
    assert _finalize_stream_perf(
        stream_started=1.0,
        first_token_at=None,
        last_token_at=None,
        output_text="",
    ) == {}


@pytest.mark.asyncio
async def test_stream_chat_response_done_includes_perf_metrics():
    org = uuid.uuid4()
    tid = uuid.uuid4()

    async def fake_stream(*_args, **_kwargs):
        yield ("Hello", "gpt-4o", "openai")
        yield (" world", "gpt-4o", "openai")

    with (
        patch("services.chat_service.resolve_thread_id", new=AsyncMock(return_value=tid)),
        patch(
            "services.chat_service.build_chat_message_with_thread_context",
            new=AsyncMock(return_value="hello"),
        ),
        patch("services.chat_service.route_request_stream", side_effect=fake_stream),
        patch("services.chat_service._schedule_chat_persist"),
    ):
        events = []
        async for line in stream_chat_response("hello", "user-1", str(org), "free", thread_id=tid, provider_id="gpt"):
            events.append(json.loads(line))

    done = next(e for e in events if e["type"] == "done")
    assert done["response"] == "Hello world"
    assert "ttft_ms" in done
    assert done["ttft_ms"] >= 0
    assert "tps" in done
    assert done["tps"] > 0

    chunk_count = sum(1 for e in events if e["type"] == "chunk")
    assert chunk_count == 2
