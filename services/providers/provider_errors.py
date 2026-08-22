"""Sanitized provider error messages for /chat (no secrets)."""
from __future__ import annotations

import re

import httpx

from services.message_format import provider_display_label
from services.ops.failure_classification import (
    FAILURE_AUTH_ERROR,
    FAILURE_CONFIG_ERROR,
    FAILURE_PROVIDER_UNAVAILABLE,
    FAILURE_TIMEOUT,
    classify_failure,
)

_GATEWAY_TO_UI_ID = {"openai": "gpt", "anthropic": "claude", "google": "gemini", "xai": "grok"}
_SECRET_RE = re.compile(
    r"sk-[a-zA-Z0-9]{10,}|xai-[a-zA-Z0-9]{10,}|api[_-]?key[=:]\s*\S+",
    re.I,
)


def gateway_provider_label(gateway_provider: str) -> str:
    ui_id = _GATEWAY_TO_UI_ID.get((gateway_provider or "").strip().lower(), "")
    return provider_display_label(ui_id) or (gateway_provider or "Provider").title()


def sanitize_provider_error_message(exc: BaseException) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    if isinstance(exc, httpx.TimeoutException):
        return "request timed out"
    text = str(exc).strip()
    if not text or text in ('""', "''"):
        return type(exc).__name__
    text = _SECRET_RE.sub("[redacted]", text)
    return text[:200]


def format_chat_provider_error(
    gateway_provider: str,
    exc: BaseException,
    *,
    timeout_s: float,
) -> str:
    label = gateway_provider_label(gateway_provider)
    if (gateway_provider or "").strip().lower() == "xai" and isinstance(exc, httpx.HTTPStatusError):
        return f"Grok request failed (HTTP {exc.response.status_code})"
    category = classify_failure(exc)
    if category == FAILURE_TIMEOUT:
        secs = int(timeout_s) if timeout_s == int(timeout_s) else round(timeout_s, 1)
        return f"{label} timed out after {secs}s"
    if category == FAILURE_AUTH_ERROR:
        return f"{label} authentication failed"
    if category == FAILURE_CONFIG_ERROR:
        detail = sanitize_provider_error_message(exc)
        if (
            "model" in detail.lower()
            or "not_found" in detail.lower()
            or detail.startswith("HTTP 4")
        ):
            return f"{label} model is not available ({detail})"
        return f"{label} configuration error"
    if category == FAILURE_PROVIDER_UNAVAILABLE:
        detail = sanitize_provider_error_message(exc)
        if detail and detail != type(exc).__name__:
            return f"{label} unavailable ({detail})"
        return f"{label} unavailable"
    detail = sanitize_provider_error_message(exc)
    if detail:
        return f"{label} error: {detail}"
    return f"{label} request failed"
