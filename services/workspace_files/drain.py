"""Gate 3B — bounded, production-safe drain that executes durable
document_processing_jobs by reusing the EXISTING extraction (process_file).

This is deliberately NOT a persistent background loop (BEN runs a single web
service and follows the News pattern of an authenticated, externally-triggered
bounded batch). One invocation: recover expired leases (reaper) → claim a bounded
batch (FOR UPDATE SKIP LOCKED, Gate 3A) → run process_file per job → record
terminal outcome or retry with Gate 3A backoff. Safe under concurrent invocation
(SKIP LOCKED) and after crash/restart (lease expiry + reaper). No new queue tech,
no second orchestration layer.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any

from services.ops.failure_classification import classify_failure
from services.ops.structured_log import log_info, log_warning
from services.workspace_files.job_queue import (
    DEFAULT_LEASE_SECONDS,
    DEFAULT_MAX_ATTEMPTS,
    claim_jobs,
    complete_job,
    compute_retry_delay_seconds,
    reap_expired_jobs,
    requeue_job,
)
from services.workspace_files.service import process_file

_SUBSYSTEM = "doc_processing"

DEFAULT_DRAIN_LIMIT = int(os.getenv("BEN_DOC_DRAIN_LIMIT", "5"))
PER_JOB_TIMEOUT_S = float(os.getenv("BEN_DOC_JOB_TIMEOUT_S", "120"))


def default_worker_id() -> str:
    return os.getenv("BEN_WORKER_ID") or f"web-{uuid.uuid4().hex[:8]}"


async def _retry_or_fail(
    job_id: uuid.UUID, attempts: int, code: str, detail: str, *, max_attempts: int
) -> str:
    """Deterministic failure -> fail immediately handled by caller; this handles
    transient failures: retry with Gate 3A backoff until the attempt budget is
    exhausted, then mark failed. Never falsely marks the file READY."""
    if attempts >= max_attempts:
        await complete_job(job_id, "failed", error_code=code, error_detail=detail)
        return "failed"
    delay = compute_retry_delay_seconds(attempts)
    await requeue_job(job_id, delay_seconds=delay, error_code=code, error_detail=detail)
    return "requeued"


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
            reaped = await reap_expired_jobs()
            summary["reaped"] = len(reaped)
        except Exception as e:  # noqa: BLE001
            log_warning("drain reaper failed", subsystem=_SUBSYSTEM, provider="database",
                        category=classify_failure(e), exc=e, operation="drain_reap", outcome="error")

    claimed = await claim_jobs(wid, lease_seconds=lease_seconds, limit=limit)
    summary["claimed"] = len(claimed)

    for job in claimed:
        jid = uuid.UUID(job["job_id"])
        org = uuid.UUID(job["org_id"])
        ws = uuid.UUID(job["workspace_id"])
        fid = uuid.UUID(job["file_id"])
        attempts = int(job.get("attempts") or 0)
        try:
            payload = await asyncio.wait_for(
                process_file(org_id=org, workspace_id=ws, file_id=fid),
                timeout=per_job_timeout_s,
            )
            st = str(payload.get("status") or "")
            if st == "ready":
                await complete_job(jid, "succeeded")
                summary["succeeded"] += 1
                outcome = "succeeded"
            elif st == "failed":
                # process_file already classified a deterministic extraction failure;
                # record it terminally (do not consume further retries).
                await complete_job(jid, "failed",
                                   error_code=payload.get("failure_code"),
                                   error_detail=payload.get("failure_message"))
                summary["failed"] += 1
                outcome = "failed"
            else:
                # Unexpected non-terminal status -> treat as transient.
                res = await _retry_or_fail(jid, attempts, "unexpected_status", st, max_attempts=max_attempts)
                summary["requeued" if res == "requeued" else "failed"] += 1
                outcome = res
        except asyncio.TimeoutError:
            res = await _retry_or_fail(jid, attempts, "timeout",
                                       f"per-job timeout {per_job_timeout_s}s", max_attempts=max_attempts)
            summary["requeued" if res == "requeued" else "failed"] += 1
            outcome = res
        except Exception as e:  # noqa: BLE001
            res = await _retry_or_fail(jid, attempts, classify_failure(e),
                                       type(e).__name__, max_attempts=max_attempts)
            summary["requeued" if res == "requeued" else "failed"] += 1
            outcome = res
        log_info("drain job processed", subsystem=_SUBSYSTEM, operation="drain_job",
                 outcome="ok", job_id=str(jid), org_id=str(org), workspace_id=str(ws),
                 file_id=str(fid), attempt=attempts, result=outcome, worker_id=wid)

    log_info("drain cycle complete", subsystem=_SUBSYSTEM, operation="drain",
             outcome="ok", **{k: v for k, v in summary.items()})
    return summary
