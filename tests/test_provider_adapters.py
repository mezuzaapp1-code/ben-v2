"""Provider adapter layer smoke tests."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.providers import get_gateway_provider
from services.providers.anthropic_provider import AnthropicProvider
from services.providers.base_provider import BaseProvider
from services.providers.gemini_provider import GeminiProvider
from services.providers.openai_provider import OpenAIProvider


def test_gateway_provider_registry():
    assert get_gateway_provider("openai").provider_name == "openai"
    assert get_gateway_provider("anthropic").provider_name == "anthropic"
    assert get_gateway_provider("google").provider_name == "google"


def test_adapter_types():
    assert isinstance(OpenAIProvider(), BaseProvider)
    assert isinstance(AnthropicProvider(), BaseProvider)
    assert isinstance(GeminiProvider(), BaseProvider)


@pytest.mark.asyncio
async def test_stream_message_yields_single_chunk(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    class _FakeStreamResponse:
        def raise_for_status(self):
            return None

        async def aiter_lines(self):
            yield 'data: {"choices":[{"delta":{"content":"hello"}}]}'
            yield "data: [DONE]"

    class _FakeStreamContext:
        async def __aenter__(self):
            return _FakeStreamResponse()

        async def __aexit__(self, *_args):
            return None

    class _FakeAsyncClient:
        def stream(self, *_args, **_kwargs):
            return _FakeStreamContext()

    adapter = OpenAIProvider()
    chunks = [
        c
        async for c in adapter.stream_message(
            _FakeAsyncClient(),  # type: ignore[arg-type]
            model="m",
            message="x",
            tenant_id="t",
        )
    ]
    assert chunks == ["hello"]
