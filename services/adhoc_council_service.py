"""Ad-hoc council: add model perspectives and BEN synthesis on an existing thread."""
from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any, Literal

import httpx
from fastapi import HTTPException, status

from services.adhoc_transcript import (
    AdhocExpertClaim,
    AdhocSessionSnapshot,
    build_adhoc_session_snapshot,
    build_transcript_lines,
    format_expert_lines_for_synthesis,
    load_thread_index,
)
from services.council_service import (
    SYNTHESIS_MODEL_DEFAULT,
    SYNTHESIS_SYSTEM,
    _openai_completion,
    _parse_synthesis_json,
)
from services.message_format import (
    build_adhoc_expert_display_text,
    build_synthesis_display_text,
    provider_display_label,
)
from services.model_gateway import normalize_chat_provider_id, route_request
from services.ops.request_context import attach_request_id
from services.ops.timeouts import HTTP_CLIENT_TIMEOUT_S, SYNTHESIS_TIMEOUT_S
from services.thread_service import append_adhoc_expert_message, append_adhoc_synthesis_message

AdhocSynthesisMode = Literal["consensus", "single_voice_wrap"]

ADHOC_EXPERT_PROMPT = """You are {provider_label}, joining an ongoing BEN workspace thread as an additional perspective.

You are NOT the primary chat assistant and NOT BEN final synthesis.

Read the conversation transcript below (prior user messages, chat replies, council experts, and any earlier ad-hoc models in this session).

Your task:
- Add a distinct, substantive perspective the thread does not already contain.
- Surface risks, tradeoffs, disagreements, or missing considerations.
- Do not repeat prior speakers verbatim or claim unanimous consensus.
- If experts failed or were unavailable, do not count them as agreeing.
- Match the language of the user's most recent message unless they explicitly asked for another language.
- Be direct. Prefer 2–4 short paragraphs over a long essay.

--- Conversation so far ---
{transcript}
--- End transcript ---

The user invited an additional model to this thread. Provide your perspective now."""


def build_adhoc_synthesis_user_prompt(
    snapshot: AdhocSessionSnapshot,
    *,
    mode: AdhocSynthesisMode = "consensus",
) -> str:
    expert_block = format_expert_lines_for_synthesis(snapshot.experts)
    ok_count = sum(1 for e in snapshot.experts if e.outcome == "ok")
    anchor = snapshot.anchor_user_text.strip() or "(no user anchor in window)"
    background = snapshot.background_tail.strip()
    mode_note = ""
    if mode == "single_voice_wrap":
        mode_note = (
            "\nNote: Only one additional AI perspective participated. "
            "Do not claim multi-expert consensus; produce a concise wrap-up.\n"
        )
    background_block = f"\nPrior thread context (trimmed):\n{background}\n" if background else ""
    return f"""The user ran an ad-hoc multi-model deliberation on an existing thread.{mode_note}

Anchor question / topic:
{anchor}

Session experts ({ok_count} of {len(snapshot.experts)} with outcome=ok):
{expert_block}
{background_block}
Synthesize across the session experts above. Use only available (outcome=ok) voices for agreement claims.
Return ONLY valid JSON per the synthesis schema."""


def _outcome_from_provider_response(content: str, provider_used: str) -> str:
    text = (content or "").strip()
    if text.startswith("missing ") or "not configured" in text.lower():
        return "degraded"
    if not text and provider_used:
        return "error"
    return "ok"


def _annotate_adhoc_synthesis(
    synthesis: dict[str, Any],
    experts: tuple[AdhocExpertClaim, ...],
) -> dict[str, Any]:
    out = dict(synthesis)
    ok = sum(1 for e in experts if e.outcome == "ok")
    total = len(experts)
    unavailable = total - ok
    out["available_experts"] = ok
    out["unavailable_experts"] = unavailable
    out["synthesis_mode"] = "adhoc_consensus" if ok >= 2 else "adhoc_partial"
    out["consensus_available"] = ok >= 2
    if ok and not out.get("agreement_estimate"):
        out["agreement_estimate"] = f"{ok}/{ok} available"
    return out


