"""Gate 3A durable orchestration substrate — service wrappers.

Enqueue + claim + reap + requeue + complete for ben.document_processing_jobs.
This module owns ONLY scheduling/ownership/attempts/lease/outcome. It performs
NO document parsing/chunking and imports NO extraction pipeline. It is NOT wired
into upload_file; it is callable from tests/internal code only in Gate 3A.

Cross-org claim/reaper run through SECURITY DEFINER functions owned by the
`ben_doc_processor` role (migration 024); product sessions stay FORCE-RLS isolated.
No document text/bytes, DATABASE_URL, or credentials are ever logged.
"""
from __future__ import annotations

import os
import uuid
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY, UUID as PG_UUID

from database.connection import get_db_session
from services.ops.structured_log import log_info, log_warning
from services.workspace_files.chunking import CHUNKING_VERSION
from services.workspace_files.document_parser import EXTRACTION_VERSION
from services.workspace_files.ingest_eligibility import (
    file_is_ingest_protected,
    new_job_is_runner_eligible,
)

JOB_TYPE_STRUCTURED_EXTRACTION = "structured_extraction"
# Gate 3B: async execution of the existing legacy extraction (process_file), which
# is what produces the READY state + extracted_text consumed by chat retrieval.
JOB_TYPE_FILE_EXTRACTION = "file_extraction"
# Post-READY grounded overview. Never executed by the extraction drain.
JOB_TYPE_FILE_INITIAL_READ = "file_initial_read"
EXTRACTION_JOB_TYPES = (JOB_TYPE_FILE_EXTRACTION, JOB_TYPE_STRUCTURED_EXTRACTION)
ACTIVE_STATUSES = ("queued", "running")
TERMINAL_STATUSES = ("succeeded", "failed", "cancelled")

DEFAULT_MAX_ATTEMPTS = int(os.getenv("BEN_DOC_JOB_MAX_ATTEMPTS", "5"))
DEFAULT_LEASE_SECONDS = int(os.getenv("BEN_DOC_JOB_LEASE_SECONDS", "300"))
RETRY_BASE_SECONDS = int(os.getenv("BEN_DOC_JOB_RETRY_BASE_SECONDS", "30"))
RETRY_CAP_SECONDS = int(os.getenv("BEN_DOC_JOB_RETRY_CAP_SECONDS", "3600"))

_SUBSYSTEM = "doc_processing"


def _pg_uuid_array(ids: list[uuid.UUID]) -> list[uuid.UUID]:
    """Native uuid[] bind value. Empty list is fail-closed at SQL (cardinality 0)."""
    return list(ids)


_UUID_ARRAY = ARRAY(PG_UUID(as_uuid=True))


class TenantOwnershipError(ValueError):
    """Raised when (org, workspace, file) do not refer to one WorkspaceFile."""


async def _set_org(session, org_id: uuid.UUID) -> None:
    await session.execute(
        text("SELECT set_config('app.current_org_id', :oid, true)"), {"oid": str(org_id)}
    )


def compute_retry_delay_seconds(
    attempts: int, *, base_seconds: int = RETRY_BASE_SECONDS, cap_seconds: int = RETRY_CAP_SECONDS
) -> int:
    """Deterministic bounded exponential backoff (no jitter; policy-configurable).

    Mirrors the SQL used by the reaper so app-side and DB-side timing agree.
    """
    return int(min(cap_seconds, base_seconds * (2 ** max(attempts - 1, 0))))


def _job_dict(row) -> dict[str, Any]:
    d = dict(row)
    for k, v in list(d.items()):
        if isinstance(v, uuid.UUID):
            d[k] = str(v)
    return d


