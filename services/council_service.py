"""T12 Council: three experts in parallel, then BEN synthesis (sequential).

Synthesis is isolated: failures return expert results only (synthesis null).
Future: critique rounds, judge layers, confidence scoring, multi-stage deliberation.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from typing import Any

import httpx
from sqlalchemy import text

from database.connection import get_db_session
from database.models import KnowledgeObject

logger = logging.getLogger(__name__)

S_LEGAL = "You are a sharp legal advisor.\nAnalyze risks, contracts, compliance.\nBe direct. Max 3 sentences."
S_BIZ = "You are a senior business strategist.\nAnalyze market, revenue, growth.\nBe direct. Max 3 sentences."
S_STRAT = "You are a strategic thinker.\nAnalyze long-term implications and risks.\nBe direct. Max 3 sentences."

ANTHROPIC_MODEL_DEFAULT = "claude-sonnet-4-6"

SYNTHESIS_MODEL_DEFAULT = "gpt-4o-mini"
SYNTHESIS_TIMEOUT_S = 10.0

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
    model = os.getenv("ANTHROPIC_MODEL", ANTHROPIC_MODEL_DEFAULT).strip() or ANTHROPIC_MODEL_DEFAULT
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
    return txt, _cost_claude(pi, po)


def _unwrap(x: Any) -> tuple[str, float]:
    if isinstance(x, BaseException):
        return repr(x), 0.0
    return x


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

        synthesis: dict[str, Any] | None = None
        synth_cost = 0.0
        model_syn = os.getenv("SYNTHESIS_MODEL", SYNTHESIS_MODEL_DEFAULT).strip() or SYNTHESIS_MODEL_DEFAULT

        try:
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
        except TimeoutError:
            logger.warning("council synthesis timed out after %ss", SYNTHESIS_TIMEOUT_S)
        except Exception as e:
            logger.warning("council synthesis failed: %s", e, exc_info=True)

    if synthesis is not None:
        try:
            await _persist_synthesis_ko(tenant_id, question, synthesis)
        except Exception as e:
            logger.warning("council synthesis persist failed: %s", e, exc_info=True)

    return {
        "question": question,
        "council": [
            {"expert": "Legal Advisor", "model": "claude", "response": ra},
            {"expert": "Business Advisor", "model": "gpt-4o", "response": rb},
            {"expert": "Strategy Advisor", "model": "gpt-4o-mini", "response": rc},
        ],
        "synthesis": synthesis,
        "cost_usd": round(ca + cb + cc + synth_cost, 6),
    }
