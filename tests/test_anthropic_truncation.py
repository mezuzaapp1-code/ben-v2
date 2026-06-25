"""Anthropic max_tokens truncation observability."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.model_gateway import route_request
from services.providers.anthropic_provider import AnthropicProvider
from services.providers.base_provider import ProviderSendResult

TENANT = "00000000-0000-0000-0000-000000000001"


@pytest.mark.asyncio
async def test_anthropic_adapter_sets_completion_truncated(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    payload = {
        "content": [{"type": "text", "text": "partial..."}],
        "usage": {"input_tokens": 10, "output_tokens": 1024},
        "stop_reason": "max_tokens",
    }
    body = json.dumps(payload).encode()

    class _Resp:
        async def aread(self):
            return body

        def raise_for_status(self):
            return None

    class _Stream:
        async def __aenter__(self):
            return _Resp()

        async def __aexit__(self, *a):
            return None

    cx = MagicMock()
    cx.stream = MagicMock(return_value=_Stream())

    with patch("services.providers.anthropic_provider.log_chat_provider_call") as log_mock:
        result = await AnthropicProvider().send_message(
            cx, model="claude-sonnet-4-6", message="hi", tenant_id=TENANT
        )

    assert result.completion_truncated is True
    assert log_mock.call_args.kwargs["truncation_detected"] is True


@pytest.mark.asyncio
async def test_route_request_includes_completion_truncated(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_API_KEY", "")

    async def truncated_send(cx, *, model, message, tenant_id, system=None):
        return ProviderSendResult.from_token_counts(
            "clip", 1, 1024, completion_truncated=True
        )

    from services.providers import get_gateway_provider

    with patch.object(
        get_gateway_provider("anthropic"),
        "send_message",
        side_effect=truncated_send,
    ):
        out = await route_request("hi", TENANT, "free", provider_id="claude")

    assert out["completion_truncated"] is True
    assert out["content"] == "clip"