async def _enqueue_in_session(
    session,
    org_id: uuid.UUID,
    workspace_id: uuid.UUID,
    file_id: uuid.UUID,
    *,
    extraction_version: int,
    chunking_version: int,
    job_type: str,
    max_attempts: int,
) -> dict[str, Any]:
    """Insert (or idempotently find) the active job in the caller's transaction.

    Does NOT commit — the caller owns the transaction boundary (this lets upload
    persist the WorkspaceFile and enqueue its job atomically).
    """
    await _set_org(session, org_id)
    owned = (
        await session.execute(
            text(
                "SELECT 1 FROM ben.workspace_files "
                "WHERE id = :f AND org_id = :o AND workspace_id = :w"
            ),
            {"f": str(file_id), "o": str(org_id), "w": str(workspace_id)},
        )
    ).first()
    if owned is None:
        raise TenantOwnershipError("file does not belong to org/workspace")

    params = {
        "o": str(org_id), "w": str(workspace_id), "f": str(file_id),
        "jt": job_type, "ev": extraction_version, "cv": chunking_version,
        "ma": max_attempts,
        "eligible": new_job_is_runner_eligible(file_id),
    }
    inserted = (
        await session.execute(
            text(
                """
                INSERT INTO ben.document_processing_jobs
                    (org_id, workspace_id, file_id, job_type, status,
                     extraction_version, chunking_version, max_attempts, available_at,
                     runner_eligible)
                VALUES (:o, :w, :f, :jt, 'queued', :ev, :cv, :ma, now(), :eligible)
                ON CONFLICT (file_id, job_type, extraction_version, chunking_version)
                    WHERE status IN ('queued','running')
                DO NOTHING
                RETURNING id, status, attempts, available_at, created_at, runner_eligible
                """
            ),
            params,
        )
    ).mappings().first()

    if inserted is not None:
        return {"created": True, **_job_dict(inserted)}

    existing = (
        await session.execute(
            text(
                """
                SELECT id, status, attempts, available_at, created_at, runner_eligible
                  FROM ben.document_processing_jobs
                 WHERE file_id = :f AND job_type = :jt
                   AND extraction_version = :ev AND chunking_version = :cv
                   AND status IN ('queued','running')
                 ORDER BY created_at, id
                 LIMIT 1
                """
            ),
            params,
        )
    ).mappings().first()
    return {"created": False, **_job_dict(existing)} if existing else {"created": False}


async def enqueue_document_processing_job(
    org_id: uuid.UUID,
    workspace_id: uuid.UUID,
    file_id: uuid.UUID,
    *,
    extraction_version: int = EXTRACTION_VERSION,
    chunking_version: int = CHUNKING_VERSION,
    job_type: str = JOB_TYPE_STRUCTURED_EXTRACTION,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    session=None,
) -> dict[str, Any]:
    """Idempotently enqueue one processing job for a file+version.

    Tenant-scoped: runs under the caller's org context. Ownership integrity is
    DB-enforced (composite FK + RLS WITH CHECK); a light pre-check gives a clean
    error. Duplicate enqueue while an active job exists returns the existing job
    (created=False) rather than erroring.

    If ``session`` is provided, the insert runs in the caller's transaction and is
    NOT committed here (the caller commits — enabling atomic upload+enqueue). If
    omitted, a dedicated session is opened and committed.
    """
    if session is not None:
        result = await _enqueue_in_session(
            session, org_id, workspace_id, file_id,
            extraction_version=extraction_version, chunking_version=chunking_version,
            job_type=job_type, max_attempts=max_attempts,
        )
    else:
        async with get_db_session() as own_session:
            result = await _enqueue_in_session(
                own_session, org_id, workspace_id, file_id,
                extraction_version=extraction_version, chunking_version=chunking_version,
                job_type=job_type, max_attempts=max_attempts,
            )
            await own_session.commit()

    log_info(
        "processing job enqueue",
        subsystem=_SUBSYSTEM, operation="enqueue", outcome="ok",
        job_id=result.get("id"), org_id=str(org_id), workspace_id=str(workspace_id),
        file_id=str(file_id), job_type=job_type, status=result.get("status"),
        job_created=result.get("created"),
    )
    return result


