"""T12 Council: three experts in parallel, then BEN synthesis (sequential).

Synthesis is isolated: failures return expert results only (synthesis null).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any, Literal

import httpx
from sqlalchemy import text

from database.connection import get_db_session
from database.models import KnowledgeObject
from services.ops.failure_classification import (
    FAILURE_CONFIG_ERROR,
    FAILURE_TIMEOUT,
    classify_failure,
)
from services.ops.request_context import attach_request_id, get_request_id
from services.ops.structured_log import log_warning
from services.message_format import build_synthesis_display_text
from services.ops.timing import log_timing, measure
from services.thread_service import persist_council_transcript, resolve_thread_id
from services.ops.timeouts import (
    COUNCIL_TOTAL_TIMEOUT_S,
    DB_OPERATION_TIMEOUT_S,
    EXPERT_CALL_TIMEOUT_S,
    HTTP_CLIENT_TIMEOUT_S,
    SYNTHESIS_TIMEOUT_S,
)

ExpertOutcome = Literal["ok", "degraded", "timeout", "error"]

S_LEGAL = "You are a sharp legal advisor.\nAnalyze risks, contracts, compliance.\nBe direct. Max 3 sentences."
S_BIZ = "You are a senior business strategist.\nAnalyze market, revenue, growth.\nBe direct. Max 3 sentences."
S_STRAT = "You are a strategic thinker.\nAnalyze long-term implications and risks.\nBe direct. Max 3 sentences."

ANTHROPIC_MODEL_DEFAULT = "claude-sonnet-4-6"
SYNTHESIS_MODEL_DEFAULT = "gpt-4o-mini"
BUSINESS_MODEL = "gpt-4o"
GEMINI_MODEL_DEFAULT = "gemini-2.5-flash"

SYNTHESIS_SYSTEM = """You are BEN, a Cognitive Operating System.
Your job is to synthesize expert opinions into structured organizational reasoning.
Return ONLY valid JSON. No markdown. No explanations outside the JSON.

Rules — honesty (must always hold):
- Only count experts with outcome=ok as agreeing experts.
- Do not claim 3/3 or 2/3 style agreement if any expert timed out or was unavailable.
- If any expert failed, say in consensus_points that synthesis uses only available experts.
- agreement_estimate must reflect available experts only (e.g. "2/2 available", "3/3 available", "unknown").

