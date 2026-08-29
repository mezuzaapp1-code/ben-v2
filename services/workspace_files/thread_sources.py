"""Postgres-owned conversation source state (pending → active → recent).

SQLite thread_store is never read or written here. Retrieval and upload
must go through these helpers.
"""
from __future__ import annotations

import uuid
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import select, text
from sqlalchemy.orm.attributes import flag_modified

from database.connection import get_db_session
from database.models import Thread, WorkspaceFile
from services.ops.structured_log import log_warning
from services.providers.vision_input import is_vision_media_type
from services.workspace_files.source_policy import (
    ACTIVE_SOURCE_IDLE_TTL_MINUTES,
    ACTIVE_SOURCE_MAX_UNUSED_TURNS,
    UPLOAD_BURST_WINDOW_SECONDS,
)

_VISION_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def empty_source_state() -> dict[str, Any]:
    return {
        "conversation_file_ids": [],
        "pending_file_ids": [],
        "active_file_ids": [],
        "recent_file_ids": [],
        "generation": 0,
        "active_opened_at": None,
        "last_used_at": None,
        "pending_opened_at": None,
        "unused_turns": 0,
    }


def normalize_source_state(raw: Any) -> dict[str, Any]:
    base = empty_source_state()
    if not isinstance(raw, dict):
        return base
    out = deepcopy(base)
    for key in ("conversation_file_ids", "pending_file_ids", "active_file_ids", "recent_file_ids"):
        out[key] = _uniq_ids(raw.get(key))
    try:
        out["generation"] = int(raw.get("generation") or 0)
    except (TypeError, ValueError):
        out["generation"] = 0
    try:
        out["unused_turns"] = max(0, int(raw.get("unused_turns") or 0))
    except (TypeError, ValueError):
        out["unused_turns"] = 0
    out["active_opened_at"] = _iso_or_none(raw.get("active_opened_at"))
    out["last_used_at"] = _iso_or_none(raw.get("last_used_at"))
    out["pending_opened_at"] = _iso_or_none(raw.get("pending_opened_at"))
    return out


def restriction_file_ids(state: dict[str, Any]) -> list[str]:
    """Ids that restrict retrieval: pending ∪ active. Recent does not restrict."""
    s = normalize_source_state(state)
    return _uniq_ids([*s["pending_file_ids"], *s["active_file_ids"]])


def is_vision_upload(*, media_type: str | None, filename: str | None) -> bool:
    if is_vision_media_type(media_type):
        return True
    name = str(filename or "").strip().lower()
    return any(name.endswith(ext) for ext in _VISION_EXT)


def parse_thread_uuid(raw: str | None) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(raw or "").strip())
    except (TypeError, ValueError, AttributeError):
        return None


def apply_chat_upload(state: dict[str, Any], file_id: str, *, now: datetime | None = None) -> dict[str, Any]:
    """Record a chat-originated non-image upload. Burst append vs supersede."""
    s = normalize_source_state(state)
    fid = str(file_id).strip()
    if not fid:
        return s
    now = now or utcnow()
    s["conversation_file_ids"] = _uniq_ids([*s["conversation_file_ids"], fid])
    burst = _in_burst_window(s.get("pending_opened_at"), now)
    if burst and s["pending_file_ids"]:
        s["pending_file_ids"] = _uniq_ids([*s["pending_file_ids"], fid])
        return s
    # New burst: current active becomes recent; pending is replaced.
    if s["active_file_ids"]:
        s["recent_file_ids"] = _uniq_ids([*s["recent_file_ids"], *s["active_file_ids"]])
        s["active_file_ids"] = []
        s["active_opened_at"] = None
        s["last_used_at"] = None
        s["unused_turns"] = 0
    if s["pending_file_ids"]:
        s["recent_file_ids"] = _uniq_ids([*s["recent_file_ids"], *s["pending_file_ids"]])
    s["pending_file_ids"] = [fid]
    s["pending_opened_at"] = _iso(now)
    s["generation"] = int(s.get("generation") or 0) + 1
    return s


