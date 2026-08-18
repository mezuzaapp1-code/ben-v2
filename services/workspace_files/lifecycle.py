"""Truthful workspace-file processing stages for the API/UI contract.

Does not invent numeric processing percentages. READY is only returned when
the legacy upload ``status`` is already ``ready``. Extracting/Indexing are
shown only when the durable job is actually ``running``.
"""
from __future__ import annotations

from typing import Any, Iterable

STAGE_QUEUED = "queued"
STAGE_EXTRACTING = "extracting"
STAGE_INDEXING = "indexing"
STAGE_READY = "ready"
STAGE_FAILED = "failed"

JOB_QUEUED = "queued"
JOB_RUNNING = "running"
JOB_FAILED = "failed"
JOB_SUCCEEDED = "succeeded"
JOB_CANCELLED = "cancelled"

_FAILED = frozenset({"failed"})
_READY = "ready"
_INDEXING = "indexing"
_COMPLETE = frozenset({"complete", "partial"})

_JOB_RANK = {
    JOB_RUNNING: 0,
    JOB_QUEUED: 1,
    JOB_FAILED: 2,
    JOB_SUCCEEDED: 3,
    JOB_CANCELLED: 4,
}


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


def _job_sort_key(job: Any) -> tuple[int, float]:
    status = _norm(getattr(job, "status", None))
    rank = _JOB_RANK.get(status, 9)
    ts = getattr(job, "updated_at", None) or getattr(job, "created_at", None)
    stamp = float(ts.timestamp()) if ts is not None and hasattr(ts, "timestamp") else 0.0
    return (rank, -stamp)


def pick_relevant_job_status(jobs: Iterable[Any] | None) -> str | None:
    """Choose the latest relevant job status. Caller must already scope rows."""
    rows = [j for j in (jobs or []) if j is not None]
    if not rows:
        return None
    best = min(rows, key=_job_sort_key)
    status = _norm(getattr(best, "status", None))
    return status or None


def job_status_by_file_id(jobs: Iterable[Any] | None) -> dict[Any, str]:
    """Group pre-scoped jobs by file_id and pick one status per file."""
    grouped: dict[Any, list[Any]] = {}
    for job in jobs or []:
        if job is None:
            continue
        fid = getattr(job, "file_id", None)
        if fid is None:
            continue
        grouped.setdefault(fid, []).append(job)
    out: dict[Any, str] = {}
    for fid, rows in grouped.items():
        status = pick_relevant_job_status(rows)
        if status:
            out[fid] = status
    return out


def derive_running_stage(*, extraction_status: Any = None, index_status: Any = None) -> str:
    """Extracting vs Indexing while the durable job is running."""
    extraction = _norm(extraction_status) or "pending"
    index = _norm(index_status) or "not_indexed"
    extraction_done = extraction in _COMPLETE
    if index == _INDEXING or (extraction_done and index != "indexed"):
        return STAGE_INDEXING
    return STAGE_EXTRACTING


def derive_processing_stage_from_fields(
    *,
    status: Any = None,
    extraction_status: Any = None,
    index_status: Any = None,
    job_status: Any = None,
) -> str:
    """Map file + durable job fields to a single UI stage.

    READY is fail-closed on file.status. Extracting/Indexing require job running.
    """
    st = _norm(status)
    extraction = _norm(extraction_status) or "pending"
    index = _norm(index_status) or "not_indexed"
    job = _norm(job_status)

    if st == _READY:
        return STAGE_READY

    if job == JOB_RUNNING:
        return derive_running_stage(extraction_status=extraction, index_status=index)

    if job == JOB_QUEUED:
        return STAGE_QUEUED

    if job == JOB_FAILED:
        return STAGE_FAILED

    # succeeded / cancelled / none: do not keep stale in-progress file flags.
    if st in _FAILED or extraction in _FAILED or index in _FAILED:
        return STAGE_FAILED
    return STAGE_QUEUED


def derive_processing_stage(row: Any, job_status: Any = None) -> str:
    return derive_processing_stage_from_fields(
        status=getattr(row, "status", None),
        extraction_status=getattr(row, "extraction_status", None),
        index_status=getattr(row, "index_status", None),
        job_status=job_status if job_status is not None else getattr(row, "job_status", None),
    )


def page_progress_from_fields(
    *,
    page_count: Any = None,
    pages_extracted: Any = None,
) -> tuple[int, int] | None:
    """Return ``(x, y)`` only when both sides are real, finite, and usable."""
    try:
        y = int(page_count)
        x = int(pages_extracted)
    except (TypeError, ValueError):
        return None
    if y <= 0 or x < 0:
        return None
    return x, y
