"""Provider call diagnostics helpers."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

from services.providers.anthropic_provider import _chat_max_tokens
from services.providers.call_diagnostics import estimate_request_tokens, timeout_reason


def test_estimate_request_tokens():
    assert estimate_request_tokens(message="abcd") == 1
    assert estimate_request_tokens(message="a" * 400) == 100


def test_timeout_reason_read_timeout():
    assert timeout_reason(httpx.ReadTimeout("")) == "read_timeout"


def test_chat_max_tokens_default():
    assert _chat_max_tokens() == 1024


def test_anthropic_completion_truncated_stop_reason():
    from services.providers.anthropic_provider import anthropic_completion_truncated

    assert anthropic_completion_truncated(
        {"stop_reason": "max_tokens", "usage": {"output_tokens": 100}},
        max_tokens=1024,
        completion_tokens=100,
    )
    assert not anthropic_completion_truncated(
        {"stop_reason": "end_turn", "usage": {"output_tokens": 100}},
        max_tokens=1024,
        completion_tokens=100,
    )


def test_anthropic_completion_truncated_token_boundary():
    from services.providers.anthropic_provider import anthropic_completion_truncated

    assert anthropic_completion_truncated(
        {"stop_reason": "end_turn"},
        max_tokens=1024,
        completion_tokens=1024,
    )
