"""T03 Model Gateway: tier routing, per-provider circuit breaker, adapter dispatch."""
from __future__ import annotations

import os
import time
from typing import Any

import httpx

from services.message_format import provider_display_label
from services.providers.speaking_registry import (
    all_provider_ids,
    gateway_for_provider_id,
    normalize_speaking_provider_id,
)
from services.ops.failure_classification import classify_failure
from services.ops.structured_log import log_info, log_warning
from services.providers.call_diagnostics import estimate_request_tokens
from services.ops.timeouts import CHAT_EXPLICIT_PROVIDER_TIMEOUT_S, HTTP_CLIENT_TIMEOUT_S
from services.providers import gateway_provider_api_key_env, get_gateway_provider
from services.providers.provider_errors import format_chat_provider_error, sanitize_provider_error_message

_CHAIN = ("openai", "anthropic", "google")
_FALLBACK = {"openai": "gpt-4o-mini", "anthropic": "claude-3-5-haiku-20241022", "google": "gemini-2.5-flash"}
ALLOWED_CHAT_PROVIDER_IDS = all_provider_ids()


def _chat_provider_to_gateway(provider_id: str) -> str:
    return gateway_for_provider_id(provider_id)
_RATES: dict[tuple[str, str], tuple[float, float]] = {
    ("openai", "gpt-4o-mini"): (0.15e-6, 0.60e-6),
    ("openai", "gpt-4o"): (2.5e-6, 10e-6),
    ("anthropic", "claude-3-5-sonnet-20241022"): (3e-6, 15e-6),
    ("anthropic", "claude-3-5-haiku-20241022"): (1e-6, 5e-6),
    ("google", "gemini-2.0-flash"): (0.1e-6, 0.4e-6),
    ("google", "gemini-1.5-flash"): (0.1e-6, 0.4e-6),
}
_CB: dict[str, dict[str, float | int]] = {}


def _tier_primary(tier: str) -> tuple[str, str]:
    t = (tier or "free").lower()
    if t == "pro":
        return "anthropic", "claude-3-5-sonnet-20241022"
    if t == "enterprise":
        return "openai", "gpt-4o"
    return "openai", "gpt-4o-mini"


def normalize_chat_provider_id(raw: str | None) -> str | None:
    """UI provider id for /chat; None when omitted (tier routing)."""
    return normalize_speaking_provider_id(raw)


def _model_for_gateway_provider(gateway_prov: str, tier: str) -> str:
    t = (tier or "free").lower()
    if gateway_prov == "openai":
        if t == "enterprise":
            return "gpt-4o"
        return "gpt-4o-mini"
    if gateway_prov == "anthropic":
        return (
            os.getenv("ANTHROPIC_MODEL", "").strip()
            or "claude-sonnet-4-6"
        )
    return (
        os.getenv("GEMINI_MODEL", "").strip()
        or os.getenv("GOOGLE_MODEL", "").strip()
        or "gemini-2.5-flash"
    )


def _attempts(tier: str, *, provider_id: str | None = None) -> list[tuple[str, str]]:
    if provider_id:
        gateway = _chat_provider_to_gateway(provider_id)
        return [(gateway, _model_for_gateway_provider(gateway, tier))]
    t = (tier or "free").lower()
    if t == "free":
        return [("openai", "gpt-4o-mini")]
    p, m = _tier_primary(tier)
    out = [(p, m)]
    for x in _CHAIN:
        if x != p:
            out.append((x, _FALLBACK[x]))
    return out


def _chat_http_timeout_s(*, provider_id: str | None) -> float:
    if provider_id:
        return CHAT_EXPLICIT_PROVIDER_TIMEOUT_S
    return HTTP_CLIENT_TIMEOUT_S


def _cb_ready(name: str) -> bool:
    s = _CB.setdefault(name, {"n": 0, "until": 0.0})
    now = time.monotonic()
    if now < float(s["until"]):
        return False
    if s["until"]:
        s["n"], s["until"] = 0, 0.0
    return True


def _cb_ok(name: str) -> None:
    _CB[name] = {"n": 0, "until": 0.0}


def _cb_fail(name: str) -> None:
    s = _CB.setdefault(name, {"n": 0, "until": 0.0})
    s["n"] = int(s["n"]) + 1
    if int(s["n"]) >= 3:
        s["until"] = time.monotonic() + 60.0
        s["n"] = 0