async def claim_jobs(
    worker_id: str, *, lease_seconds: int = DEFAULT_LEASE_SECONDS, limit: int = 1
) -> list[dict[str, Any]]:
    """Atomically claim up to `limit` due jobs (FOR UPDATE SKIP LOCKED), cross-org.

    Uses the SECURITY DEFINER system function; commit persists the lease
    independently of any future document processing.
    """
    async with get_db_session() as session:
        rows = (
            await session.execute(
                text("SELECT * FROM ben.claim_document_processing_jobs(:w, :l, :n)"),
                {"w": worker_id, "l": lease_seconds, "n": limit},
            )
        ).mappings().all()
        await session.commit()
    claimed = [_job_dict(r) for r in rows]
    for j in claimed:
        if file_is_ingest_protected(j.get("file_id")):
            raise RuntimeError("claim refused a historically quarantined file_id")
        log_info(
            "processing job claimed", subsystem=_SUBSYSTEM, operation="claim", outcome="ok",
            job_id=j.get("job_id"), org_id=j.get("org_id"), workspace_id=j.get("workspace_id"),
            file_id=j.get("file_id"), job_type=j.get("job_type"), attempt=j.get("attempts"),
            status="running", worker_id=worker_id,
        )
    return claimed


async def claim_job_for_file(
    worker_id: str, file_id: uuid.UUID, *, lease_seconds: int = DEFAULT_LEASE_SECONDS
) -> list[dict[str, Any]]:
    """Claim at most one due queued job for an exact file_id.

    Never falls back to the generic queue. Empty result is a no-op (no eligible
    queued job for that file). Same lease/attempt semantics as claim_jobs.
    Historically quarantined file IDs are never claimed.
    """
    if file_is_ingest_protected(file_id):
        return []
    async with get_db_session() as session:
        rows = (
            await session.execute(
                text("SELECT * FROM ben.claim_document_processing_job_for_file(:w, :l, :f)"),
                {"w": worker_id, "l": lease_seconds, "f": str(file_id)},
            )
        ).mappings().all()
        await session.commit()
    claimed = [_job_dict(r) for r in rows]
    for j in claimed:
        if str(j.get("file_id")) != str(file_id):
            raise RuntimeError("scoped claim returned a job for a different file_id")
        if file_is_ingest_protected(j.get("file_id")):
            raise RuntimeError("claim refused a historically quarantined file_id")
        log_info(
            "processing job claimed", subsystem=_SUBSYSTEM, operation="claim_for_file",
            outcome="ok", job_id=j.get("job_id"), org_id=j.get("org_id"),
            workspace_id=j.get("workspace_id"), file_id=j.get("file_id"),
            job_type=j.get("job_type"), attempt=j.get("attempts"),
            status="running", worker_id=worker_id,
        )
    return claimed


async def claim_file_initial_read_jobs(
    worker_id: str, *, lease_seconds: int = DEFAULT_LEASE_SECONDS, limit: int = 1
) -> list[dict[str, Any]]:
    """Claim due queued file_initial_read jobs only. Never claims extraction jobs."""
    async with get_db_session() as session:
        rows = (
            await session.execute(
                text("SELECT * FROM ben.claim_file_initial_read_jobs(:w, :l, :n)"),
                {"w": worker_id, "l": lease_seconds, "n": limit},
            )
        ).mappings().all()
        await session.commit()
    claimed = [_job_dict(r) for r in rows]
    for j in claimed:
        if str(j.get("job_type") or "") != JOB_TYPE_FILE_INITIAL_READ:
            raise RuntimeError("initial-read claim returned a non-initial-read job")
        if file_is_ingest_protected(j.get("file_id")):
            raise RuntimeError("claim refused a historically quarantined file_id")
        log_info(
            "processing job claimed", subsystem=_SUBSYSTEM, operation="claim_initial_read",
            outcome="ok", job_id=j.get("job_id"), org_id=j.get("org_id"),
            workspace_id=j.get("workspace_id"), file_id=j.get("file_id"),
            job_type=j.get("job_type"), attempt=j.get("attempts"),
            status="running", worker_id=worker_id,
        )
    return claimed


