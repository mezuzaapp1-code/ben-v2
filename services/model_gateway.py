"""T03 Model Gateway: tier routing, per-provider circuit breaker, httpx async calls."""
from __future__ import annotations

import os
import time
from typing import Any

import httpx

_CHAIN = ("openai", "anthropic", "google")
_FALLBACK = {"openai": "gpt-4o-mini", "anthropic": "claude-3-5-haiku-20241022", "google": "gemini-1.5-flash"}
_RATES: dict[tuple[str, str], tuple[float, float]] = {
    ("openai", "gpt-4o-mini"): (0.15e-6, 0.60e-6),
    ("openai", "gpt-4o"): (2.5e-6, 10e-6),
    ("anthropic", "claude-3-5-sonnet-20241022"): (3e-6, 15e-6),
    ("anthropic", "claude-3-5-haiku-20241022"): (1e-6, 5e-6),
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


def _attempts(tier: str) -> list[tuple[str, str]]:
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


def _hdr(tenant_id: str) -> dict[str, str]:
    return {"X-BEN-Tenant": tenant_id}


async def _call_openai(cx: httpx.AsyncClient, model: str, message: str, tenant_id: str) -> tuple[str, int, int, int]:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    r = await cx.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", **_hdr(tenant_id)},
        json={"model": model, "messages": [{"role": "user", "content": message}]},
    )
    r.raise_for_status()
    d = r.json()
    u = d.get("usage") or {}
    pi, po = int(u.get("prompt_tokens", 0)), int(u.get("completion_tokens", 0))
    return str(d["choices"][0]["message"]["content"]), pi + po, pi, po


async def _call_anthropic(cx: httpx.AsyncClient, model: str, message: str, tenant_id: str) -> tuple[str, int, int, int]:
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    r = await cx.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json", **_hdr(tenant_id)},
        json={"model": model, "max_tokens": 4096, "messages": [{"role": "user", "content": message}]},
    )
    r.raise_for_status()
    d = r.json()
    txt = "".join(b.get("text", "") for b in d.get("content", []) if b.get("type") == "text")
    u = d.get("usage") or {}
    pi, po = int(u.get("input_tokens", 0)), int(u.get("output_tokens", 0))
    return txt, pi + po, pi, po


async def _call_google(cx: httpx.AsyncClient, model: str, message: str, tenant_id: str) -> tuple[str, int, int, int]:
    api_key = os.getenv("GOOGLE_API_KEY", "").strip()
    url = f"https://generativelanguage.googleapis.com/v1/models/{model}:generateContent"
    r = await cx.post(url, params={"key": api_key}, headers=_hdr(tenant_id), json={"contents": [{"parts": [{"text": message}]}]})
    r.raise_for_status()
    d = r.json()
    parts = ((d.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
    txt = "".join(p.get("text", "") for p in parts)
    m = d.get("usageMetadata") or {}
    pi, po = int(m.get("promptTokenCount", 0)), int(m.get("candidatesTokenCount", 0))
    return txt, pi + po, pi, po


async def route_request(message: str, tenant_id: str, tier: str) -> dict[str, Any]:
    t0 = time.perf_counter()
    keys = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY", "google": "GOOGLE_API_KEY"}
    last: BaseException | None = None
    async with httpx.AsyncClient(timeout=120.0) as cx:
        for prov, model in _attempts(tier):
            if not (os.getenv(keys[prov]) or "").strip():
                continue
            if not _cb_ready(prov):
                continue
            try:
                if prov == "openai":
                    content, tok, pi, po = await _call_openai(cx, model, message, tenant_id)
                elif prov == "anthropic":
                    content, tok, pi, po = await _call_anthropic(cx, model, message, tenant_id)
                else:
                    content, tok, pi, po = await _call_google(cx, model, message, tenant_id)
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