def _cost(prov: str, model: str, inp: int, out: int) -> float:
    ir, or_ = _RATES.get((prov, model), (0.5e-6, 1.5e-6))
    return ir * inp + or_ * out


def _missing_key_message(*, provider_id: str | None, gateway_prov: str) -> str:
    if provider_id:
        label = provider_display_label(provider_id) or gateway_prov.title()
        return f"{label} is not configured (missing API key)"
    return f"{gateway_prov} is not configured (missing API key)"


async def route_request(
    message: str,
    tenant_id: str,
    tier: str,
    *,
    provider_id: str | None = None,
) -> dict[str, Any]:
    t0 = time.perf_counter()
    timeout_s = _chat_http_timeout_s(provider_id=provider_id)
    last: BaseException | None = None
    last_prov: str = ""
    attempts = _attempts(tier, provider_id=provider_id)
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_s, connect=5.0)) as cx:
        for prov, model in attempts:
            key_env = gateway_provider_api_key_env(prov)
            if not (os.getenv(key_env) or "").strip():
                if provider_id and _chat_provider_to_gateway(provider_id) == prov:
                    ms = (time.perf_counter() - t0) * 1000.0
                    return {
                        "content": _missing_key_message(provider_id=provider_id, gateway_prov=prov),
                        "model_used": "",
                        "provider_used": prov,
                        "tokens": 0,
                        "cost_usd": 0.0,
                        "latency_ms": round(ms, 2),
                    }
                continue
            if not _cb_ready(prov):
                continue
            attempt_t0 = time.perf_counter()
            try:
                adapter = get_gateway_provider(prov)
                send_result = await adapter.send_message(
                    cx, model=model, message=message, tenant_id=tenant_id
                )
                content = send_result.content
                tok = send_result.total_tokens
                pi = send_result.prompt_tokens
                po = send_result.completion_tokens
                _cb_ok(prov)
                attempt_ms = int((time.perf_counter() - attempt_t0) * 1000.0)
                if prov != "anthropic":
                    log_info(
                        "chat provider adapter call completed",
                        subsystem="model_gateway",
                        provider=prov,
                        model=model,
                        duration_ms=attempt_ms,
                        operation="provider_send_message",
                        outcome="ok",
                        timeout_s=timeout_s,
                        request_chars=len(message),
                        request_tokens_est=estimate_request_tokens(message=message),
                        response_tokens=tok,
                        prompt_tokens=pi,
                        completion_tokens=po,
                    )
                if send_result.completion_truncated:
                    log_info(
                        "chat provider completion truncated",
                        subsystem="model_gateway",
                        provider=prov,
                        model=model,
                        operation="provider_send_message",
                        outcome="truncated",
                        completion_truncated=True,
                        truncation_detected=True,
                        completion_tokens=po,
                    )
                ms = (time.perf_counter() - t0) * 1000.0
                return {
                    "content": content,
                    "model_used": model,
                    "provider_used": prov,
                    "tokens": tok,
                    "cost_usd": round(_cost(prov, model, pi, po), 6),
                    "latency_ms": round(ms, 2),
                    "completion_truncated": send_result.completion_truncated,
                }
            except BaseException as e:
                last = e
                last_prov = prov
                elapsed_ms = int((time.perf_counter() - attempt_t0) * 1000.0)
                if prov != "anthropic":
                    log_warning(
                        "chat provider adapter call failed",
                        subsystem="model_gateway",
                        provider=prov,
                        category=classify_failure(e),
                        exc=e,
                        duration_ms=elapsed_ms,
                        operation="provider_send_message",
                        outcome="error",
                        model=model,
                        timeout_s=timeout_s,
                        request_chars=len(message),
                        request_tokens_est=estimate_request_tokens(message=message),
                        error_class=type(e).__name__,
                        error_message=sanitize_provider_error_message(e),
                    )
                _cb_fail(prov)
    ms = (time.perf_counter() - t0) * 1000.0
    if last and last_prov:
        err = format_chat_provider_error(last_prov, last, timeout_s=timeout_s)
    elif last:
        err = format_chat_provider_error("", last, timeout_s=timeout_s)
    else:
        err = "No provider available"
    return {
        "content": err,
        "model_used": "",
        "provider_used": last_prov,
        "tokens": 0,
        "cost_usd": 0.0,
        "latency_ms": round(ms, 2),
    }