async def claim_file_initial_read_job_for_file(
    worker_id: str, file_id: uuid.UUID, *, lease_seconds: int = DEFAULT_LEASE_SECONDS
) -> list[dict[str, Any]]:
    """Claim at most one due file_initial_read job for an exact file_id."""
    if file_is_ingest_protected(file_id):
        return []
    async with get_db_session() as session:
        rows = (
            await session.execute(
                text("SELECT * FROM ben.claim_file_initial_read_job_for_file(:w, :l, :f)"),
                {"w": worker_id, "l": lease_seconds, "f": str(file_id)},
            )
        ).mappings().all()
        await session.commit()
    claimed = [_job_dict(r) for r in rows]
    for j in claimed:
        if str(j.get("file_id")) != str(file_id):
            raise RuntimeError("scoped initial-read claim returned a different file_id")
        if str(j.get("job_type") or "") != JOB_TYPE_FILE_INITIAL_READ:
            raise RuntimeError("initial-read claim returned a non-initial-read job")
        if file_is_ingest_protected(j.get("file_id")):
            raise RuntimeError("claim refused a historically quarantined file_id")
        log_info(
            "processing job claimed", subsystem=_SUBSYSTEM, operation="claim_initial_read_for_file",
            outcome="ok", job_id=j.get("job_id"), org_id=j.get("org_id"),
            workspace_id=j.get("workspace_id"), file_id=j.get("file_id"),
            job_type=j.get("job_type"), attempt=j.get("attempts"),
            status="running", worker_id=worker_id,
        )
    return claimed


async def reap_expired_jobs(
    *, base_seconds: int = RETRY_BASE_SECONDS, cap_seconds: int = RETRY_CAP_SECONDS, limit: int = 100
) -> list[dict[str, Any]]:
    """Recover expired leases: requeue (with backoff) or fail at attempt limit.

    Safe under concurrent invocation (FOR UPDATE SKIP LOCKED inside the function).
    Does NOT execute extraction.
    """
    async with get_db_session() as session:
        rows = (
            await session.execute(
                text("SELECT * FROM ben.reap_expired_document_processing_jobs(:b, :c, :n)"),
                {"b": base_seconds, "c": cap_seconds, "n": limit},
            )
        ).mappings().all()
        await session.commit()
    reaped = [_job_dict(r) for r in rows]
    for j in reaped:
        log_warning(
            "processing job lease reaped", subsystem=_SUBSYSTEM, operation="reap", outcome="ok",
            job_id=j.get("job_id"), status=j.get("outcome"),
        )
    return reaped


async def reap_expired_file_initial_read_jobs(
    *, base_seconds: int = RETRY_BASE_SECONDS, cap_seconds: int = RETRY_CAP_SECONDS, limit: int = 100
) -> list[dict[str, Any]]:
    """Reap expired file_initial_read leases only. Does not require runner_eligible."""
    async with get_db_session() as session:
        rows = (
            await session.execute(
                text("SELECT * FROM ben.reap_expired_file_initial_read_jobs(:b, :c, :n)"),
                {"b": base_seconds, "c": cap_seconds, "n": limit},
            )
        ).mappings().all()
        await session.commit()
    reaped = [_job_dict(r) for r in rows]
    for j in reaped:
        log_warning(
            "processing job lease reaped", subsystem=_SUBSYSTEM, operation="reap_initial_read",
            outcome="ok", job_id=j.get("job_id"), status=j.get("outcome"),
        )
    return reaped


