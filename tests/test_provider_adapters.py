"""Provider adapter layer smoke tests."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.providers import get_gateway_provider
from services.providers.anthropic_provider import AnthropicProvider
from services.providers.base_provider import BaseProvider, ProviderSendResult
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
async def test_stream_message_yields_single_chunk():
    adapter = OpenAIProvider()

    async def fake_send(cx, *, model, message, tenant_id):
        return ProviderSendResult.from_token_counts("hello", 1, 1)

    adapter.send_message = fake_send  # type: ignore[method-assign]
    chunks = [c async for c in adapter.stream_message(None, model="m", message="x", tenant_id="t")]
    assert chunks == ["hello"]
