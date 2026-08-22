"""XAIProvider V1 — isolated adapter, search off, streaming, Large Paste canary."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:5432/test")

from services.inference.execution_context import begin_execution_context, set_execution_context
from services.inference.usage_normalize import normalize_openai_usage
from services.providers import get_gateway_provider, gateway_provider_api_key_env
from services.providers.base_provider import ProviderStreamEnd
from services.providers.xai_provider import (
    XAI_CHAT_COMPLETIONS_URL,
    XAI_FAST_MODEL,
    XAI_FLAGSHIP_MODEL,
    XAIProvider,
)


def test_xai_gateway_registered():
    adapter = get_gateway_provider("xai")
    assert adapter.provider_name == "xai"
    assert isinstance(adapter, XAIProvider)
    assert gateway_provider_api_key_env("xai") == "XAI_API_KEY"


def test_xai_request_body_disables_search_and_omits_tools():
    body = XAIProvider()._json_body(XAI_FLAGSHIP_MODEL, "hello", None, stream=True)
    assert body["model"] == XAI_FLAGSHIP_MODEL
    assert body["search_parameters"] == {"mode": "off"}
    assert "tools" not in body
    assert "tool_choice" not in body
    assert body["stream"] is True
    assert body["stream_options"] == {"include_usage": True}


def test_xai_request_body_uses_caller_model():
    body = XAIProvider()._json_body(XAI_FAST_MODEL, "hi", "sys", stream=False)
    assert body["model"] == XAI_FAST_MODEL
    assert body["search_parameters"] == {"mode": "off"}
    assert "stream" not in body


@pytest.mark.asyncio
async def test_xai_stream_normalizes_openai_sse(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "xai-test-key-not-a-secret")
    captured: dict = {}

    class _FakeStreamResponse:
        def raise_for_status(self):
            return None

        async def aiter_lines(self):
            yield (
                'data: {"id":"cmpl-x","choices":[{"delta":{"content":"Hello "}}],'
                '"usage":null}'
            )
            yield (
                'data: {"id":"cmpl-x","choices":[{"delta":{"content":"Grok"},'
                '"finish_reason":"stop"}],'
                '"usage":{"prompt_tokens":9,"completion_tokens":2,"total_tokens":11}}'
            )
            yield "data: [DONE]"

    class _FakeStreamContext:
        async def __aenter__(self):
            return _FakeStreamResponse()

        async def __aexit__(self, *_args):
            return None

    class _FakeAsyncClient:
        def stream(self, method, url, *, headers, json):
            captured["method"] = method
            captured["url"] = url
            captured["json"] = json
            captured["auth_prefix"] = str(headers.get("Authorization", "")).split(" ", 1)[0]
            return _FakeStreamContext()

    chunks = [
        item
        async for item in XAIProvider().stream_message(
            _FakeAsyncClient(),  # type: ignore[arg-type]
            model=XAI_FLAGSHIP_MODEL,
            message="ping",
            tenant_id="t",
        )
    ]
    assert captured["method"] == "POST"
    assert captured["url"] == XAI_CHAT_COMPLETIONS_URL
    assert captured["json"]["model"] == XAI_FLAGSHIP_MODEL
    assert captured["json"]["search_parameters"] == {"mode": "off"}
    assert "tools" not in captured["json"]
    assert captured["auth_prefix"] == "Bearer"
    assert chunks[0] == "Hello "
    assert chunks[1] == "Grok"
    assert isinstance(chunks[2], ProviderStreamEnd)
    assert chunks[2].usage.usage_status == "exact"
    assert chunks[2].usage.input_tokens == 9
    assert chunks[2].usage.output_tokens == 2
    assert chunks[2].finish_reason == "stop"
    assert chunks[2].provider_request_id == "cmpl-x"


def test_normalize_openai_usage_reads_xai_shaped_fields():
    usage = normalize_openai_usage(
        {
            "prompt_tokens": 41,
            "completion_tokens": 2,
            "total_tokens": 43,
            "prompt_tokens_details": {"cached_tokens": 4},
        }
    )
    assert usage.input_tokens == 41
    assert usage.output_tokens == 2
    assert usage.cached_input_tokens == 4


@pytest.mark.asyncio
async def test_large_paste_canary_reaches_xai_adapter():
    from services.chat_service import stream_chat_response
    from services.message_format import encode_user_turn
    import uuid

    canary = "BEN-GROK-LP-END-92741"
    paste = "BODY-" + ("ק" * 12_000) + "\n" + canary
    assert len(paste) > 10_000
    encoded = encode_user_turn(
        [
            {"type": "text", "text": "Review:\n"},
            {
                "type": "large_paste",
                "id": "grok-lp",
                "label": "Pasted text",
                "text": paste,
                "char_count": len(paste),
            },
            {"type": "text", "text": "\nThanks"},
        ]
    )
    seen: dict = {}

    async def fake_stream(cx, *, model, message, tenant_id, system=None):
        seen["model"] = model
        seen["message"] = message
        yield "ok"
        yield ProviderStreamEnd(
            usage=normalize_openai_usage(
                {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}
            )
        )

    org = uuid.uuid4()
    tid = uuid.uuid4()
    begin_execution_context(org_id=str(org), workspace_id=None, pipeline="chat")
    try:
        with (
            patch("services.chat_service.resolve_thread_id", new=AsyncMock(return_value=tid)),
            patch("services.chat_service.is_project_setup_thread", return_value=False),
            patch(
                "services.chat_service.build_chat_message_with_thread_context",
                new=AsyncMock(side_effect=lambda _o, _t, m: m),
            ),
            patch(
                "services.chat_service.inject_knowledge_few_shot",
                new=AsyncMock(side_effect=lambda _m, p: p),
            ),
            patch("services.chat_service.apply_language_context", side_effect=lambda msg, _lang: msg),
            patch.object(get_gateway_provider("xai"), "stream_message", side_effect=fake_stream),
            patch("services.chat_service.persist_chat_exchange_sqlite", return_value=(1, 2)),
            patch("services.chat_service._schedule_chat_persist"),
            patch.dict(os.environ, {"XAI_API_KEY": "xai-test-key-not-a-secret"}, clear=False),
        ):
            events = []
            async for line in stream_chat_response(
                encoded,
                "user-1",
                str(org),
                "free",
                thread_id=tid,
                provider_id="grok",
                model_override=XAI_FLAGSHIP_MODEL,
            ):
                events.append(json.loads(line))
    finally:
        set_execution_context(None)

    assert canary in seen["message"]
    assert paste in seen["message"]
    assert seen["message"].startswith("Review:\n")
    assert seen["message"].endswith("\nThanks")
    assert seen["model"] == XAI_FLAGSHIP_MODEL
    assert any(e.get("type") == "done" for e in events)