async def claim_jobs_for_allowlist(
    worker_id: str,
    *,
    file_ids: list[uuid.UUID],
    workspace_ids: list[uuid.UUID],
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    limit: int = 1,
) -> list[dict[str, Any]]:
    """Claim due queued jobs matching file_id or workspace_id allowlists.

    Empty allowlists return no rows (SQL fail-closed). Never falls back to FIFO.
    Historically quarantined file IDs are dropped from the file allowlist.
    """
    file_ids = [f for f in file_ids if not file_is_ingest_protected(f)]
    if not file_ids and not workspace_ids:
        return []
    async with get_db_session() as session:
        stmt = text(
            "SELECT * FROM ben.claim_document_processing_jobs_for_allowlist("
            ":w, :l, :n, :files, :workspaces)"
        ).bindparams(
            bindparam("files", type_=_UUID_ARRAY),
            bindparam("workspaces", type_=_UUID_ARRAY),
        )
        rows = (
            await session.execute(
                stmt,
                {
                    "w": worker_id,
                    "l": lease_seconds,
                    "n": limit,
                    "files": _pg_uuid_array(file_ids),
                    "workspaces": _pg_uuid_array(workspace_ids),
                },
            )
        ).mappings().all()
        await session.commit()
    claimed = [_job_dict(r) for r in rows]
    allowed_files = {str(x) for x in file_ids}
    allowed_ws = {str(x) for x in workspace_ids}
    for j in claimed:
        fid = str(j.get("file_id"))
        ws = str(j.get("workspace_id"))
        if allowed_files and fid in allowed_files:
            pass
        elif allowed_ws and ws in allowed_ws:
            pass
        else:
            raise RuntimeError("allowlist claim returned a job outside the allowlist")
        if file_is_ingest_protected(fid):
            raise RuntimeError("claim refused a historically quarantined file_id")
        log_info(
            "processing job claimed", subsystem=_SUBSYSTEM, operation="claim_allowlist",
            outcome="ok", job_id=j.get("job_id"), org_id=j.get("org_id"),
            workspace_id=j.get("workspace_id"), file_id=j.get("file_id"),
            job_type=j.get("job_type"), attempt=j.get("attempts"),
            status="running", worker_id=worker_id,
        )
    return claimed


async def reap_expired_jobs_for_allowlist(
    *,
    file_ids: list[uuid.UUID],
    workspace_ids: list[uuid.UUID],
    base_seconds: int = RETRY_BASE_SECONDS,
    cap_seconds: int = RETRY_CAP_SECONDS,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Reap expired leases only for allowlisted files/workspaces."""
    file_ids = [f for f in file_ids if not file_is_ingest_protected(f)]
    if not file_ids and not workspace_ids:
        return []
    async with get_db_session() as session:
        stmt = text(
            "SELECT * FROM ben.reap_expired_document_processing_jobs_for_allowlist("
            ":files, :workspaces, :b, :c, :n)"
        ).bindparams(
            bindparam("files", type_=_UUID_ARRAY),
            bindparam("workspaces", type_=_UUID_ARRAY),
        )
        rows = (
            await session.execute(
                stmt,
                {
                    "files": _pg_uuid_array(file_ids),
                    "workspaces": _pg_uuid_array(workspace_ids),
                    "b": base_seconds,
                    "c": cap_seconds,
                    "n": limit,
                },
            )
        ).mappings().all()
        await session.commit()
    reaped = [_job_dict(r) for r in rows]
    for j in reaped:
        log_warning(
            "processing job lease reaped", subsystem=_SUBSYSTEM, operation="reap_allowlist",
            outcome="ok", job_id=j.get("job_id"), status=j.get("outcome"),
        )
    return reaped


async def claim_jobs_for_eligible(
    worker_id: str,
    *,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    limit: int = 1,
) -> list[dict[str, Any]]:
    """Claim due queued jobs marked runner_eligible.

    Never falls back to generic FIFO. Protected historical file IDs cannot be
    claimed. Empty result is a no-op.
    """
    async with get_db_session() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT * FROM ben.claim_document_processing_jobs_for_eligible(:w, :l, :n)"
                ),
                {"w": worker_id, "l": lease_seconds, "n": limit},
            )
        ).mappings().all()
        await session.commit()
    claimed = [_job_dict(r) for r in rows]
    for j in claimed:
        if file_is_ingest_protected(j.get("file_id")):
            raise RuntimeError("eligible claim refused a historically quarantined file_id")
        log_info(
            "processing job claimed", subsystem=_SUBSYSTEM, operation="claim_eligible",
            outcome="ok", job_id=j.get("job_id"), org_id=j.get("org_id"),
            workspace_id=j.get("workspace_id"), file_id=j.get("file_id"),
            job_type=j.get("job_type"), attempt=j.get("attempts"),
            status="running", worker_id=worker_id,
        )
    return claimed


async def reap_expired_jobs_for_eligible(
    *,
    base_seconds: int = RETRY_BASE_SECONDS,
    cap_seconds: int = RETRY_CAP_SECONDS,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Reap expired leases only for runner_eligible jobs. Denylist is untouched."""
    async with get_db_session() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT * FROM ben.reap_expired_document_processing_jobs_for_eligible(:b, :c, :n)"
                ),
                {"b": base_seconds, "c": cap_seconds, "n": limit},
            )
        ).mappings().all()
        await session.commit()
    reaped = [_job_dict(r) for r in rows]
    for j in reaped:
        log_warning(
            "processing job lease reaped", subsystem=_SUBSYSTEM, operation="reap_eligible",
            outcome="ok", job_id=j.get("job_id"), status=j.get("outcome"),
        )
    return reaped