async def run_adhoc_expert(
    org_id: uuid.UUID,
    thread_id: uuid.UUID,
    *,
    session_id: uuid.UUID,
    provider_id: str,
    tenant_id: str,
    tier: str,
) -> dict[str, Any]:
    """Load DB transcript, call one provider, persist adhoc_expert row."""
    try:
        normalized_provider = normalize_chat_provider_id(provider_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if normalized_provider is None:
        raise HTTPException(status_code=400, detail="provider_id is required")

    session_str = str(session_id)
    index = await load_thread_index(org_id, thread_id)
    if index.session_closed(session_str):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "adhoc_session_closed",
                "message": "This ad-hoc session already has a synthesis.",
            },
        )

    transcript = build_transcript_lines(index.messages)
    provider_label = provider_display_label(normalized_provider) or normalized_provider
    prompt = ADHOC_EXPERT_PROMPT.format(provider_label=provider_label, transcript=transcript)

    raw = await route_request(prompt, tenant_id, tier, provider_id=normalized_provider)
    response_text = str(raw.get("content") or "")
    model_used = str(raw.get("model_used") or "")
    provider_used = str(raw.get("provider_used") or "")
    cost_usd = float(raw.get("cost_usd") or 0)
    outcome = _outcome_from_provider_response(response_text, provider_used)

    existing = index.experts_for(session_str)
    sequence = len(existing) + 1
    display = build_adhoc_expert_display_text(normalized_provider, model_used, response_text)

    message_id = await append_adhoc_expert_message(
        org_id,
        thread_id,
        session_id=session_str,
        provider_id=normalized_provider,
        response=response_text,
        provider_used=provider_used,
        model=model_used,
        outcome=outcome,
        cost_usd=cost_usd,
        sequence=sequence,
        display_content=display,
    )

    payload: dict[str, Any] = {
        "session_id": session_str,
        "message_id": str(message_id),
        "provider_id": normalized_provider,
        "model_used": model_used,
        "provider_used": provider_used,
        "response": response_text,
        "content": display,
        "cost_usd": round(cost_usd, 6),
        "outcome": outcome,
        "sequence": sequence,
    }
    return attach_request_id(payload)


async def run_adhoc_synthesize(
    org_id: uuid.UUID,
    thread_id: uuid.UUID,
    *,
    session_id: uuid.UUID,
    tenant_id: str,
    mode: AdhocSynthesisMode = "consensus",
) -> dict[str, Any]:
    """Synthesize session ad-hoc experts using slim snapshot context."""
    session_str = str(session_id)
    index = await load_thread_index(org_id, thread_id)
    snapshot = build_adhoc_session_snapshot(index, session_str)

    if snapshot.closed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "adhoc_session_closed",
                "message": "This ad-hoc session already has a synthesis.",
            },
        )

    voices = len(snapshot.voice_keys)
    if mode == "consensus" and voices < 2:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "adhoc_synthesis_requires_multiple_voices",
                "message": "At least two distinct AI perspectives are required before synthesis.",
                "voices_detected": voices,
            },
        )

    if len(snapshot.experts) < 1:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "adhoc_synthesis_requires_experts",
                "message": "No ad-hoc expert responses found for this session.",
            },
        )

    if mode == "single_voice_wrap" and len(snapshot.experts) < 1:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "adhoc_synthesis_requires_experts",
                "message": "No ad-hoc expert responses found for this session.",
            },
        )

    user_prompt = build_adhoc_synthesis_user_prompt(snapshot, mode=mode)
    model_syn = os.getenv("SYNTHESIS_MODEL", SYNTHESIS_MODEL_DEFAULT).strip() or SYNTHESIS_MODEL_DEFAULT

    timeout = httpx.Timeout(HTTP_CLIENT_TIMEOUT_S)
    async with httpx.AsyncClient(timeout=timeout) as cx:
        try:
            raw_syn, synth_cost = await asyncio.wait_for(
                _openai_completion(cx, model_syn, SYNTHESIS_SYSTEM, user_prompt, tenant_id),
                timeout=SYNTHESIS_TIMEOUT_S,
            )
        except (TimeoutError, asyncio.TimeoutError) as exc:
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail={"error": "adhoc_synthesis_timeout", "message": "Ad-hoc synthesis timed out."},
            ) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={"error": "adhoc_synthesis_failed", "message": "Ad-hoc synthesis failed."},
            ) from exc

    experts = snapshot.experts
    synthesis = _parse_synthesis_json(raw_syn, [])
    synthesis = _annotate_adhoc_synthesis(synthesis, experts)
    any_failed = any(e.outcome != "ok" for e in experts)
    display_text = build_synthesis_display_text(synthesis, any_expert_failed=any_failed)
    if not display_text.strip().startswith("🧠"):
        display_text = f"🧠 BEN Ad-hoc Synthesis\n\n{display_text}"

    message_id = await append_adhoc_synthesis_message(
        org_id,
        thread_id,
        session_id=session_str,
        synthesis=synthesis,
        display_text=display_text,
        cost_usd=synth_cost,
    )

    payload: dict[str, Any] = {
        "session_id": session_str,
        "message_id": str(message_id),
        "synthesis": synthesis,
        "display_text": display_text,
        "content": display_text,
        "cost_usd": round(float(synth_cost), 6),
        "next_steps": synthesis.get("next_steps") or [],
        "voices_detected": voices,
        "mode": mode,
    }
    return attach_request_id(payload)
