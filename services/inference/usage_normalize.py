"""Normalize provider usage payloads into InferenceUsage."""
from __future__ import annotations

from typing import Any

from services.inference.contracts import InferenceUsage


def usage_missing() -> InferenceUsage:
    return InferenceUsage(usage_status="missing")


def normalize_openai_usage(raw: dict[str, Any] | None) -> InferenceUsage:
    if not isinstance(raw, dict) or not raw:
        return usage_missing()
    input_tokens = int(raw.get("prompt_tokens") or raw.get("input_tokens") or 0)
    output_tokens = int(raw.get("completion_tokens") or raw.get("output_tokens") or 0)
    details = raw.get("prompt_tokens_details") or {}
    cached = 0
    if isinstance(details, dict):
        cached = int(details.get("cached_tokens") or 0)
    completion_details = raw.get("completion_tokens_details") or {}
    reasoning = 0
    if isinstance(completion_details, dict):
        reasoning = int(completion_details.get("reasoning_tokens") or 0)
    total = int(raw.get("total_tokens") or (input_tokens + output_tokens))
    if input_tokens == 0 and output_tokens == 0 and total == 0 and cached == 0 and reasoning == 0:
        return usage_missing()
    return InferenceUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached,
        reasoning_tokens=reasoning,
        total_tokens=total,
        usage_status="exact",
    )


def normalize_anthropic_usage(raw: dict[str, Any] | None) -> InferenceUsage:
    if not isinstance(raw, dict) or not raw:
        return usage_missing()
    input_tokens = int(raw.get("input_tokens") or 0)
    output_tokens = int(raw.get("output_tokens") or 0)
    cached = int(
        raw.get("cache_read_input_tokens")
        or raw.get("cache_creation_input_tokens")
        or 0
    )
    # Anthropic may split cache; prefer read as cached input.
    cache_read = raw.get("cache_read_input_tokens")
    if cache_read is not None:
        cached = int(cache_read or 0)
    if input_tokens == 0 and output_tokens == 0 and cached == 0:
        return usage_missing()
    return InferenceUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached,
        reasoning_tokens=0,
        total_tokens=input_tokens + output_tokens,
        usage_status="exact",
    )


def merge_anthropic_stream_usage(
    prior: InferenceUsage,
    delta_raw: dict[str, Any] | None,
) -> InferenceUsage:
    """Merge Anthropic message_delta usage onto message_start baseline."""
    delta = normalize_anthropic_usage(delta_raw)
    if delta.usage_status == "missing":
        return prior
    input_tokens = prior.input_tokens or delta.input_tokens
    output_tokens = delta.output_tokens or prior.output_tokens
    cached = prior.cached_input_tokens or delta.cached_input_tokens
    if input_tokens == 0 and output_tokens == 0 and cached == 0:
        return prior if prior.usage_status != "missing" else usage_missing()
    return InferenceUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached,
        reasoning_tokens=max(prior.reasoning_tokens, delta.reasoning_tokens),
        total_tokens=input_tokens + output_tokens,
        usage_status="exact",
    )


def normalize_gemini_usage(raw: dict[str, Any] | None) -> InferenceUsage:
    if not isinstance(raw, dict) or not raw:
        return usage_missing()
    input_tokens = int(raw.get("promptTokenCount") or 0)
    output_tokens = int(raw.get("candidatesTokenCount") or 0)
    total = int(raw.get("totalTokenCount") or (input_tokens + output_tokens))
    cached = int(raw.get("cachedContentTokenCount") or 0)
    thoughts = int(raw.get("thoughtsTokenCount") or 0)
    if input_tokens == 0 and output_tokens == 0 and total == 0 and cached == 0 and thoughts == 0:
        return usage_missing()
    return InferenceUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached,
        reasoning_tokens=thoughts,
        total_tokens=total,
        usage_status="exact",
    )