def apply_file_ready(state: dict[str, Any], file_id: str, *, now: datetime | None = None) -> dict[str, Any]:
    """Promote a pending id to active. No-op if the file is not a conversation source."""
    s = normalize_source_state(state)
    fid = str(file_id).strip()
    if not fid or fid not in s["pending_file_ids"]:
        return s
    now = now or utcnow()
    s["pending_file_ids"] = [x for x in s["pending_file_ids"] if x != fid]
    if not s["active_file_ids"]:
        s["active_opened_at"] = _iso(now)
        s["unused_turns"] = 0
        s["last_used_at"] = None
    if fid not in s["active_file_ids"]:
        s["active_file_ids"] = [*s["active_file_ids"], fid]
    if not s["pending_file_ids"]:
        s["pending_opened_at"] = None
    return s


def apply_file_failed(state: dict[str, Any], file_id: str) -> dict[str, Any]:
    s = normalize_source_state(state)
    fid = str(file_id).strip()
    if not fid:
        return s
    s["pending_file_ids"] = [x for x in s["pending_file_ids"] if x != fid]
    s["active_file_ids"] = [x for x in s["active_file_ids"] if x != fid]
    if not s["pending_file_ids"]:
        s["pending_opened_at"] = None
    if not s["active_file_ids"]:
        s["active_opened_at"] = None
        s["last_used_at"] = None
        s["unused_turns"] = 0
    return s


def expire_active(state: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    """Move active → recent on idle TTL. Does not use unused_turns."""
    s = normalize_source_state(state)
    if not s["active_file_ids"]:
        return s
    now = now or utcnow()
    anchor = _parse_dt(s.get("last_used_at")) or _parse_dt(s.get("active_opened_at"))
    if anchor is None:
        return s
    idle = timedelta(minutes=ACTIVE_SOURCE_IDLE_TTL_MINUTES)
    if now - anchor < idle:
        return s
    return _active_to_recent(s)


def apply_chat_turn(
    state: dict[str, Any],
    used_file_ids: list[str] | tuple[str, ...] | None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """After a standard-chat retrieval turn. Explicit-name override does not clear active."""
    now = now or utcnow()
    s = expire_active(state, now=now)
    if not s["active_file_ids"]:
        return s
    used = set(_uniq_ids(used_file_ids))
    active = set(s["active_file_ids"])
    if used & active:
        s["unused_turns"] = 0
        s["last_used_at"] = _iso(now)
        return s
    s["unused_turns"] = int(s.get("unused_turns") or 0) + 1
    if s["unused_turns"] >= ACTIVE_SOURCE_MAX_UNUSED_TURNS:
        return _active_to_recent(s)
    return s


def _active_to_recent(state: dict[str, Any]) -> dict[str, Any]:
    s = normalize_source_state(state)
    if s["active_file_ids"]:
        s["recent_file_ids"] = _uniq_ids([*s["recent_file_ids"], *s["active_file_ids"]])
    s["active_file_ids"] = []
    s["active_opened_at"] = None
    s["last_used_at"] = None
    s["unused_turns"] = 0
    return s


def _uniq_ids(raw: Any) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    if not isinstance(raw, (list, tuple)):
        return out
    for item in raw:
        fid = str(item or "").strip()
        if not fid or fid in seen:
            continue
        seen.add(fid)
        out.append(fid)
    return out


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _iso_or_none(raw: Any) -> str | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, datetime):
        return _iso(raw)
    text_v = str(raw).strip()
    return text_v or None


def _parse_dt(raw: Any) -> datetime | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, datetime):
        dt = raw
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    try:
        text_v = str(raw).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(text_v)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        return None


def _in_burst_window(pending_opened_at: Any, now: datetime) -> bool:
    opened = _parse_dt(pending_opened_at)
    if opened is None:
        return False
    return (now - opened) <= timedelta(seconds=UPLOAD_BURST_WINDOW_SECONDS)


