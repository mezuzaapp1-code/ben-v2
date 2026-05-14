"""T12 Council MVP: three experts in parallel (Claude + GPT-4o + GPT-4o-mini)."""
from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx

S_LEGAL = "You are a sharp legal advisor.\nAnalyze risks, contracts, compliance.\nBe direct. Max 3 sentences."
S_BIZ = "You are a senior business strategist.\nAnalyze market, revenue, growth.\nBe direct. Max 3 sentences."
S_STRAT = "You are a strategic thinker.\nAnalyze long-term implications and risks.\nBe direct. Max 3 sentences."

CLAUDE_MODEL = "claude-3-5-sonnet-20241022"


def _hdr(tenant_id: str) -> dict[str, str]:
    return {"X-BEN-Tenant": tenant_id}


def _cost_oai(model: str, pi: int, po: int) -> float:
    ir, or_ = {"gpt-4o": (2.5e-6, 10e-6), "gpt-4o-mini": (0.15e-6, 0.60e-6)}.get(model, (0.5e-6, 1.5e-6))
    return ir * pi + or_ * po


def _cost_claude(pi: int, po: int) -> float:
    return 3e-6 * pi + 15e-6 * po


async def _openai(cx: httpx.AsyncClient, model: str, system: str, q: str, tenant_id: str) -> tuple[str, float]:
    k = os.getenv("OPENAI_API_KEY", "").strip()
    if not k:
        return "missing OPENAI_API_KEY", 0.0
    r = await cx.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {k}", **_hdr(tenant_id)},
        json={"model": model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": q}]},
    )
    r.raise_for_status()
    d = r.json()
    txt = str(d["choices"][0]["message"]["content"])
    u = d.get("usage") or {}
    pi, po = int(u.get("prompt_tokens", 0)), int(u.get("completion_tokens", 0))
    return txt, _cost_oai(model, pi, po)


async def _legal(cx: httpx.AsyncClient, q: str, tenant_id: str) -> tuple[str, float]:
    """Anthropic Messages API: x-api-key + anthropic-version (never Authorization: Bearer)."""
    k = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not k:
        return "missing ANTHROPIC_API_KEY", 0.0
    r = await cx.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": k,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
            **_hdr(tenant_id),
        },
        json={
            "model": CLAUDE_MODEL,
            "max_tokens": 512,
            "system": S_LEGAL,
            "messages": [{"role": "user", "content": q}],
        },
    )
    r.raise_for_status()
    d = r.json()
    txt = "".join(b.get("text", "") for b in d.get("content", []) if b.get("type") == "text")
    u = d.get("usage") or {}
    pi, po = int(u.get("input_tokens", 0)), int(u.get("output_tokens", 0))
    return txt, _cost_claude(pi, po)


def _unwrap(x: Any) -> tuple[str, float]:
    if isinstance(x, BaseException):
        return repr(x), 0.0
    return x


async def run_council(question: str, tenant_id: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=120.0) as cx:
        raw = await asyncio.gather(
            _legal(cx, question, tenant_id),
            _openai(cx, "gpt-4o", S_BIZ, question, tenant_id),
            _openai(cx, "gpt-4o-mini", S_STRAT, question, tenant_id),
            return_exceptions=True,
        )
    ra, ca = _unwrap(raw[0])
    rb, cb = _unwrap(raw[1])
    rc, cc = _unwrap(raw[2])
    return {
        "question": question,
        "council": [
            {"expert": "Legal Advisor", "model": "claude", "response": ra},
            {"expert": "Business Advisor", "model": "gpt-4o", "response": rb},
            {"expert": "Strategy Advisor", "model": "gpt-4o-mini", "response": rc},
        ],
        "cost_usd": round(ca + cb + cc, 6),
    }
