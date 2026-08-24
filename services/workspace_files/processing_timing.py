"""Passive document-processing lifecycle timing (observability only).

Does not change claim, retry, extraction, or upload behavior. Emits one
structured record when a job reaches a terminal state (succeeded/failed).

Existing timestamps reused (no new schema):

* workspace_files.created_at      → uploaded_at (stable; not rewritten)
* document_processing_jobs.created_at → job_created_at (stable across retries)
* in-memory claim wall clock      → claimed_at (DB claimed_at is cleared on
  complete_job / requeue, so it cannot be read back after terminal state)
* executor start/end wall clock   → processing_started_at / processing_finished_at
  (started is the executor start; claimed_at is the SQL claim instant)
* workspace_files.updated_at      → ready_at / failed_at after pipeline persist
  (file row is updated inside extraction, before complete_job)

complete_job NULLs claimed_at; do not treat post-terminal claimed_at as truth.
available_at is scheduling/backoff, never processing duration.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from database.connection import get_db_session
from services.ops.structured_log import log_info, log_warning

_SUBSYSTEM = "doc_processing"
TIMING_EVENT = "document_processing_timing"

# Explicit allowlist for the JSON log line. Never filenames, bytes, or text.
TIMING_LOG_KEYS = (
    "event",
    "file_id",
    "job_id",
    "org_id",
    "workspace_id",
    "job_status",
    "file_status",
    "attempts",
    "uploaded_at",
    "job_created_at",
    "claimed_at",
    "processing_started_at",
    "processing_finished_at",
    "ready_at",
    "upload_to_job_ms",
    "job_to_claim_ms",
    "claim_to_finish_ms",
    "processing_ms",
    "finish_to_ready_ms",
    "upload_to_ready_ms",
)

_FORBIDDEN_LOG_KEYS = frozenset(
    {
        "extracted_text",
        "content",
        "prompt",
        "api_key",
        "authorization",
        "token",
        "secret",
        "password",
        "database_url",
        "filename",
        "display_name",
        "original_filename",
        "text",
        "bytes",
        "storage_key",
    }
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def stamp_claimed_jobs(claimed: list[dict[str, Any]], *, claimed_at: datetime | None = None) -> datetime:
    """Attach the SQL-claim instant to each in-memory job dict. Does not write DB."""
    stamp = claimed_at or utcnow()
    for job in claimed:
        job["_timing_claimed_at"] = stamp
    return stamp


def _as_utc(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
        return _as_utc(parsed)
    return None


def _iso(value: datetime | None) -> str | None:
    dt = _as_utc(value)
    if dt is None:
        return None
    return dt.isoformat()


def duration_ms(start: datetime | None, end: datetime | None) -> int | None:
    a = _as_utc(start)
    b = _as_utc(end)
    if a is None or b is None:
        return None
    return max(0, int(round((b - a).total_seconds() * 1000.0)))


def build_timing_payload(
    *,
    file_id: str,
    job_id: str,
    org_id: str,
    workspace_id: str,
    job_status: str,
    attempts: int,
    uploaded_at: datetime | None,
    job_created_at: datetime | None,
    claimed_at: datetime | None,
    processing_started_at: datetime | None,
    processing_finished_at: datetime | None,
    ready_at: datetime | None,
    file_status: str | None = None,
    job_completed_at: datetime | None = None,
) -> dict[str, Any]:
    """Pure duration math. Original upload/job timestamps are inputs, never overwritten."""
    start_for_process = processing_started_at or claimed_at
    payload: dict[str, Any] = {
        "event": TIMING_EVENT,
        "file_id": str(file_id),
        "job_id": str(job_id),
        "org_id": str(org_id),
        "workspace_id": str(workspace_id),
        "job_status": str(job_status),
        "attempts": int(attempts),
        "uploaded_at": _iso(uploaded_at),
        "job_created_at": _iso(job_created_at),
        "claimed_at": _iso(claimed_at),
        "processing_started_at": _iso(start_for_process),
        "processing_finished_at": _iso(processing_finished_at),
        "ready_at": _iso(ready_at),
        "upload_to_job_ms": duration_ms(uploaded_at, job_created_at),
        "job_to_claim_ms": duration_ms(job_created_at, claimed_at),
        "claim_to_finish_ms": duration_ms(claimed_at, processing_finished_at),
        "processing_ms": duration_ms(start_for_process, processing_finished_at),
        "finish_to_ready_ms": duration_ms(processing_finished_at, job_completed_at or ready_at),
        "upload_to_ready_ms": duration_ms(uploaded_at, ready_at or job_completed_at),
    }
    if file_status:
        payload["file_status"] = str(file_status)
    leaked = _FORBIDDEN_LOG_KEYS.intersection(payload)
    if leaked:
        raise RuntimeError(f"timing payload contained forbidden keys: {sorted(leaked)}")
    return payload


async def _load_timing_anchors(
    *,
    org_id: uuid.UUID,
    job_id: uuid.UUID,
    file_id: uuid.UUID,
) -> dict[str, Any]:
    """Read surviving timestamps. RLS via org_id from the already-claimed job."""
    async with get_db_session() as session:
        await session.execute(
            text("SELECT set_config('app.current_org_id', :v, true)"),
            {"v": str(org_id)},
        )
        row = (
            await session.execute(
                text(
                    """
                    SELECT f.created_at AS uploaded_at,
                           f.updated_at AS file_updated_at,
                           f.status AS file_status,
                           j.created_at AS job_created_at,
                           j.attempts AS attempts,
                           j.status AS job_status
                      FROM ben.document_processing_jobs j
                      JOIN ben.workspace_files f ON f.id = j.file_id
                     WHERE j.id = :jid AND j.file_id = :fid
                       AND j.org_id = :oid
                     LIMIT 1
                    """
                ),
                {"jid": str(job_id), "fid": str(file_id), "oid": str(org_id)},
            )
        ).mappings().first()
    return dict(row) if row else {}


def emit_timing_record(payload: dict[str, Any]) -> None:
    fields = {k: payload.get(k) for k in TIMING_LOG_KEYS if k in payload and payload.get(k) is not None}
    log_info(
        "document processing timing",
        subsystem=_SUBSYSTEM,
        operation="document_processing_timing",
        outcome=str(payload.get("job_status") or "ok"),
        **fields,
    )


async def emit_terminal_timing(
    *,
    org_id: uuid.UUID,
    workspace_id: uuid.UUID,
    file_id: uuid.UUID,
    job_id: uuid.UUID,
    job_status: str,
    attempts: int,
    claimed_at: datetime | None,
    processing_started_at: datetime | None,
    processing_finished_at: datetime | None,
    job_completed_at: datetime | None,
) -> dict[str, Any] | None:
    """Load surviving DB timestamps and emit one terminal timing record. Never raises."""
    if job_status not in {"succeeded", "failed", "cancelled"}:
        return None
    try:
        anchors = await _load_timing_anchors(org_id=org_id, job_id=job_id, file_id=file_id)
        payload = build_timing_payload(
            file_id=str(file_id),
            job_id=str(job_id),
            org_id=str(org_id),
            workspace_id=str(workspace_id),
            job_status=str(anchors.get("job_status") or job_status),
            attempts=int(anchors.get("attempts") if anchors.get("attempts") is not None else attempts),
            uploaded_at=_as_utc(anchors.get("uploaded_at")),
            job_created_at=_as_utc(anchors.get("job_created_at")),
            claimed_at=_as_utc(claimed_at),
            processing_started_at=_as_utc(processing_started_at),
            processing_finished_at=_as_utc(processing_finished_at),
            ready_at=_as_utc(anchors.get("file_updated_at")),
            file_status=str(anchors.get("file_status") or "") or None,
            job_completed_at=_as_utc(job_completed_at),
        )
        emit_timing_record(payload)
        return payload
    except Exception as exc:  # noqa: BLE001
        log_warning(
            "document processing timing emit failed",
            subsystem=_SUBSYSTEM,
            operation="document_processing_timing",
            outcome="error",
            file_id=str(file_id),
            job_id=str(job_id),
            error_class=type(exc).__name__,
        )
        return None
