"""T12 Council: three experts in parallel, then BEN synthesis (sequential).

Synthesis is isolated: failures return expert results only (synthesis null).
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from typing import Any

import httpx
from sqlalchemy import text

from database.connection import get_db_session
from database.models import KnowledgeObject
from services.ops.failure_classification import FAILURE_CONFIG_ERROR, classify_failure
from services.ops.request_context import attach_request_id, get_request_id
from services.ops.structured_log import log_warning
from services.ops.timing import log_timing, measure
from services.ops.timeouts import (
    DB_OPERATION_TIMEOUT_S,
    HTTP_CLIENT_TIMEOUT_S,
    SYNTHESIS_TIMEOUT_S,
)

S_LEGAL = "You are a sharp legal advisor.\nAnalyze risks, contracts, compliance.\nBe direct. Max 3 sentences."
S_BIZ = "You are a senior business strategist.\nAnalyze market, revenue, growth.\nBe direct. Max 3 sentences."
S_STRAT = "You are a strategic thinker.\nAnalyze long-term implications and risks.\nBe direct. Max 3 sentences."

ANTHROPIC_MODEL_DEFAULT = "claude-sonnet-4-6"
SYNTHESIS_MODEL_DEFAULT = "gpt-4o-mini"

SYNTHESIS_SYSTEM = """You are BEN, a Cognitive Operating System.
Your job is to synthesize expert opinions into structured organizational reasoning.
Return ONLY valid JSON. No markdown. No explanations outside the JSON."""


def _hdr(tenant_id: str) -> dict[str, str]:
    return {"X-BEN-Tenant": tenant_id}


def _cost_oai(model: str, pi: int, po: int) -> float:
    ir, or_ = {"gpt-4o": (2.5e-6, 10e-6), "gpt-4o-mini": (0.15e-6, 0.60e-6)}.get(model, (0.5e-6, 1.5e-6))
    return ir * pi + or_ * po


def _cost_claude(pi: int, po: int) -> float:
    return 3e-6 * pi + 15e-6 * po


def _degraded_expert_response(category: str) -> str:
    return f"Expert unavailable ({category}). Please retry or check configuration."


def _log_provider_failure(*, provider: str, subsystem: str, exc: BaseException | None = None, message: str) -> None:
    category = classify_failure(exc) if exc else FAILURE_CONFIG_ERROR
    log_warning(message, subsystem=subsystem, provider=provider, category=category, exc=exc)


async def _openai(cx: httpx.AsyncClient, model: str, system: str, q: str, tenant_id: str) -> tuple[str, float]:
    k = os.getenv("OPENAI_API_KEY", "").strip()
    if not k:
        _log_provider_failure(provider="openai", subsystem="council", message="missing OPENAI_API_KEY")
        return "missing OPENAI_API_KEY", 0.0
    t0 = time.perf_counter()
    try:
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
        log_timing(
            "openai call completed",
            subsystem="council",
            operation="provider_openai",
            provider="openai",
            duration_ms=int((time.perf_counter() - t0) * 1000),
            outcome="ok",
            model=model,
        )
        return txt, _cost_oai(model, pi, po)
    except Exception as e:
        log_timing(
            "openai call failed",
            subsystem="council",
            operation="provider_openai",
            provider="openai",
            duration_ms=int((time.perf_counter() - t0) * 1000),
            outcome="error",
            category=classify_failure(e),
            model=model,
        )
        raise


async def _legal(cx: httpx.AsyncClient, q: str, tenant_id: str) -> tuple[str, float]:
    k = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not k:
        _log_provider_failure(provider="anthropic", subsystem="council", message="missing ANTHROPIC_API_KEY")
        return "missing ANTHROPIC_API_KEY", 0.0
    model = os.getenv("ANTHROPIC_MODEL", ANTHROPIC_MODEL_DEFAULT).strip() or ANTHROPIC_MODEL_DEFAULT
    t0 = time.perf_counter()
    try:
        r = await cx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": k,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
                **_hdr(tenant_id),
            },
            json={
                "model": model,
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
        log_timing(
            "anthropic call completed",
            subsystem="council",
            operation="provider_anthropic",
            provider="anthropic",
            duration_ms=int((time.perf_counter() - t0) * 1000),
            outcome="ok",
            model=model,
        )
        return txt, _cost_claude(pi, po)
    except Exception as e:
        log_timing(
            "anthropic call failed",
            subsystem="council",
            operation="provider_anthropic",
            provider="anthropic",
            duration_ms=int((time.perf_counter() - t0) * 1000),
            outcome="error",
            category=classify_failure(e),
            model=model,
        )
        raise


async def _safe_expert(
    coro_factory,
    *,
    provider: str,
    label: str,
) -> tuple[str, float]:
    t0 = time.perf_counter()
    op = f"expert_{label.lower()}"
    try:
        text, cost = await coro_factory()
        duration_ms = int((time.perf_counter() - t0) * 1000)
        if text.startswith("Expert unavailable") or text.startswith("missing "):
            log_timing(
                f"{label} expert degraded",
                subsystem="council",
                operation=op,
                provider=provider,
                duration_ms=duration_ms,
                outcome="degraded",
            )
        return text, cost
    except Exception as e:
        category = classify_failure(e)
        duration_ms = int((time.perf_counter() - t0) * 1000)
        _log_provider_failure(
            provider=provider,
            subsystem="council",
            exc=e,
            message=f"{label} expert failed",
        )
        log_timing(
            f"{label} expert degraded",
            subsystem="council",
            operation=op,
            provider=provider,
            duration_ms=duration_ms,
            outcome="degraded",
            category=category,
        )
        return _degraded_expert_response(category), 0.0


def _synthesis_user_prompt(question: str, legal: str, business: str, strategy: str) -> str:
    return f"""Three experts answered this question:
Question: {question}

⚖️ Legal Advisor: {legal}
💼 Business Advisor: {business}
🎯 Strategy Advisor: {strategy}

Tasks:
1. Write ONE clear synthesized recommendation in 2-3 sentences.
2. Identify the strongest consensus points between experts.
3. Identify the primary disagreement if one exists (or null).
4. Estimate agreement level between experts.

Return ONLY this JSON format:
{{
  "recommendation": "...",
  "consensus_points": "...",
  "main_disagreement": null,
  "agreement_estimate": "3/3"
}}"""


def _parse_synthesis_json(raw: str) -> dict[str, Any]:
    try:
        data = json.loads(raw.strip())
    except json.JSONDecodeError:
        return {
            "recommendation": raw,
            "consensus_points": None,
            "main_disagreement": None,
            "agreement_estimate": "unknown",
        }
    if not isinstance(data, dict):
        return {
            "recommendation": raw,
            "consensus_points": None,
            "main_disagreement": None,
            "agreement_estimate": "unknown",
        }

    def pick(key: str) -> Any:
        return data.get(key)

    reco = pick("recommendation")
    cp = pick("consensus_points")
    md = pick("main_disagreement")
    ae = pick("agreement_estimate")

    if cp is not None and not isinstance(cp, str):
        cp = str(cp)
    if md is not None and not isinstance(md, str):
        md = json.dumps(md) if isinstance(md, (dict, list)) else str(md)

    return {
        "recommendation": str(reco).strip() if reco is not None else raw,
        "consensus_points": cp,
        "main_disagreement": md,
        "agreement_estimate": str(ae) if ae is not None else "unknown",
    }


async def _persist_synthesis_ko(tenant_id: str, question: str, synthesis: dict[str, Any]) -> None:
    org = uuid.UUID(tenant_id)
    title = (question[:100] if question else "Council synthesis")[:512]

    async def _do() -> None:
        async with get_db_session() as session:
            await session.execute(text("SELECT set_config('app.current_org_id', :v, true)"), {"v": str(org)})
            session.add(
                KnowledgeObject(
                    org_id=org,
                    type="synthesis",
                    status="evolving",
                    confidence=None,
                    title=title,
                    content=dict(synthesis),
                )
            )
            await session.commit()

    try:
        async with measure(subsystem="council", operation="db_persist_synthesis", provider="database"):
            await asyncio.wait_for(_do(), timeout=DB_OPERATION_TIMEOUT_S)
    except Exception as e:
        log_warning(
            "council synthesis persist failed",
            subsystem="council",
            provider="database",
            category=classify_failure(e),
            exc=e,
            operation="db_persist_synthesis",
            outcome="error",
        )


async def run_council(question: str, tenant_id: str) -> dict[str, Any]:
    timeout = httpx.Timeout(HTTP_CLIENT_TIMEOUT_S)
    async with httpx.AsyncClient(timeout=timeout) as cx:
        ra, ca = await _safe_expert(
            lambda: _legal(cx, question, tenant_id),
            provider="anthropic",
            label="Legal",
        )
        rb, cb = await _safe_expert(
            lambda: _openai(cx, "gpt-4o", S_BIZ, question, tenant_id),
            provider="openai",
            label="Business",
        )
        rc, cc = await _safe_expert(
            lambda: _openai(cx, "gpt-4o-mini", S_STRAT, question, tenant_id),
            provider="openai",
            label="Strategy",
        )

        synthesis: dict[str, Any] | None = None
        synth_cost = 0.0
        model_syn = os.getenv("SYNTHESIS_MODEL", SYNTHESIS_MODEL_DEFAULT).strip() or SYNTHESIS_MODEL_DEFAULT

        try:
            async with measure(subsystem="council", operation="synthesis", provider="openai"):
                raw_syn, synth_cost = await asyncio.wait_for(
                    _openai(
                        cx,
                        model_syn,
                        SYNTHESIS_SYSTEM,
                        _synthesis_user_prompt(question, ra, rb, rc),
                        tenant_id,
                    ),
                    timeout=SYNTHESIS_TIMEOUT_S,
                )
            synthesis = _parse_synthesis_json(raw_syn)
        except TimeoutError as e:
            log_warning(
                "council synthesis timed out",
                subsystem="council",
                provider="openai",
                category="timeout",
                exc=e,
                operation="synthesis",
                outcome="timeout",
            )
        except Exception as e:
            log_warning(
                "council synthesis failed",
                subsystem="council",
                provider="openai",
                category=classify_failure(e),
                exc=e,
                operation="synthesis",
                outcome="error",
            )

    if synthesis is not None:
        await _persist_synthesis_ko(tenant_id, question, synthesis)

    payload = {
        "question": question,
        "council": [
            {"expert": "Legal Advisor", "model": "claude", "response": ra},
            {"expert": "Business Advisor", "model": "gpt-4o", "response": rb},
            {"expert": "Strategy Advisor", "model": "gpt-4o-mini", "response": rc},
        ],
        "synthesis": synthesis,
        "cost_usd": round(ca + cb + cc + synth_cost, 6),
    }
    if get_request_id():
        return attach_request_id(payload)
    return payload
