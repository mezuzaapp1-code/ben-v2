"""Gate 3B — internal document-processing drain endpoint.

System path only (cron-secret authenticated). Triggers one bounded drain cycle
that recovers expired leases, claims a bounded batch of document_processing_jobs,
and runs the existing extraction (process_file) to reach READY. No user-facing
behavior; not part of the product API surface.

A separate file-id-scoped path drains exactly one WorkspaceFile without claiming
the generic queue.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, Request

from auth.doc_processing_cron_auth import assert_doc_processing_cron
from services.ops.request_context import attach_request_id
from services.workspace_files.drain import (
    DEFAULT_DRAIN_LIMIT,
    default_worker_id,
    drain_document_processing_job_for_file,
    drain_document_processing_jobs,
    drain_document_processing_jobs_for_runner,
    runner_processing_stats,
)

router = APIRouter(prefix="/api/internal/documents", tags=["document-processing"])


@router.post("/processing/drain")
async def drain_processing_jobs(
    request: Request,
    limit: int = Query(DEFAULT_DRAIN_LIMIT, ge=1, le=50),
):
    """Bounded drain of durable document-processing jobs (cron-secret only)."""
    assert_doc_processing_cron(request)
    summary = await drain_document_processing_jobs(worker_id=default_worker_id(), limit=limit)
    return attach_request_id(summary)


@router.post("/processing/files/{file_id}/drain")
async def drain_processing_job_for_file(request: Request, file_id: uuid.UUID):
    """Drain exactly one file_id. Cron-secret only. No generic-queue fallback."""
    assert_doc_processing_cron(request)
    summary = await drain_document_processing_job_for_file(
        file_id, worker_id=default_worker_id(),
    )
    return attach_request_id(summary)


@router.post("/processing/runner/drain")
async def drain_processing_jobs_for_runner(
    request: Request,
    limit: int = Query(DEFAULT_DRAIN_LIMIT, ge=1, le=50),
):
    """Eligible-job runner drain. Cron-secret only. Never a silent FIFO fallback."""
    assert_doc_processing_cron(request)
    summary = await drain_document_processing_jobs_for_runner(
        worker_id=default_worker_id(), limit=limit,
    )
    return attach_request_id(summary)


@router.get("/processing/runner/stats")
async def document_processing_runner_stats(request: Request):
    """Queue gauges for the runner. Cron-secret only. Read-only."""
    assert_doc_processing_cron(request)
    return attach_request_id(await runner_processing_stats())
