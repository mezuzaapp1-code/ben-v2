"""Provider adapter error formatting and chat gateway timeouts."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.model_gateway import _chat_http_timeout_s, route_request
from services.ops.timeouts import CHAT_EXPLICIT_PROVIDER_TIMEOUT_S, HTTP_CLIENT_TIMEOUT_S
from services.providers.provider_errors import (
    format_chat_provider_error,
    gateway_provider_label,
    sanitize_provider_error_message,
)

TENANT = "00000000-0000-0000-0000-000000000001"


def test_chat_timeout_explicit_provider_uses_longer_budget():
    assert _chat_http_timeout_s(provider_id="claude") == CHAT_EXPLICIT_PROVIDER_TIMEOUT_S
    assert _chat_http_timeout_s(provider_id=None) == HTTP_CLIENT_TIMEOUT_S


def test_format_read_timeout_message():
    exc = httpx.ReadTimeout("")
    msg = format_chat_provider_error("anthropic", exc, timeout_s=25.0)
    assert msg == "Claude timed out after 25s"


def test_format_gemini_timeout_message():
    exc = httpx.ReadTimeout("timed out")
    msg = format_chat_provider_error("google", exc, timeout_s=25)
    assert msg == "Gemini timed out after 25s"


def test_sanitize_empty_read_timeout():
    assert sanitize_provider_error_message(httpx.ReadTimeout("")) == "request timed out"


def test_sanitize_redacts_xai_key_material():
    msg = sanitize_provider_error_message(RuntimeError("auth failed xai-abcdefghijklmnopqrstuvwxyz"))
    assert "xai-abcdefghijklmnopqrstuvwxyz" not in msg
    assert "[redacted]" in msg


def test_gateway_provider_label():
    assert gateway_provider_label("anthropic") == "Claude"
    assert gateway_provider_label("xai") == "Grok"


def test_xai_timeout_message_unchanged():
    msg = format_chat_provider_error("xai", httpx.ReadTimeout(""), timeout_s=25)
    assert msg == "Grok timed out after 25s"


@pytest.mark.asyncio
async def test_route_request_returns_provider_timeout_message(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("OPENAI_API_KEY", "")

    async def fail_send(cx, *, model, message, tenant_id):
        raise httpx.ReadTimeout("")

    with patch.object(
        __import__("services.providers", fromlist=["get_gateway_provider"]).get_gateway_provider("anthropic"),
        "send_message",
        side_effect=fail_send,
    ):
        out = await route_request("hi", TENANT, "free", provider_id="claude")
    assert out["content"] == "Claude timed out after 25s"
    assert "ReadTimeout" not in out["content"]
