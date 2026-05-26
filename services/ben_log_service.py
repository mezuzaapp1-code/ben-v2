"""Append-only BEN Log capture (non-blocking; does not affect /chat or /council)."""
from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from database.connection import get_db_session
from database.models import BEN_LOG_SOURCES, BenLogEvent
from services.ops.structured_log import log_warning

MAX_SUMMARY_LEN = 240
MAX_JSONB_BYTES = 65536


def _truncate_summary(value: str, *, max_len: int = MAX_SUMMARY_LEN) -> str:
    compact = " ".join((value or "").split())
    if len(compact) <= max_len:
        return compact
    return compact[: max_len - 3] + "..."


def _payload_ok(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not payload:
        return None
    try:
        size = len(json.dumps(payload, default=str).encode("utf-8"))
    except (TypeError, ValueError):
        return None
    if size > MAX_JSONB_BYTES:
        return {"truncated": True, "note": "payload exceeded capture limit"}
    return payload


async def _set_org(session: AsyncSession, org_id: uuid.UUID) -> None:
    await session.execute(text("SELECT set_config('app.current_org_id', :v, true)"), {"v": str(org_id)})


async def append_event(
    *,
    org_id: uuid.UUID,
    thread_id: uuid.UUID,
    event_type: str,
    summary: str,
    source: str,
    provider: str | None = None,
    model: str | None = None,
    actor_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> uuid.UUID | None:
    """Insert one BEN Log row. Never raises to callers."""
    if event_type not in (
        "prompt",
        "response",
        "decision",
        "rejection",
        "unresolved",
        "next_step",
        "context",
        "note",
    ):
        log_warning(
            "ben log capture skipped: invalid event_type",
            subsystem="ben_log",
            operation="append_event",
            outcome="skipped",
            category="validation_error",
        )
        return None
    if source not in BEN_LOG_SOURCES:
        log_warning(
            "ben log capture skipped: invalid source",
            subsystem="ben_log",
            operation="append_event",
            outcome="skipped",
            category="validation_error",
        )
        return None

    summary_norm = _truncate_summary(summary)
    if not summary_norm:
        log_warning(
            "ben log capture skipped: empty summary",
            subsystem="ben_log",
            operation="append_event",
            outcome="skipped",
            category="validation_error",
        )
        return None

    safe_payload = _payload_ok(payload)
    try:
        async with get_db_session() as session:
            await _set_org(session, org_id)
            row = BenLogEvent(
                org_id=org_id,
                thread_id=thread_id,
                event_type=event_type,
                summary=summary_norm,
                payload=safe_payload,
                source=source,
                provider=(provider or None),
                model=(model or None),
                actor_id=actor_id,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row.id
    except Exception as exc:
        log_warning(
            "ben log capture failed",
            subsystem="ben_log",
            operation="append_event",
            outcome="error",
            category="persistence_error",
            exc=exc,
        )
        return None


async def capture_chat_exchange(
    *,
    org_id: uuid.UUID,
    thread_id: uuid.UUID,
    user_message: str,
    assistant_response: str,
    provider_id: str | None,
    model_used: str | None,
    user_message_id: uuid.UUID | None = None,
    assistant_message_id: uuid.UUID | None = None,
) -> None:
    """Record chat prompt + response after messages are persisted. Never raises."""
    try:
        prompt_payload: dict[str, Any] = {}
        if user_message_id is not None:
            prompt_payload["message_id"] = str(user_message_id)
        await append_event(
            org_id=org_id,
            thread_id=thread_id,
            event_type="prompt",
            summary=_truncate_summary(user_message),
            source="chat",
            payload=prompt_payload or None,
        )

        response_payload: dict[str, Any] = {}
        if assistant_message_id is not None:
            response_payload["message_id"] = str(assistant_message_id)
        if provider_id:
            response_payload["provider_id"] = provider_id
        await append_event(
            org_id=org_id,
            thread_id=thread_id,
            event_type="response",
            summary=_truncate_summary(assistant_response),
            source="chat",
            provider=provider_id,
            model=model_used,
            payload=response_payload or None,
        )
    except Exception as exc:
        log_warning(
            "ben log chat capture failed",
            subsystem="ben_log",
            operation="capture_chat_exchange",
            outcome="error",
            category="persistence_error",
            exc=exc,
        )


async def capture_council_synthesis(
    *,
    org_id: uuid.UUID,
    thread_id: uuid.UUID,
    question: str,
    payload: dict[str, Any],
) -> None:
    """Record council synthesis outcome after transcript persist. Never raises."""
    try:
        await _capture_council_synthesis_inner(
            org_id=org_id,
            thread_id=thread_id,
            question=question,
            payload=payload,
        )
    except Exception as exc:
        log_warning(
            "ben log council capture failed",
            subsystem="ben_log",
            operation="capture_council_synthesis",
            outcome="error",
            category="persistence_error",
            exc=exc,
        )


async def _capture_council_synthesis_inner(
    *,
    org_id: uuid.UUID,
    thread_id: uuid.UUID,
    question: str,
    payload: dict[str, Any],
) -> None:
    synthesis = payload.get("synthesis") if isinstance(payload.get("synthesis"), dict) else None
    available = int(payload.get("available_experts") or 0)
    unavailable = int(payload.get("unavailable_experts") or 0)
    room = payload.get("room") if isinstance(payload.get("room"), dict) else {}

    if synthesis and synthesis.get("recommendation"):
        summary_text = str(synthesis["recommendation"])
    else:
        summary_text = question

    capture_payload: dict[str, Any] = {
        "available_experts": available,
        "unavailable_experts": unavailable,
        "degraded": available < 2 or unavailable > 0,
    }
    if room.get("id"):
        capture_payload["room_id"] = room["id"]
    if room.get("question_id"):
        capture_payload["question_id"] = room["question_id"]
    if room.get("status"):
        capture_payload["room_status"] = room["status"]
    if synthesis:
        for key in (
            "synthesis_mode",
            "consensus_available",
            "agreement_estimate",
            "available_experts",
            "unavailable_experts",
        ):
            if key in synthesis and key not in capture_payload:
                capture_payload[key] = synthesis[key]

    await append_event(
        org_id=org_id,
        thread_id=thread_id,
        event_type="response",
        summary=_truncate_summary(summary_text),
        source="council",
        provider="synthesis",
        model="synthesis",
        payload=capture_payload,
    )