Rules — reasoning preservation (must always hold):
- Preserve distinct expert lenses. Do NOT collapse Legal, Business/operational, and Strategy into one generic voice.
- If experts agree on a conclusion but differ on WHY, capture those WHY differences in disagreement_points and/or domain sections.
- agreement vs rationale: consensus_points = what aligns; disagreement_points = where they diverge (including rationale), without fabricating conflict.
- Use domain sections to hold domain-specific priorities and risk framing (legal vs operational vs strategic vs infrastructure where relevant).
- Be faithful: do not invent unanimous consensus when experts emphasized different risks or tradeoffs.
- minority_or_unique_views: views articulated by one expert or clearly not shared by others (or null).
- Omit optional keys or set them to null if not applicable."""

SYNTHESIS_OPTIONAL_STRING_KEYS = (
    "shared_recommendation",
    "disagreement_points",
    "legal_reasoning",
    "operational_reasoning",
    "strategic_reasoning",
    "infrastructure_reasoning",
    "minority_or_unique_views",
)


@dataclass
class ExpertResult:
    expert: str
    provider: str
    model: str
    outcome: ExpertOutcome
    response: str
    cost: float

    def to_member(self) -> dict[str, Any]:
        return {
            "expert": self.expert,
            "provider": self.provider,
            "model": self.model,
            "outcome": self.outcome,
            "response": self.response,
        }


def _hdr(tenant_id: str) -> dict[str, str]:
    return {"X-BEN-Tenant": tenant_id}


def _cost_oai(model: str, pi: int, po: int) -> float:
    ir, or_ = {"gpt-4o": (2.5e-6, 10e-6), "gpt-4o-mini": (0.15e-6, 0.60e-6)}.get(model, (0.5e-6, 1.5e-6))
    return ir * pi + or_ * po


def _cost_claude(pi: int, po: int) -> float:
    return 3e-6 * pi + 15e-6 * po


def _cost_gemini(pi: int, po: int) -> float:
    return 0.1e-6 * pi + 0.4e-6 * po


def _strategy_gemini_model() -> str:
    return (
        os.getenv("GEMINI_MODEL", "").strip()
        or os.getenv("GOOGLE_MODEL", "").strip()
        or GEMINI_MODEL_DEFAULT
    )


def _degraded_expert_response(category: str) -> str:
    return f"Expert unavailable ({category}). Please retry or check configuration."


def _category_to_outcome(category: str) -> ExpertOutcome:
    if category == FAILURE_TIMEOUT:
        return "timeout"
    if category == FAILURE_CONFIG_ERROR:
        return "degraded"
    return "error"


def _log_provider_failure(*, provider: str, subsystem: str, exc: BaseException | None = None, message: str) -> None:
    category = classify_failure(exc) if exc else FAILURE_CONFIG_ERROR
    log_warning(message, subsystem=subsystem, provider=provider, category=category, exc=exc)


async def _openai_completion(
    cx: httpx.AsyncClient, model: str, system: str, q: str, tenant_id: str
) -> tuple[str, float]:
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


async def _gemini_completion(
    cx: httpx.AsyncClient, model: str, system: str, q: str, tenant_id: str
) -> tuple[str, float]:
    api_key = os.getenv("GOOGLE_API_KEY", "").strip()
    if not api_key:
        _log_provider_failure(provider="google", subsystem="council", message="missing GOOGLE_API_KEY")
        return "missing GOOGLE_API_KEY", 0.0
    t0 = time.perf_counter()
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    try:
        r = await cx.post(
            url,
            params={"key": api_key},
            headers=_hdr(tenant_id),
            json={
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": q}]}],
            },
        )
        r.raise_for_status()
        d = r.json()
        parts = ((d.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
        txt = "".join(p.get("text", "") for p in parts)
        m = d.get("usageMetadata") or {}
        pi, po = int(m.get("promptTokenCount", 0)), int(m.get("candidatesTokenCount", 0))
        log_timing(
            "gemini call completed",
            subsystem="council",
            operation="provider_gemini",
            provider="google",
            duration_ms=int((time.perf_counter() - t0) * 1000),
            outcome="ok",
            model=model,
        )
        return txt, _cost_gemini(pi, po)
    except Exception as e:
        log_timing(
            "gemini call failed",
            subsystem="council",
            operation="provider_gemini",
            provider="google",
            duration_ms=int((time.perf_counter() - t0) * 1000),
            outcome="error",
            category=classify_failure(e),
            model=model,
        )
        raise


async def _gemini_expert(
    cx: httpx.AsyncClient,
    model: str,
    system: str,
    q: str,
    tenant_id: str,
    *,
    expert: str,
) -> ExpertResult:
    if not os.getenv("GOOGLE_API_KEY", "").strip():
        _log_provider_failure(provider="google", subsystem="council", message="missing GOOGLE_API_KEY")
        return ExpertResult(
            expert=expert,
            provider="google",
            model=model,
            outcome="degraded",
            response="missing GOOGLE_API_KEY",
            cost=0.0,
        )
    text, cost = await _gemini_completion(cx, model, system, q, tenant_id)
    if text.startswith("missing "):
        return ExpertResult(expert=expert, provider="google", model=model, outcome="degraded", response=text, cost=cost)
    return ExpertResult(expert=expert, provider="google", model=model, outcome="ok", response=text, cost=cost)


async def _openai_expert(
    cx: httpx.AsyncClient,
    model: str,
    system: str,
    q: str,
    tenant_id: str,
    *,
    expert: str,
) -> ExpertResult:
    k = os.getenv("OPENAI_API_KEY", "").strip()
    if not k:
        _log_provider_failure(provider="openai", subsystem="council", message="missing OPENAI_API_KEY")
        return ExpertResult(
            expert=expert,
            provider="openai",
            model=model,
            outcome="degraded",
            response="missing OPENAI_API_KEY",
            cost=0.0,
        )
    text, cost = await _openai_completion(cx, model, system, q, tenant_id)
    if text.startswith("missing "):
        return ExpertResult(expert=expert, provider="openai", model=model, outcome="degraded", response=text, cost=cost)
    return ExpertResult(expert=expert, provider="openai", model=model, outcome="ok", response=text, cost=cost)


async def _legal(cx: httpx.AsyncClient, q: str, tenant_id: str) -> ExpertResult:
    k = os.getenv("ANTHROPIC_API_KEY", "").strip()
    model = os.getenv("ANTHROPIC_MODEL", ANTHROPIC_MODEL_DEFAULT).strip() or ANTHROPIC_MODEL_DEFAULT
    if not k:
        _log_provider_failure(provider="anthropic", subsystem="council", message="missing ANTHROPIC_API_KEY")
        return ExpertResult(
            expert="Legal Advisor",
            provider="anthropic",
            model=model,
            outcome="degraded",
            response="missing ANTHROPIC_API_KEY",
            cost=0.0,
        )
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
        return ExpertResult(
            expert="Legal Advisor",
            provider="anthropic",
            model=model,
            outcome="ok",
            response=txt,
            cost=_cost_claude(pi, po),
        )
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
    expert: str,
    model: str,
) -> ExpertResult:
    t0 = time.perf_counter()
    op = f"expert_{label.lower()}"
    try:
        result = await asyncio.wait_for(coro_factory(), timeout=EXPERT_CALL_TIMEOUT_S)
        if not isinstance(result, ExpertResult):
            raise TypeError("expert coroutine must return ExpertResult")
        duration_ms = int((time.perf_counter() - t0) * 1000)
        if result.outcome != "ok":
            log_timing(
                f"{label} expert degraded",
                subsystem="council",
                operation=op,
                provider=provider,
                duration_ms=duration_ms,
                outcome="degraded",
            )
        return result
    except Exception as e:
        category = classify_failure(e)
        outcome = _category_to_outcome(category)
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
        return ExpertResult(
            expert=expert,
            provider=provider,
            model=model,
            outcome=outcome,
            response=_degraded_expert_response(category),
            cost=0.0,
        )


def _expert_line_for_synthesis(member: ExpertResult) -> str:
    icon = {"Legal Advisor": "⚖️", "Business Advisor": "💼", "Strategy Advisor": "🎯"}.get(member.expert, "•")
    if member.outcome == "ok":
        return f"{icon} {member.expert} (outcome=ok): {member.response}"
    return (
        f"{icon} {member.expert} (outcome={member.outcome}, UNAVAILABLE — do not count in agreement): "
        f"{member.response}"
    )


def _synthesis_user_prompt(question: str, experts: list[ExpertResult]) -> str:
    lines = "\n".join(_expert_line_for_synthesis(e) for e in experts)
    ok_count = sum(1 for e in experts if e.outcome == "ok")
    return f"""Experts responded to this question ({ok_count} of {len(experts)} with outcome=ok):
