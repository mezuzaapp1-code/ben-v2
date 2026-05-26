"""Read-only continuity reconstruction from BEN Log events (rule-based v1)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select, text

from database.connection import get_db_session
from database.models import BenLogEvent
from services.ops.request_context import attach_request_id
from services.ops.structured_log import log_warning
from services.thread_service import get_thread_for_org

MAX_EVENTS_CONSIDERED = 100
MAX_ITEMS_PER_SECTION = 10

_PROVIDER_BUCKETS = ("openai", "anthropic", "google", "synthesis")
_LOW_SIGNAL_TYPES = frozenset({"prompt", "response", "context", "note"})


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _cap(items: list[str], limit: int = MAX_ITEMS_PER_SECTION) -> list[str]:
    return items[:limit]


def _unique_append(target: list[str], value: str) -> None:
    text_val = (value or "").strip()
    if not text_val or text_val in target:
        return
    target.append(text_val)


def _payload_dict(payload: Any) -> dict[str, Any]:
    return payload if isinstance(payload, dict) else {}


def _event_summary(event: BenLogEvent) -> str:
    summary = (event.summary or "").strip()
    if summary:
        return summary
    payload = _payload_dict(event.payload)
    for key in ("summary", "next_step", "message", "text"):
        raw = payload.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return ""


def _provider_bucket(
    *,
    provider: str | None,
    model: str | None,
    payload: dict[str, Any],
    event_type: str,
) -> str | None:
    if event_type != "response":
        return None
    pid = str(payload.get("provider_id") or provider or "").lower()
    mod = str(model or "").lower()
    if pid == "synthesis" or mod == "synthesis" or provider == "synthesis":
        return "synthesis"
    if pid in ("gpt", "openai") or "gpt" in mod or provider == "openai":
        return "openai"
    if pid in ("claude", "anthropic") or "claude" in mod or provider == "anthropic":
        return "anthropic"
    if pid in ("gemini", "google") or "gemini" in mod or provider == "google":
        return "google"
    return None


def _extract_payload_lists(payload: dict[str, Any], *, rejected_paths: list[str], next_steps: list[str]) -> None:
    raw_rejected = payload.get("rejected_paths")
    if isinstance(raw_rejected, list):
        for item in raw_rejected:
            if isinstance(item, str):
                _unique_append(rejected_paths, item)
    raw_next = payload.get("next_step")
    if isinstance(raw_next, str):
        _unique_append(next_steps, raw_next)


def _compute_confidence(
    *,
    event_count: int,
    decisions: list[str],
    unresolved_items: list[str],
    rejected_paths: list[str],
    next_steps: list[str],
    event_types: set[str],
) -> str:
    if event_count == 0:
        return "none"
    if (decisions and next_steps) or (rejected_paths and next_steps):
        return "high"
    if unresolved_items or next_steps:
        return "medium"
    if event_types <= _LOW_SIGNAL_TYPES:
        return "low"
    return "low"


def _current_direction(events: list[BenLogEvent]) -> str:
    latest_next: str | None = None
    latest_decision: str | None = None
    latest_unresolved: str | None = None
    latest_response: str | None = None

    for event in events:
        summary = _event_summary(event)
        if not summary:
            continue
        if event.event_type == "next_step":
            latest_next = summary
        elif event.event_type == "decision":
            latest_decision = summary
        elif event.event_type == "unresolved":
            latest_unresolved = summary
        elif event.event_type == "response":
            latest_response = summary

    if latest_next:
        return latest_next
    if latest_decision:
        return latest_decision
    if latest_unresolved:
        return latest_unresolved
    if latest_response:
        return latest_response
    return ""


def _aggregate_events(events: list[BenLogEvent], *, thread_id: uuid.UUID) -> dict[str, Any]:
    decisions: list[str] = []
    unresolved_items: list[str] = []
    rejected_paths: list[str] = []
    next_steps: list[str] = []
    provider_activity = {k: 0 for k in _PROVIDER_BUCKETS}
    event_types: set[str] = set()

    for event in events:
        event_types.add(event.event_type)
        payload = _payload_dict(event.payload)
        _extract_payload_lists(payload, rejected_paths=rejected_paths, next_steps=next_steps)

        bucket = _provider_bucket(
            provider=event.provider,
            model=event.model,
            payload=payload,
            event_type=event.event_type,
        )
        if bucket:
            provider_activity[bucket] += 1

        summary = _event_summary(event)
        if event.event_type == "decision" and summary:
            _unique_append(decisions, summary)
        elif event.event_type == "rejection" and summary:
            _unique_append(rejected_paths, summary)
        elif event.event_type == "unresolved" and summary:
            _unique_append(unresolved_items, summary)
        elif event.event_type == "next_step" and summary:
            _unique_append(next_steps, summary)

    last_activity_at: str | None = _iso(events[-1].created_at) if events else None

    return {
        "thread_id": str(thread_id),
        "last_activity_at": last_activity_at,
        "current_direction": _current_direction(events),
        "decisions": _cap(decisions),
        "unresolved_items": _cap(unresolved_items),
        "rejected_paths": _cap(rejected_paths),
        "next_steps": _cap(next_steps),
        "provider_activity": provider_activity,
        "event_count": len(events),
        "continuity_confidence": _compute_confidence(
            event_count=len(events),
            decisions=decisions,
            unresolved_items=unresolved_items,
            rejected_paths=rejected_paths,
            next_steps=next_steps,
            event_types=event_types,
        ),
    }


async def _fetch_log_events(org_id: uuid.UUID, thread_id: uuid.UUID) -> list[BenLogEvent]:
    async with get_db_session() as session:
        await session.execute(text("SELECT set_config('app.current_org_id', :v, true)"), {"v": str(org_id)})
        stmt = (
            select(BenLogEvent)
            .where(BenLogEvent.org_id == org_id, BenLogEvent.thread_id == thread_id)
            .order_by(BenLogEvent.created_at.desc())
            .limit(MAX_EVENTS_CONSIDERED)
        )
        rows = list((await session.scalars(stmt)).all())
    rows.reverse()
    return rows


async def build_thread_continuity(org_id: uuid.UUID, thread_id: uuid.UUID) -> dict[str, Any]:
    """Reconstruct continuity for a thread. Read-only; raises 404/503 only."""
    thread = await get_thread_for_org(org_id, thread_id)
    if thread is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Thread not found")

    try:
        events = await _fetch_log_events(org_id, thread_id)
    except Exception as exc:
        log_warning(
            "continuity read failed",
            subsystem="continuity",
            operation="fetch_log_events",
            outcome="error",
            category="persistence_error",
            exc=exc,
        )
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"detail": "Continuity temporarily unavailable", "code": "continuity_read_failed"},
        ) from exc

    body = _aggregate_events(events, thread_id=thread_id)
    return attach_request_id(body)
