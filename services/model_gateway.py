"""T03 Model Gateway: tier routing, per-provider circuit breaker, adapter dispatch."""
from __future__ import annotations

import os
import time
from typing import Any

import httpx

from services.ops.timeouts import HTTP_CLIENT_TIMEOUT_S
from services.providers import gateway_provider_api_key_env, get_gateway_provider

_CHAIN = ("openai", "anthropic", "google")
_FALLBACK = {"openai": "gpt-4o-mini", "anthropic": "claude-3-5-haiku-20241022", "google": "gemini-2.5-flash"}
ALLOWED_CHAT_PROVIDER_IDS = frozenset({"gpt", "claude", "gemini"})
_CHAT_PROVIDER_TO_GATEWAY = {"gpt": "openai", "claude": "anthropic", "gemini": "google"}
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
    if raw is None:
        return None
    pid = str(raw).strip().lower()
    if not pid:
        return None
    if pid not in ALLOWED_CHAT_PROVIDER_IDS:
        raise ValueError("provider_id must be one of: gpt, claude, gemini")
    return pid


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
        gateway = _CHAT_PROVIDER_TO_GATEWAY[provider_id]
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


async def route_request(
    message: str,
    tenant_id: str,
    tier: str,
    *,
    provider_id: str | None = None,
) -> dict[str, Any]:
    t0 = time.perf_counter()
    last: BaseException | None = None
    attempts = _attempts(tier, provider_id=provider_id)
    async with httpx.AsyncClient(timeout=HTTP_CLIENT_TIMEOUT_S) as cx:
        for prov, model in attempts:
            if not (os.getenv(gateway_provider_api_key_env(prov)) or "").strip():
                continue
            if not _cb_ready(prov):
                continue
            try:
                adapter = get_gateway_provider(prov)
                content, tok, pi, po = await adapter.send_message(
                    cx, model=model, message=message, tenant_id=tenant_id
                )
                _cb_ok(prov)
                ms = (time.perf_counter() - t0) * 1000.0
                return {
                    "content": content,
                    "model_used": model,
                    "provider_used": prov,
                    "tokens": tok,
                    "cost_usd": round(_cost(prov, model, pi, po), 6),
                    "latency_ms": round(ms, 2),
                }
            except BaseException as e:
                last = e
                _cb_fail(prov)
    ms = (time.perf_counter() - t0) * 1000.0
    err = repr(last) if last else "no_provider"
    return {"content": f"error: {err}", "model_used": "", "provider_used": "", "tokens": 0, "cost_usd": 0.0, "latency_ms": round(ms, 2)}
