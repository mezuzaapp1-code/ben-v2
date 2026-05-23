"""Structured diagnostics for chat provider adapter calls (no payloads/secrets)."""
from __future__ import annotations

from typing import Any

from services.ops.failure_classification import FAILURE_TIMEOUT, classify_failure
from services.ops.structured_log import log_info, log_warning


def estimate_request_tokens(*, message: str) -> int:
    """Rough input token estimate when provider usage is unavailable."""
    text = (message or "").strip()
    if not text:
        return 0
    return max(1, len(text) // 4)


def timeout_reason(exc: BaseException | None) -> str | None:
    if exc is None:
        return None
    if classify_failure(exc) == FAILURE_TIMEOUT:
        return "read_timeout" if type(exc).__name__ == "ReadTimeout" else "timeout"
    return None


def log_chat_provider_call(
    *,
    provider: str,
    model: str,
    outcome: str,
    duration_ms: int,
    operation: str = "provider_send_message",
    timeout_s: float | None = None,
    request_chars: int | None = None,
    request_tokens_est: int | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    ttfb_ms: int | None = None,
    max_tokens: int | None = None,
    truncation_detected: bool | None = None,
    stop_reason: str | None = None,
    exc: BaseException | None = None,
    error_class: str | None = None,
    error_message: str | None = None,
    **extra: Any,
) -> None:
    fields: dict[str, Any] = {
        "operation": operation,
        "outcome": outcome,
        "model": model,
        "duration_ms": duration_ms,
    }
    if timeout_s is not None:
        fields["timeout_s"] = timeout_s
    if request_chars is not None:
        fields["request_chars"] = request_chars
    if request_tokens_est is not None:
        fields["request_tokens_est"] = request_tokens_est
    if prompt_tokens is not None:
        fields["prompt_tokens"] = prompt_tokens
    if completion_tokens is not None:
        fields["completion_tokens"] = completion_tokens
    if ttfb_ms is not None:
        fields["ttfb_ms"] = ttfb_ms
    if max_tokens is not None:
        fields["max_tokens"] = max_tokens
    if truncation_detected is not None:
        fields["truncation_detected"] = truncation_detected
    if stop_reason is not None:
        fields["stop_reason"] = stop_reason
    treason = timeout_reason(exc)
    if treason:
        fields["timeout_reason"] = treason
    if error_class:
        fields["error_class"] = error_class
    if error_message:
        fields["error_message"] = error_message
    fields.update(extra)

    if outcome == "ok":
        log_info(
            "chat provider adapter call completed",
            subsystem="model_gateway",
            provider=provider,
            **fields,
        )
    else:
        log_warning(
            "chat provider adapter call failed",
            subsystem="model_gateway",
            provider=provider,
            category=classify_failure(exc) if exc else "unknown_error",
            exc=exc,
            **fields,
        )