async def document_processing_job_stats() -> dict[str, Any]:
    """Cross-org operational gauges. No document text."""
    async with get_db_session() as session:
        row = (
            await session.execute(text("SELECT * FROM ben.document_processing_job_stats()"))
        ).mappings().first()
    if row is None:
        return {
            "due_queue_depth": 0,
            "oldest_due_queued_age_s": None,
            "running_count": 0,
            "failed_count": 0,
            "retry_count": 0,
            "succeeded_24h": 0,
        }
    d = dict(row)
    age = d.get("oldest_due_queued_age_s")
    if age is not None:
        d["oldest_due_queued_age_s"] = float(age)
    for k in ("due_queue_depth", "running_count", "failed_count", "retry_count", "succeeded_24h"):
        if d.get(k) is not None:
            d[k] = int(d[k])
    return d


async def reap_expired_jobs_for_file(
    file_id: uuid.UUID,
    *,
    base_seconds: int = RETRY_BASE_SECONDS,
    cap_seconds: int = RETRY_CAP_SECONDS,
) -> list[dict[str, Any]]:
    """Recover an expired lease for one file_id only. Does not touch other files."""
    if file_is_ingest_protected(file_id):
        return []
    async with get_db_session() as session:
        rows = (
            await session.execute(
                text("SELECT * FROM ben.reap_expired_document_processing_jobs_for_file(:f, :b, :c)"),
                {"f": str(file_id), "b": base_seconds, "c": cap_seconds},
            )
        ).mappings().all()
        await session.commit()
    reaped = [_job_dict(r) for r in rows]
    for j in reaped:
        log_warning(
            "processing job lease reaped", subsystem=_SUBSYSTEM, operation="reap_for_file",
            outcome="ok", job_id=j.get("job_id"), status=j.get("outcome"),
            file_id=str(file_id),
        )
    return reaped


async def requeue_job(
    job_id: uuid.UUID, *, delay_seconds: int, error_code: str | None = None,
    error_detail: str | None = None,
) -> dict[str, Any] | None:
    """Retry primitive: running -> queued with a future available_at (in Postgres)."""
    async with get_db_session() as session:
        row = (
            await session.execute(
                text(
                    "SELECT * FROM ben.requeue_document_processing_job(:id, :d, :ec, :ed)"
                ),
                {"id": str(job_id), "d": max(delay_seconds, 0), "ec": error_code, "ed": error_detail},
            )
        ).mappings().first()
        await session.commit()
    if row is None:
        return None
    j = _job_dict(row)
    log_info(
        "processing job requeued", subsystem=_SUBSYSTEM, operation="requeue", outcome="ok",
        job_id=j.get("job_id"), status=j.get("status"),
    )
    return j


async def complete_job(
    job_id: uuid.UUID, outcome: str, *, error_code: str | None = None,
    error_detail: str | None = None,
) -> dict[str, Any] | None:
    """Record terminal outcome: running -> succeeded|failed|cancelled.

    Records execution outcome only; does NOT invoke extraction.
    """
    if outcome not in TERMINAL_STATUSES:
        raise ValueError(f"invalid terminal outcome: {outcome}")
    async with get_db_session() as session:
        row = (
            await session.execute(
                text(
                    "SELECT * FROM ben.complete_document_processing_job(:id, :o, :ec, :ed)"
                ),
                {"id": str(job_id), "o": outcome, "ec": error_code, "ed": error_detail},
            )
        ).mappings().first()
        await session.commit()
    if row is None:
        return None
    j = _job_dict(row)
    log_info(
        "processing job completed", subsystem=_SUBSYSTEM, operation="complete",
        outcome="ok", job_id=j.get("job_id"), status=j.get("status"),
    )
    return j