Question: {question}

{lines}

Tasks:
1. recommendation: one short cross-cutting summary (2-4 sentences) — backward compatible.
2. shared_recommendation: optional; if present, should align with recommendation (may be slightly fuller). Omit or null if redundant.
3. consensus_points: what experts with outcome=ok agree on (facts, actions, constraints) — not forced unanimity.
4. main_disagreement: primary tension or null (string).
5. disagreement_points: optional string — rationale differences, priority clashes, or "agree on outcome, disagree on why" (null if N/A).
6. legal_reasoning: optional — distilled legal/compliance lens from Legal Advisor (null if unavailable or not outcome=ok).
7. operational_reasoning: optional — business/operations lens from Business Advisor (same rule).
8. strategic_reasoning: optional — long-horizon / positioning lens from Strategy Advisor (same rule).
9. infrastructure_reasoning: optional — only if an expert addressed infra/tech debt/serving (often null).
10. minority_or_unique_views: optional — non-majority or single-expert emphasis (null if none).
11. agreement_estimate: use available experts only (e.g. "{ok_count}/{ok_count} available" or "unknown"). Never claim full panel when any expert is not outcome=ok.

Return ONLY this JSON (optional keys may be null or omitted):
{{
  "recommendation": "...",
  "shared_recommendation": null,
  "consensus_points": "...",
  "main_disagreement": null,
  "disagreement_points": null,
  "legal_reasoning": null,
  "operational_reasoning": null,
  "strategic_reasoning": null,
  "infrastructure_reasoning": null,
  "minority_or_unique_views": null,
  "agreement_estimate": "{ok_count}/{ok_count} available"
}}"""


def _honest_agreement_estimate(experts: list[ExpertResult], synthesis: dict[str, Any]) -> dict[str, Any]:
    ok_count = sum(1 for e in experts if e.outcome == "ok")
    total = len(experts)
    ae = str(synthesis.get("agreement_estimate") or "unknown")
    if ok_count < total:
        synthesis["agreement_estimate"] = f"{ok_count}/{ok_count} available" if ok_count else "unknown"
        return synthesis
    misleading = re.search(r"(\d+)\s*/\s*3\b", ae)
    if misleading and int(misleading.group(1)) > ok_count:
        synthesis["agreement_estimate"] = f"{ok_count}/{ok_count} available" if ok_count else "unknown"
    return synthesis


def _norm_synth_optional_str(val: Any) -> str | None:
    if val is None:
        return None
    if isinstance(val, (dict, list)):
        if not val:
            return None
        s = json.dumps(val)
        return s if s.strip() else None
    s = str(val).strip()
    return s or None


def _parse_synthesis_json(raw: str, experts: list[ExpertResult]) -> dict[str, Any]:
    try:
        data = json.loads(raw.strip())
    except json.JSONDecodeError:
        data = {
            "recommendation": raw,
            "consensus_points": None,
            "main_disagreement": None,
            "agreement_estimate": "unknown",
        }
    if not isinstance(data, dict):
        data = {
            "recommendation": raw,
            "consensus_points": None,
            "main_disagreement": None,
            "agreement_estimate": "unknown",
        }

    def pick(key: str) -> Any:
        return data.get(key)

    shared = _norm_synth_optional_str(pick("shared_recommendation"))
    reco = _norm_synth_optional_str(pick("recommendation")) or shared
    if not reco:
        reco = raw if isinstance(raw, str) else str(raw)
    cp = _norm_synth_optional_str(pick("consensus_points"))
    md = pick("main_disagreement")
    if md is not None and not isinstance(md, str):
        md = json.dumps(md) if isinstance(md, (dict, list)) else str(md)
    else:
        md = _norm_synth_optional_str(md)
    ae = pick("agreement_estimate")

    parsed: dict[str, Any] = {
        "recommendation": reco,
        "consensus_points": cp,
        "main_disagreement": md,
        "agreement_estimate": str(ae) if ae is not None else "unknown",
    }

    if shared:
        parsed["shared_recommendation"] = shared

    for key in SYNTHESIS_OPTIONAL_STRING_KEYS:
        if key == "shared_recommendation":
            continue
        v = _norm_synth_optional_str(pick(key))
        if v:
            parsed[key] = v

    return _honest_agreement_estimate(experts, parsed)


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


def _build_council_payload(
    question: str,
    *,
    experts: list[ExpertResult],
    synthesis: dict[str, Any] | None,
    synth_cost: float,
) -> dict[str, Any]:
    payload = {
        "question": question,
        "council": [e.to_member() for e in experts],
        "synthesis": synthesis,
        "cost_usd": round(sum(e.cost for e in experts) + synth_cost, 6),
    }
    if get_request_id():
        return attach_request_id(payload)
    return payload


def _timeout_degraded_experts() -> list[ExpertResult]:
    msg = _degraded_expert_response("timeout")
    return [
        ExpertResult("Legal Advisor", "anthropic", ANTHROPIC_MODEL_DEFAULT, "timeout", msg, 0.0),
        ExpertResult("Business Advisor", "openai", BUSINESS_MODEL, "timeout", msg, 0.0),
        ExpertResult("Strategy Advisor", "google", _strategy_gemini_model(), "timeout", msg, 0.0),
    ]


async def _persist_council_thread_if_needed(
    tenant_id: str,
    thread_id: uuid.UUID | None,
    question: str,
    payload: dict[str, Any],
) -> uuid.UUID | None:
    org_uuid = uuid.UUID(tenant_id)
    tid = thread_id
    if tid is None:
        tid = await resolve_thread_id(org_uuid, None, title=(question[:100] or "Council")[:512])
    members = payload.get("council") or []
    synthesis = payload.get("synthesis") if isinstance(payload.get("synthesis"), dict) else None
    any_failed = any((m.get("outcome") or "ok") != "ok" for m in members)
    display = build_synthesis_display_text(synthesis, any_expert_failed=any_failed) if synthesis else ""
    try:
        await persist_council_transcript(
            org_uuid,
            tid,
            question,
            council_members=members,
            synthesis=synthesis,
            total_cost_usd=float(payload.get("cost_usd") or 0),
            synthesis_display_text=display,
        )
        return tid
    except Exception as e:
        log_warning(
            "council thread persist failed",
            subsystem="council",
            provider="database",
            category=classify_failure(e),
            exc=e,
            operation="persist_council_thread",
            outcome="error",
        )
    return None


async def _run_council_inner(
    question: str,
    tenant_id: str,
    *,
    thread_id: uuid.UUID | None = None,
    experts_out: list[list[ExpertResult]],
    synthesis_out: list[dict[str, Any] | None],
    synth_cost_out: list[float],
) -> dict[str, Any]:
    """Council body; writes partial state for outer-timeout fallback."""
    legal_model = os.getenv("ANTHROPIC_MODEL", ANTHROPIC_MODEL_DEFAULT).strip() or ANTHROPIC_MODEL_DEFAULT
    strategy_model = _strategy_gemini_model()
    timeout = httpx.Timeout(HTTP_CLIENT_TIMEOUT_S)
    async with httpx.AsyncClient(timeout=timeout) as cx:
        expert_results: list[ExpertResult] = list(
            await asyncio.gather(
                _safe_expert(
                    lambda: _legal(cx, question, tenant_id),
                    provider="anthropic",
                    label="Legal",
                    expert="Legal Advisor",
                    model=legal_model,
                ),
                _safe_expert(
                    lambda: _openai_expert(cx, BUSINESS_MODEL, S_BIZ, question, tenant_id, expert="Business Advisor"),
                    provider="openai",
                    label="Business",
                    expert="Business Advisor",
                    model=BUSINESS_MODEL,
                ),
                _safe_expert(
                    lambda: _gemini_expert(
                        cx, strategy_model, S_STRAT, question, tenant_id, expert="Strategy Advisor"
                    ),
                    provider="google",
                    label="Strategy",
                    expert="Strategy Advisor",
                    model=strategy_model,
                ),
            )
        )
        experts_out.append(expert_results)

        synthesis: dict[str, Any] | None = None
        synth_cost = 0.0
        model_syn = os.getenv("SYNTHESIS_MODEL", SYNTHESIS_MODEL_DEFAULT).strip() or SYNTHESIS_MODEL_DEFAULT

        try:
            async with measure(subsystem="council", operation="synthesis", provider="openai"):
                raw_syn, synth_cost = await asyncio.wait_for(
                    _openai_completion(
                        cx,
                        model_syn,
                        SYNTHESIS_SYSTEM,
                        _synthesis_user_prompt(question, expert_results),
                        tenant_id,
                    ),
                    timeout=SYNTHESIS_TIMEOUT_S,
                )
            synthesis = _parse_synthesis_json(raw_syn, expert_results)
        except (TimeoutError, asyncio.TimeoutError) as e:
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

        synthesis_out.append(synthesis)
        synth_cost_out.append(synth_cost)

    if synthesis is not None:
        await _persist_synthesis_ko(tenant_id, question, synthesis)

    payload = _build_council_payload(
        question,
        experts=expert_results,
        synthesis=synthesis,
        synth_cost=synth_cost,
    )
    await _persist_council_thread_if_needed(tenant_id, thread_id, question, payload)
    return payload


async def run_council(question: str, tenant_id: str, *, thread_id: uuid.UUID | None = None) -> dict[str, Any]:
    experts_out: list[list[ExpertResult]] = []
    synthesis_out: list[dict[str, Any] | None] = []
    synth_cost_out: list[float] = []

    try:
        return await asyncio.wait_for(
            _run_council_inner(
                question,
                tenant_id,
                thread_id=thread_id,
                experts_out=experts_out,
                synthesis_out=synthesis_out,
                synth_cost_out=synth_cost_out,
            ),
            timeout=COUNCIL_TOTAL_TIMEOUT_S,
        )
    except (TimeoutError, asyncio.TimeoutError) as e:
        log_warning(
            "council total timed out",
            subsystem="council",
            provider="openai",
            category="timeout",
            exc=e,
            operation="council_total",
            outcome="timeout",
        )
        if experts_out:
            experts = experts_out[0]
            synthesis = synthesis_out[0] if synthesis_out else None
            synth_cost = synth_cost_out[0] if synth_cost_out else 0.0
        else:
            experts = _timeout_degraded_experts()
            synthesis = None
            synth_cost = 0.0
        payload = _build_council_payload(
            question,
            experts=experts,
            synthesis=synthesis,
            synth_cost=synth_cost,
        )
        await _persist_council_thread_if_needed(tenant_id, thread_id, question, payload)
        return payload
