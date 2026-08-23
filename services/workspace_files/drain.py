"""Gate 3C — bounded, production-safe drain that executes durable
document_processing_jobs by running the Gate 2 STRUCTURED pipeline
(run_structured_extraction), which persists pages + chunks + truthful lifecycle
and (temporarily, until Gate 4) a legacy compatibility projection.

Not a persistent loop (BEN runs a single web service; externally/cron triggered
bounded batch). One invocation: recover expired leases (reaper) -> claim a bounded
batch (FOR UPDATE SKIP LOCKED, Gate 3A) -> run the structured pipeline per job ->
record terminal outcome or retry with Gate 3A backoff. Safe under concurrent
invocation (SKIP LOCKED) and after crash/restart (lease + reaper). No new queue
tech, no second orchestration layer, no arbitrary executor dispatch.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any

from services.ops.failure_classification import classify_failure
from services.ops.structured_log import log_info, log_warning
from services.workspace_files.ingest_eligibility import file_is_ingest_protected
from services.workspace_files.extraction_pipeline import run_structured_extraction
from services.workspace_files.job_queue import (
    DEFAULT_LEASE_SECONDS,
    DEFAULT_MAX_ATTEMPTS,
    JOB_TYPE_FILE_EXTRACTION,
    JOB_TYPE_STRUCTURED_EXTRACTION,
    claim_job_for_file,
    claim_jobs,
    claim_jobs_for_eligible,
    complete_job,
    compute_retry_delay_seconds,
    document_processing_job_stats,
    reap_expired_jobs,
    reap_expired_jobs_for_eligible,
    reap_expired_jobs_for_file,
    requeue_job,
)
from services.workspace_files.runner_config import (
    resolve_runner_claim_policy,
    runner_file_ids,
    runner_workspace_ids,
)

_SUBSYSTEM = "doc_processing"

DEFAULT_DRAIN_LIMIT = int(os.getenv("BEN_DOC_DRAIN_LIMIT", "5"))
PER_JOB_TIMEOUT_S = float(os.getenv("BEN_DOC_JOB_TIMEOUT_S", "120"))

# Determinate infra errors that must NOT retry (a retry cannot succeed).
_DETERMINISTIC_ERRORS = {"missing_bytes", "file_not_found"}

_DIAG_KEYS = (
    "parser_id", "parser_version", "source_page_count", "pages_extracted",
    "pages_empty", "pages_needs_ocr", "pages_failed", "pages_skipped", "chunk_count",
    "final_extraction_status", "final_index_status", "valid_source_without_text",
    "parse_ms", "chunk_ms", "persist_ms", "total_ms",
)


def processing_completed_without_text(diag: dict[str, Any] | None) -> bool:
    """True when the pipeline finished a valid source that has no usable text.

    Job outcome is success (processed, no text). Not a source/storage failure.
    """
    if not diag or diag.get("error") is not None:
        return False
    return bool(diag.get("valid_source_without_text"))


async def _execute_structured(org_id: uuid.UUID, workspace_id: uuid.UUID, file_id: uuid.UUID) -> dict[str, Any]:
    """Call the structured pipeline with EXPLICIT tenant context. Resolves the
    module-level symbol at call time (patchable in tests)."""
    return await run_structured_extraction(org_id, workspace_id, file_id)


# Bounded, explicit job_type -> executor map. Both current job types run the Gate 2
# structured pipeline. No dynamic/arbitrary function selection from job payload.
_EXECUTORS = {
    JOB_TYPE_FILE_EXTRACTION: _execute_structured,
    JOB_TYPE_STRUCTURED_EXTRACTION: _execute_structured,
}


def default_worker_id() -> str:
    return os.getenv("BEN_WORKER_ID") or f"web-{uuid.uuid4().hex[:8]}"


async def _retry_or_fail(
    job_id: uuid.UUID, attempts: int, code: str, detail: str, *, max_attempts: int
) -> str:
    if attempts >= max_attempts:
        await complete_job(job_id, "failed", error_code=code, error_detail=detail)
        return "failed"
    delay = compute_retry_delay_seconds(attempts)
    await requeue_job(job_id, delay_seconds=delay, error_code=code, error_detail=detail)
    return "requeued"


async def _run_claimed_jobs(
    claimed: list[dict[str, Any]],
    summary: dict[str, Any],
    *,
    worker_id: str,
    per_job_timeout_s: float,
    max_attempts: int,
) -> None:
    """Execute already-claimed jobs. Does not claim or reap."""
    for job in claimed:
        jid = uuid.UUID(job["job_id"])
        org = uuid.UUID(job["org_id"])
        ws = uuid.UUID(job["workspace_id"])
        fid = uuid.UUID(job["file_id"])
        if file_is_ingest_protected(fid):
            raise RuntimeError("drain refused a historically quarantined file_id")
        attempts = int(job.get("attempts") or 0)
        jtype = str(job.get("job_type") or "")

        executor = _EXECUTORS.get(jtype)
        if executor is None:
            await complete_job(jid, "failed", error_code="unknown_job_type", error_detail=jtype)
            summary["failed"] += 1
            log_warning("drain unknown job_type", subsystem=_SUBSYSTEM, operation="drain_job",
                        outcome="error", job_id=str(jid), job_type=jtype, worker_id=worker_id)
            continue

        diag: dict[str, Any] | None = None
        error_code: str | None = None
        try:
            diag = await asyncio.wait_for(executor(org, ws, fid), timeout=per_job_timeout_s)
        except asyncio.TimeoutError:
            error_code = "timeout"
            outcome = await _retry_or_fail(jid, attempts, error_code,
                                           f"per-job timeout {per_job_timeout_s}s", max_attempts=max_attempts)
            summary["requeued" if outcome == "requeued" else "failed"] += 1
        except Exception as e:  # noqa: BLE001  (transient infra around the pipeline)
            error_code = classify_failure(e)
            outcome = await _retry_or_fail(jid, attempts, error_code,
                                           type(e).__name__, max_attempts=max_attempts)
            summary["requeued" if outcome == "requeued" else "failed"] += 1
        else:
            err = diag.get("error")
            status = diag.get("final_extraction_status")
            if err is None and status in ("complete", "partial"):
                await complete_job(jid, "succeeded")
                summary["succeeded"] += 1
                outcome = "succeeded"
            elif err is None and processing_completed_without_text(diag):
                # Valid stored source, no usable text (image-only / needs_ocr-only).
                # Processing finished; this is not a source or job failure.
                await complete_job(jid, "succeeded")
                summary["succeeded"] += 1
                outcome = "succeeded_no_text"
            elif err is None and status == "failed":
                # Determinate broken source (empty / corrupt / unsupported).
                # Terminal — must NOT retry.
                error_code = "no_usable_text"
                await complete_job(jid, "failed", error_code=error_code,
                                   error_detail="extraction produced no usable text")
                summary["failed"] += 1
                outcome = "failed_determinate"
            elif err in _DETERMINISTIC_ERRORS:
                error_code = str(err)
                await complete_job(jid, "failed", error_code=error_code)
                summary["failed"] += 1
                outcome = "failed_determinate"
            else:
                # Transient infra error swallowed by the pipeline -> retry idempotently.
                error_code = str(err or "unknown")
                outcome = await _retry_or_fail(jid, attempts, error_code,
                                               "pipeline error", max_attempts=max_attempts)
                summary["requeued" if outcome == "requeued" else "failed"] += 1

        fields = {k: diag.get(k) for k in _DIAG_KEYS} if diag else {}
        log_info(
            "drain job processed",
            subsystem=_SUBSYSTEM,
            operation="drain_job",
            outcome=str(outcome),
            job_id=str(jid),
            org_id=str(org),
            workspace_id=str(ws),
            file_id=str(fid),
            attempts=attempts,
            job_type=jtype,
            result=outcome,
            worker_id=worker_id,
            error_code=error_code,
            **fields,
        )


def _scoped_outcome(summary: dict[str, Any]) -> str:
    if summary["succeeded"]:
        return "succeeded"
    if summary["requeued"]:
        return "requeued"
    if summary["failed"]:
        return "failed"
    if summary["reaped"]:
        return "reaped"
    return "no_eligible_job"


async def drain_document_processing_jobs(
    *,
    worker_id: str | None = None,
    limit: int = DEFAULT_DRAIN_LIMIT,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    per_job_timeout_s: float = PER_JOB_TIMEOUT_S,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    reap: bool = True,
) -> dict[str, Any]:
    """Run one bounded drain cycle. Returns a non-sensitive summary (no doc text)."""
    wid = worker_id or default_worker_id()
    summary: dict[str, Any] = {
        "worker_id": wid, "reaped": 0, "claimed": 0,
        "succeeded": 0, "failed": 0, "requeued": 0,
    }

    if reap:
        try:
            summary["reaped"] = len(await reap_expired_jobs())
        except Exception as e:  # noqa: BLE001
            log_warning("drain reaper failed", subsystem=_SUBSYSTEM, provider="database",
                        category=classify_failure(e), exc=e, operation="drain_reap", outcome="error")

    claimed = await claim_jobs(wid, lease_seconds=lease_seconds, limit=limit)
    summary["claimed"] = len(claimed)
    await _run_claimed_jobs(
        claimed, summary, worker_id=wid,
        per_job_timeout_s=per_job_timeout_s, max_attempts=max_attempts,
    )

    log_info("drain cycle complete", subsystem=_SUBSYSTEM, operation="drain", outcome="ok", **summary)
    return summary


async def drain_document_processing_job_for_file(
    file_id: uuid.UUID,
    *,
    worker_id: str | None = None,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    per_job_timeout_s: float = PER_JOB_TIMEOUT_S,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    reap: bool = True,
) -> dict[str, Any]:
    """Drain at most one job for an exact file_id. Never claims another file.

    Reaps an expired lease for this file only, then claims a due queued job for
    this file only. No generic-queue fallback. Missing/ineligible file_id is a
    safe no-op (claimed=0, outcome=no_eligible_job).
    """
    wid = worker_id or default_worker_id()
    fid = str(file_id)
    summary: dict[str, Any] = {
        "worker_id": wid, "file_id": fid, "job_id": None,
        "reaped": 0, "claimed": 0,
        "succeeded": 0, "failed": 0, "requeued": 0,
        "outcome": "no_eligible_job",
    }

    if file_is_ingest_protected(file_id):
        log_warning(
            "scoped drain refused historically quarantined file",
            subsystem=_SUBSYSTEM, operation="drain_for_file",
            outcome="no_eligible_job", file_id=fid, worker_id=wid,
        )
        return summary

    if reap:
        try:
            summary["reaped"] = len(await reap_expired_jobs_for_file(file_id))
        except Exception as e:  # noqa: BLE001
            log_warning("scoped drain reaper failed", subsystem=_SUBSYSTEM, provider="database",
                        category=classify_failure(e), exc=e, operation="drain_reap_for_file",
                        outcome="error", file_id=fid)

    claimed = await claim_job_for_file(wid, file_id, lease_seconds=lease_seconds)
    if any(str(job.get("file_id")) != fid for job in claimed):
        raise RuntimeError("scoped drain refused a claim for a different file_id")
    summary["claimed"] = len(claimed)
    if claimed:
        summary["job_id"] = claimed[0].get("job_id")
    await _run_claimed_jobs(
        claimed, summary, worker_id=wid,
        per_job_timeout_s=per_job_timeout_s, max_attempts=max_attempts,
    )
    summary["outcome"] = _scoped_outcome(summary)
    log_fields = {k: v for k, v in summary.items() if k != "outcome"}
    log_info("scoped drain cycle complete", subsystem=_SUBSYSTEM, operation="drain_for_file",
             outcome="ok", **log_fields)
    return summary


async def drain_document_processing_jobs_for_runner(
    *,
    worker_id: str | None = None,
    limit: int = DEFAULT_DRAIN_LIMIT,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    per_job_timeout_s: float = PER_JOB_TIMEOUT_S,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    reap: bool = True,
) -> dict[str, Any]:
    """Bounded runner drain. Claims persisted-eligible jobs only.

    Never uses generic FIFO. Never uses CLAIM_GLOBAL. Env allowlists are not
    the claim path. Historically quarantined file IDs cannot be claimed.
    """
    wid = worker_id or default_worker_id()
    policy = resolve_runner_claim_policy()
    files = runner_file_ids()
    workspaces = runner_workspace_ids()
    summary: dict[str, Any] = {
        "worker_id": wid,
        "claim_policy": policy,
        "reaped": 0,
        "claimed": 0,
        "succeeded": 0,
        "failed": 0,
        "requeued": 0,
        "file_ids": [str(x) for x in files],
        "workspace_ids": [str(x) for x in workspaces],
        "outcome": policy,
    }

    if policy != "eligible":
        log_info(
            "runner drain skipped",
            subsystem=_SUBSYSTEM,
            operation="runner_drain",
            outcome=policy,
            worker_id=wid,
            claim_policy=policy,
        )
        return summary

    if reap:
        try:
            summary["reaped"] = len(await reap_expired_jobs_for_eligible())
        except Exception as e:  # noqa: BLE001
            log_warning(
                "runner eligible reaper failed",
                subsystem=_SUBSYSTEM, provider="database",
                category=classify_failure(e), exc=e,
                operation="runner_reap", outcome="error",
            )
    claimed = await claim_jobs_for_eligible(
        wid, lease_seconds=lease_seconds, limit=limit,
    )
    if any(file_is_ingest_protected(job.get("file_id")) for job in claimed):
        raise RuntimeError("runner refused a historically quarantined file_id")

    summary["claimed"] = len(claimed)
    await _run_claimed_jobs(
        claimed, summary, worker_id=wid,
        per_job_timeout_s=per_job_timeout_s, max_attempts=max_attempts,
    )
    if summary["succeeded"]:
        summary["outcome"] = "succeeded"
    elif summary["requeued"]:
        summary["outcome"] = "requeued"
    elif summary["failed"]:
        summary["outcome"] = "failed"
    elif summary["reaped"]:
        summary["outcome"] = "reaped"
    elif summary["claimed"] == 0:
        summary["outcome"] = "no_eligible_job"
    log_info(
        "runner drain cycle complete",
        subsystem=_SUBSYSTEM,
        operation="runner_drain",
        outcome=str(summary["outcome"]),
        worker_id=wid,
        claim_policy=policy,
        reaped=summary["reaped"],
        claimed=summary["claimed"],
        succeeded=summary["succeeded"],
        failed=summary["failed"],
        requeued=summary["requeued"],
    )
    return summary


async def runner_processing_stats() -> dict[str, Any]:
    """DB-derived queue gauges. Does not claim or mutate jobs."""
    stats = await document_processing_job_stats()
    payload = {
        "claim_policy": resolve_runner_claim_policy(),
        "runner_enabled": resolve_runner_claim_policy() != "disabled",
        "file_ids": [str(x) for x in runner_file_ids()],
        "workspace_ids": [str(x) for x in runner_workspace_ids()],
        **stats,
    }
    log_info(
        "runner stats",
        subsystem=_SUBSYSTEM,
        operation="runner_stats",
        outcome="ok",
        due_queue_depth=payload.get("due_queue_depth"),
        running_count=payload.get("running_count"),
        failed_count=payload.get("failed_count"),
        retry_count=payload.get("retry_count"),
        succeeded_24h=payload.get("succeeded_24h"),
    )
    return payload
