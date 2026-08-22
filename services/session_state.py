"""State-aware session pre-fetch and compact context hydration for council runtime."""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from database.connection import get_db_session
from database.models import Message
from services.message_format import decode_message
from services.thread_service import _envelope_kind, _set_org

SESSION_STATE_TIMEOUT_S = 0.5
ACTIVE_CONTEXT_MAX_CHARS = 800
SYNTHESIS_EXCERPT_MAX_CHARS = 4000


@dataclass(frozen=True)
class SessionState:
    has_prior_synthesis: bool
    active_context: str | None
    synthesis_excerpt: str | None
    last_recommendation: str | None


def _extract_decision_summary(artifact: str) -> str:
    text = (artifact or "").strip()
    if not text:
        return ""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("|") and "---" not in stripped:
            cells = [c.strip() for c in stripped.strip("|").split("|") if c.strip()]
            if cells:
                return " — ".join(cells[:3])[:400]
        if len(stripped) > 12:
            return stripped[:400]
    return text[:400]


def _synthesis_payload(decoded: dict[str, Any]) -> dict[str, Any]:
    syn = decoded.get("synthesis")
    return syn if isinstance(syn, dict) else decoded


def _build_active_context(syn: dict[str, Any]) -> str | None:
    rec = str(syn.get("recommendation") or "").strip()
    artifact = str(syn.get("deliverable_artifact") or "").strip()
    summary = _extract_decision_summary(artifact)
    label = rec or summary
    if not label:
        return None
    parts = [f"Active Context: {label}."]
    if summary and rec and summary.lower() != rec.lower():
        parts.append(f"Prior deliverable state: {summary}.")
    parts.append(
        "Sequential follow-up — treat this as authoritative session state; do not ask for background."
    )
    packed = " ".join(parts)
    return packed[:ACTIVE_CONTEXT_MAX_CHARS]


def _synthesis_excerpt_from_decoded(decoded: dict[str, Any]) -> str:
    syn = _synthesis_payload(decoded)
    artifact = str(syn.get("deliverable_artifact") or "").strip()
    if artifact:
        return artifact[:SYNTHESIS_EXCERPT_MAX_CHARS]
    rec = str(syn.get("recommendation") or "").strip()
    if rec:
        return rec[:2000]
    content = str(decoded.get("content") or "").strip()
    return content[:2000] if content else ""


async def fetch_latest_synthesis_text(org_id: uuid.UUID, thread_id: uuid.UUID) -> str | None:
    """Fetch only the single most recent BEN synthesis text block for a thread."""
    try:
        async with asyncio.timeout(SESSION_STATE_TIMEOUT_S):
            async with get_db_session() as session:
                await _set_org(session, org_id)
                rows = list(
                    (
                        await session.execute(
                            select(Message)
                            .where(Message.org_id == org_id, Message.thread_id == thread_id)
                            .order_by(Message.created_at.desc())
                            .limit(20)
                        )
                    ).scalars()
                )
    except (TimeoutError, asyncio.TimeoutError):
        return None

    for message in rows:
        kind = _envelope_kind(message.role, message.content)
        if kind not in ("council_synthesis", "adhoc_synthesis"):
            continue
        decoded = decode_message(message.role, message.content)
        text = _synthesis_excerpt_from_decoded(decoded)
        if text:
            return text
    return None


async def prefetch_session_state(org_id: uuid.UUID, thread_id: uuid.UUID | None) -> SessionState | None:
    """Lightweight, non-blocking fetch of the latest BEN synthesis for a thread."""
    if thread_id is None:
        return None
    try:
        async with asyncio.timeout(SESSION_STATE_TIMEOUT_S):
            async with get_db_session() as session:
                await _set_org(session, org_id)
                rows = list(
                    (
                        await session.execute(
                            select(Message)
                            .where(Message.org_id == org_id, Message.thread_id == thread_id)
                            .order_by(Message.created_at.desc())
                            .limit(10)
                        )
                    ).scalars()
                )
    except (TimeoutError, asyncio.TimeoutError):
        return None

    if not rows:
        return None

    for message in rows:
        kind = _envelope_kind(message.role, message.content)
        if kind not in ("council_synthesis", "adhoc_synthesis"):
            continue
        decoded = decode_message(message.role, message.content)
        syn = _synthesis_payload(decoded)
        excerpt = _synthesis_excerpt_from_decoded(decoded)
        if not excerpt:
            continue
        rec = str(syn.get("recommendation") or "").strip() or None
        return SessionState(
            has_prior_synthesis=True,
            active_context=_build_active_context(syn),
            synthesis_excerpt=excerpt,
            last_recommendation=rec,
        )

    last_user: str | None = None
    last_asst: str | None = None
    for message in rows:
        if message.role == "user" and last_user is None:
            decoded = decode_message(message.role, message.content)
            last_user = str(decoded.get("content") or "")[:512]
        elif message.role == "assistant" and last_asst is None:
            decoded = decode_message(message.role, message.content)
            last_asst = str(decoded.get("content") or "")[:512]
        if last_user and last_asst:
            break

    if not last_user and not last_asst:
        return None

    preview = last_user or last_asst or ""
    active = (
        f"Active Context: continuing thread. Last turn: {preview[:360]}. "
        "Sequential follow-up — do not ask for background."
    )
    return SessionState(
        has_prior_synthesis=False,
        active_context=active[:ACTIVE_CONTEXT_MAX_CHARS],
        synthesis_excerpt=None,
        last_recommendation=None,
    )


def pack_active_context(state: SessionState | None) -> str | None:
    if state is None:
        return None
    ctx = (state.active_context or "").strip()
    return ctx or None


def inject_expert_prompt(question: str, active_context: str | None) -> str:
    ctx = (active_context or "").strip()
    q = (question or "").strip()
    if not ctx:
        return q
    return f"{ctx}\n\nCurrent request:\n{q}"


def session_state_stream_payload(state: SessionState | None) -> dict[str, Any]:
    if state is None:
        return {"hydrated": False, "has_prior_synthesis": False}
    preview = (state.active_context or "")[:120]
    return {
        "hydrated": bool(state.active_context),
        "has_prior_synthesis": state.has_prior_synthesis,
        "active_context_preview": preview,
    }


def continuity_from_session_state(state: SessionState | None) -> str | None:
    if state is None:
        return None
    if state.synthesis_excerpt:
        return f"Latest BEN synthesis deliverable:\n{state.synthesis_excerpt}"
    if state.active_context:
        return state.active_context
    return None
