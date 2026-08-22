"""Allowlisted xAI HTTP error extraction — no secrets, no conversation dump."""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.ops.json_log_formatter import BenOpsJsonFormatter
from services.providers.provider_errors import format_chat_provider_error
from services.providers.xai_error_diagnostics import (
    ERROR_MESSAGE_MAX,
    extract_safe_xai_http_error,
    log_safe_xai_http_error,
)
from services.providers.xai_provider import XAI_FAST_MODEL, XAI_FLAGSHIP_MODEL, XAIProvider


PASTE_CANARY = "BEN-GROK-LP-SECRET-BODY-" + ("ק" * 200)
PROMPT_CANARY = "USER-PROMPT-MUST-NOT-APPEAR"
KEY_CANARY = "xai-abcdefghijklmnopqrstuvwxyz012345"


def _response(
    status: int,
    body,
    *,
    headers: dict[str, str] | None = None,
    text: bytes | None = None,
) -> httpx.Response:
    content = text if text is not None else json.dumps(body).encode() if body is not None else b""
    request = httpx.Request("POST", "https://api.x.ai/v1/chat/completions")
    return httpx.Response(status, content=content, headers=headers or {}, request=request)


def test_extracts_flat_xai_json_allowlist_only():
    resp = _response(
        400,
        {"code": "invalid-argument", "error": "Incorrect API key provided."},
        headers={"cf-ray": "abc123-IAD", "x-request-id": "req-safe-1"},
    )
    fields = extract_safe_xai_http_error(resp)
    assert fields["http_status"] == 400
    assert fields["error_code"] == "invalid-argument"
    assert fields["error_message"] == "Incorrect API key provided."
    assert fields["error_type"] is None
    assert fields["request_id"] == "req-safe-1"
    assert fields["cf_ray"] == "abc123-IAD"
    assert set(fields) <= {
        "http_status",
        "error_code",
        "error_type",
        "error_message",
        "request_id",
        "cf_ray",
    }


def test_extracts_nested_openai_shaped_error():
    resp = _response(
        400,
        {
            "error": {
                "type": "invalid_request_error",
                "code": "model_not_found",
                "message": "The model does not exist",
                "secret": KEY_CANARY,
                "prompt": PROMPT_CANARY,
            },
            "unused": PASTE_CANARY,
        },
        headers={"openai-request-id": "cmpl-nested"},
    )
    fields = extract_safe_xai_http_error(resp)
    assert fields["http_status"] == 400
    assert fields["error_type"] == "invalid_request_error"
    assert fields["error_code"] == "model_not_found"
    assert fields["error_message"] == "The model does not exist"
    assert fields["request_id"] == "cmpl-nested"
    dumped = json.dumps(fields)
    assert KEY_CANARY not in dumped
    assert PROMPT_CANARY not in dumped
    assert PASTE_CANARY not in dumped
    assert "unused" not in dumped
    assert "secret" not in dumped


def test_malformed_body_does_not_dump_text():
    resp = _response(
        400,
        None,
        text=f"not-json {PROMPT_CANARY} {PASTE_CANARY} {KEY_CANARY}".encode(),
    )
    fields = extract_safe_xai_http_error(resp)
    assert fields["http_status"] == 400
    assert fields["error_message"] is None
    assert fields["error_code"] is None
    dumped = json.dumps(fields)
    assert PROMPT_CANARY not in dumped
    assert PASTE_CANARY not in dumped
    assert KEY_CANARY not in dumped
    assert "not-json" not in dumped


def test_error_message_is_bounded_and_redacts_key_material():
    long_msg = f"{KEY_CANARY} Authorization: Bearer secret-token " + ("x " * 300)
    resp = _response(400, {"code": "bad", "error": long_msg})
    fields = extract_safe_xai_http_error(resp)
    assert fields["error_message"] is not None
    assert len(fields["error_message"]) <= ERROR_MESSAGE_MAX
    assert KEY_CANARY not in fields["error_message"]
    assert "Bearer secret-token" not in fields["error_message"]
    assert "[redacted]" in fields["error_message"]


def test_log_event_is_allowlisted_and_formatter_safe(caplog):
    resp = _response(
        400,
        {
            "code": "invalid-argument",
            "error": f"Incorrect API key provided {KEY_CANARY}",
            "prompt": PROMPT_CANARY,
            "paste": PASTE_CANARY,
            "Authorization": f"Bearer {KEY_CANARY}",
        },
        headers={"cf-ray": "ray-9", "authorization": f"Bearer {KEY_CANARY}"},
    )
    logger = logging.getLogger("ben.ops")
    with caplog.at_level(logging.WARNING, logger="ben.ops"):
        log_safe_xai_http_error(resp)
    assert caplog.records
    rec = caplog.records[-1]
    formatter = BenOpsJsonFormatter()
    line = formatter.format(rec)
    payload = json.loads(line)
    assert payload["provider"] == "xai"
    assert payload["event"] == "provider_http_error"
    assert payload["http_status"] == 400
    assert payload["error_code"] == "invalid-argument"
    assert KEY_CANARY not in line
    assert "Authorization" not in line
    assert "Bearer " not in line
    assert PROMPT_CANARY not in line
    assert PASTE_CANARY not in line
    assert "XAI_API_KEY" not in line


def test_user_facing_xai_http_error_is_generic():
    req = httpx.Request("POST", "https://api.x.ai/v1/chat/completions")
    resp = httpx.Response(
        400,
        content=b'{"code":"invalid-argument","error":"Incorrect API key provided."}',
        request=req,
    )
    exc = httpx.HTTPStatusError("400", request=req, response=resp)
    msg = format_chat_provider_error("xai", exc, timeout_s=25)
    assert msg == "Grok request failed (HTTP 400)"
    assert "Incorrect API key" not in msg
    assert "invalid-argument" not in msg


def test_gpt_claude_gemini_http_errors_unchanged():
    req = httpx.Request("POST", "https://example.test")
    resp = httpx.Response(400, request=req)
    exc = httpx.HTTPStatusError("400", request=req, response=resp)
    assert format_chat_provider_error("openai", exc, timeout_s=25) == "GPT error: HTTP 400"
    assert format_chat_provider_error("anthropic", exc, timeout_s=25) == "Claude error: HTTP 400"
    assert format_chat_provider_error("google", exc, timeout_s=25) == "Gemini error: HTTP 400"


def test_grok_request_body_unchanged_by_diagnostics():
    stream = XAIProvider()._json_body(XAI_FLAGSHIP_MODEL, PROMPT_CANARY, None, stream=True)
    plain = XAIProvider()._json_body(XAI_FAST_MODEL, PROMPT_CANARY, "sys", stream=False)
    assert stream == {
        "model": "grok-4.6",
        "messages": [
            {"role": "system", "content": stream["messages"][0]["content"]},
            {"role": "user", "content": PROMPT_CANARY},
        ],
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    assert set(stream) == {"model", "messages", "stream", "stream_options"}
    assert set(plain) == {"model", "messages"}
    assert plain["model"] == "grok-4.3"
    for body in (stream, plain):
        assert "search_parameters" not in body
        assert "web_search_options" not in body
        assert "tools" not in body
        assert "tool_choice" not in body