async def _set_org(session, org_id: uuid.UUID) -> None:
    await session.execute(
        text("SELECT set_config('app.current_org_id', :oid, true)"), {"oid": str(org_id)}
    )


async def mutate_source_state(
    org_id: uuid.UUID,
    thread_id: uuid.UUID,
    mutator: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any] | None:
    """SELECT FOR UPDATE on the Postgres Thread row. None if thread missing."""
    async with get_db_session() as session:
        await _set_org(session, org_id)
        stmt = (
            select(Thread)
            .where(Thread.id == thread_id, Thread.org_id == org_id)
            .with_for_update()
        )
        row = (await session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        current = normalize_source_state(getattr(row, "source_state", None))
        nxt = normalize_source_state(mutator(current))
        row.source_state = nxt
        flag_modified(row, "source_state")
        await session.commit()
        return nxt


async def load_source_state(org_id: uuid.UUID, thread_id: uuid.UUID) -> dict[str, Any]:
    async with get_db_session() as session:
        await _set_org(session, org_id)
        row = await session.get(Thread, thread_id)
        if row is None or row.org_id != org_id:
            return empty_source_state()
        return normalize_source_state(getattr(row, "source_state", None))


async def record_chat_upload(
    *,
    org_id: uuid.UUID,
    source_chat_id: str | None,
    file_id: uuid.UUID,
    media_type: str | None,
    filename: str | None,
) -> dict[str, Any] | None:
    if is_vision_upload(media_type=media_type, filename=filename):
        return None
    tid = parse_thread_uuid(source_chat_id)
    if tid is None:
        return None
    fid = str(file_id)

    def _mut(state: dict[str, Any]) -> dict[str, Any]:
        return apply_chat_upload(state, fid)

    return await mutate_source_state(org_id, tid, _mut)


async def on_file_ready(*, org_id: uuid.UUID, file_id: uuid.UUID) -> dict[str, Any] | None:
    tid = await _source_thread_for_file(org_id, file_id)
    if tid is None:
        return None
    fid = str(file_id)

    def _mut(state: dict[str, Any]) -> dict[str, Any]:
        return apply_file_ready(state, fid)

    return await mutate_source_state(org_id, tid, _mut)


async def on_file_failed(*, org_id: uuid.UUID, file_id: uuid.UUID) -> dict[str, Any] | None:
    tid = await _source_thread_for_file(org_id, file_id)
    if tid is None:
        return None
    fid = str(file_id)

    def _mut(state: dict[str, Any]) -> dict[str, Any]:
        return apply_file_failed(state, fid)

    return await mutate_source_state(org_id, tid, _mut)


async def record_standard_chat_turn(
    *,
    org_id: uuid.UUID,
    thread_id: uuid.UUID,
    used_file_ids: list[str],
) -> dict[str, Any] | None:
    def _mut(state: dict[str, Any]) -> dict[str, Any]:
        return apply_chat_turn(state, used_file_ids)

    return await mutate_source_state(org_id, thread_id, _mut)


async def restriction_ids_for_thread(org_id: uuid.UUID, thread_id: uuid.UUID) -> list[str]:
    state = expire_active(await load_source_state(org_id, thread_id))
    return restriction_file_ids(state)


async def _source_thread_for_file(org_id: uuid.UUID, file_id: uuid.UUID) -> uuid.UUID | None:
    async with get_db_session() as session:
        await _set_org(session, org_id)
        row = await session.get(WorkspaceFile, file_id)
        if row is None or row.org_id != org_id:
            return None
        return parse_thread_uuid(row.source_chat_id)


def log_source_state_error(exc: BaseException, *, operation: str, file_id: str = "") -> None:
    log_warning(
        "conversation source state update failed",
        subsystem="workspace_files",
        operation=operation,
        outcome="error",
        file_id=file_id,
        error_class=type(exc).__name__,
    )
