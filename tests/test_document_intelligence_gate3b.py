"""Gate 3B — upload wired to the durable document_processing_jobs substrate.

Verifies the async flow: upload persists WorkspaceFile + enqueues a job atomically
(no synchronous extraction), and a bounded drain claims the job and runs the
existing process_file to reach READY. Covers failure/retry/lease-recovery and the
"never orphan a user file / never falsely READY / not exposed before READY"
guarantees. Real-DB tests SKIP cleanly when Postgres/asyncpg or the 024 schema is
unavailable.
"""
from __future__ import annotations

import io
import os
import shutil
import uuid

import pytest
import pytest_asyncio

try:
    import asyncpg
except Exception:  # pragma: no cover
    asyncpg = None

from services.workspace_files import service as file_service
from services.workspace_files import storage
from services.workspace_files.drain import drain_document_processing_jobs
from services.workspace_files.job_queue import (
    JOB_TYPE_FILE_EXTRACTION,
    enqueue_document_processing_job,
)

_DSN = os.getenv("BEN_TEST_PG_DSN") or "postgresql://ben:ben@127.0.0.1:5432/ben"


class _Upload:
    """Minimal UploadFile stand-in for storage.write_upload."""

    def __init__(self, filename: str, content_type: str, data: bytes):
        self.filename = filename
        self.content_type = content_type
        self._b = io.BytesIO(data)

    async def read(self, n: int = -1) -> bytes:
        return self._b.read(n)


async def _open():
    if asyncpg is None:
        pytest.skip("asyncpg not installed")
    try:
        conn = await asyncpg.connect(_DSN)
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"Postgres unavailable: {exc}")
    if not await conn.fetchval("SELECT to_regclass('ben.document_processing_jobs') IS NOT NULL"):
        await conn.close()
        pytest.skip("Gate 3A schema (024) not applied")
    return conn


async def _mk_workspace(conn, org_id) -> uuid.UUID:
    ws = uuid.uuid4()
    await conn.execute(
        "INSERT INTO ben.projects (id, org_id, name, status) VALUES ($1,$2,'g3b','active')", ws, org_id
    )
    return ws


async def _jobs(conn, fid):
    return await conn.fetch(
        "SELECT id, status, attempts, available_at, worker_id FROM ben.document_processing_jobs "
        "WHERE file_id=$1 ORDER BY created_at", fid,
    )


async def _file(conn, fid):
    return await conn.fetchrow(
        "SELECT status, extracted_text, failure_code FROM ben.workspace_files WHERE id=$1", fid
    )


def _cleanup_storage(org, ws):
    try:
        shutil.rmtree(storage.files_root() / str(org) / str(ws), ignore_errors=True)
    except Exception:
        pass


@pytest_asyncio.fixture
async def fresh_engine():
    from database.connection import dispose_engine
    await dispose_engine()
    yield
    await dispose_engine()


@pytest.fixture(autouse=True)
def _enable_async(monkeypatch):
    """Gate 3B async behavior is behind BEN_DOC_PROCESSING_ENABLED (default OFF).
    These tests exercise the async path, so enable it by default; the explicit
    OFF test overrides this within its own body."""
    monkeypatch.setenv("BEN_DOC_PROCESSING_ENABLED", "on")
    monkeypatch.delenv("BEN_DOC_UPLOAD_WAKE_ENABLED", raising=False)


async def _upload(org, ws, name, ct, data):
    return await file_service.upload_file(
        org_id=org, workspace_id=ws, upload=_Upload(name, ct, data), uploaded_by="tester"
    )


# =========================================================================== #
# Phase 1 — upload enqueues (no synchronous extraction)
# =========================================================================== #
@pytest.mark.asyncio
async def test_upload_creates_one_job_and_is_not_processed_synchronously(fresh_engine):
    conn = await _open()
    org = uuid.uuid4(); ws = await _mk_workspace(conn, org)
    try:
        payload = await _upload(org, ws, "notes.txt", "text/plain", b"hello gate3b content")
        fid = uuid.UUID(payload["id"])
        # Upload did NOT extract synchronously: non-ready, no extracted text yet.
        assert payload["status"] == "queued"
        f = await _file(conn, fid)
        assert f["status"] == "queued" and f["extracted_text"] is None
        # Exactly one durable job, queued, for this file.
        jobs = await _jobs(conn, fid)
        assert len(jobs) == 1 and jobs[0]["status"] == "queued" and jobs[0]["attempts"] == 0
    finally:
        await conn.execute("DELETE FROM ben.projects WHERE id=$1", ws); await conn.close(); _cleanup_storage(org, ws)


@pytest.mark.asyncio
async def test_queued_file_not_exposed_to_chat_before_ready(fresh_engine):
    conn = await _open()
    org = uuid.uuid4(); ws = await _mk_workspace(conn, org)
    try:
        await _upload(org, ws, "secret.txt", "text/plain", b"CONFIDENTIAL not ready yet")
        ctx = await file_service.load_ready_files_context(org, ws, max_chars=10000)
        assert ctx.block == "" and ctx.count == 0  # queued file must not reach chat context
    finally:
        await conn.execute("DELETE FROM ben.projects WHERE id=$1", ws); await conn.close(); _cleanup_storage(org, ws)


@pytest.mark.asyncio
async def test_upload_enqueue_failure_rolls_back_no_orphan_file(fresh_engine, monkeypatch):
    """file persistence + enqueue are atomic: if enqueue fails, no file row survives."""
    async def boom(*a, **k):
        raise RuntimeError("enqueue failed")
    monkeypatch.setattr("services.workspace_files.service.enqueue_document_processing_job", boom)
    conn = await _open()
    org = uuid.uuid4(); ws = await _mk_workspace(conn, org)
    try:
        with pytest.raises(Exception):
            await _upload(org, ws, "x.txt", "text/plain", b"data")
        assert await conn.fetchval("SELECT count(*) FROM ben.workspace_files WHERE workspace_id=$1", ws) == 0
        assert await conn.fetchval("SELECT count(*) FROM ben.document_processing_jobs WHERE workspace_id=$1", ws) == 0
    finally:
        await conn.execute("DELETE FROM ben.projects WHERE id=$1", ws); await conn.close(); _cleanup_storage(org, ws)


@pytest.mark.asyncio
async def test_duplicate_enqueue_protected(fresh_engine):
    conn = await _open()
    org = uuid.uuid4(); ws = await _mk_workspace(conn, org)
    try:
        payload = await _upload(org, ws, "d.txt", "text/plain", b"dup")
        fid = uuid.UUID(payload["id"])
        # A second enqueue for the same file+type+versions must not create a 2nd active job.
        res = await enqueue_document_processing_job(org, ws, fid, job_type=JOB_TYPE_FILE_EXTRACTION)
        assert res["created"] is False
        assert await conn.fetchval(
            "SELECT count(*) FROM ben.document_processing_jobs WHERE file_id=$1 AND status IN ('queued','running')", fid) == 1
    finally:
        await conn.execute("DELETE FROM ben.projects WHERE id=$1", ws); await conn.close(); _cleanup_storage(org, ws)


# =========================================================================== #
# Phase 2/3 — drain executes process_file; terminal + retry + recovery
# =========================================================================== #
@pytest.mark.asyncio
async def test_drain_success_marks_ready_and_completes_job(fresh_engine):
    conn = await _open()
    org = uuid.uuid4(); ws = await _mk_workspace(conn, org)
    try:
        payload = await _upload(org, ws, "doc.txt", "text/plain", b"hello gate3b content")
        fid = uuid.UUID(payload["id"])
        summary = await drain_document_processing_jobs(worker_id="t-worker", limit=10)
        assert summary["claimed"] >= 1 and summary["succeeded"] >= 1
        f = await _file(conn, fid)
        assert f["status"] == "ready" and f["extracted_text"] == "hello gate3b content"
        jobs = await _jobs(conn, fid)
        assert len(jobs) == 1 and jobs[0]["status"] == "succeeded"
        # Now retrievable by chat context.
        ctx = await file_service.load_ready_files_context(org, ws, max_chars=10000)
        assert ctx.count == 1 and "hello gate3b content" in ctx.block
    finally:
        await conn.execute("DELETE FROM ben.projects WHERE id=$1", ws); await conn.close(); _cleanup_storage(org, ws)


@pytest.mark.asyncio
async def test_drain_extraction_failure_not_falsely_ready(fresh_engine):
    conn = await _open()
    org = uuid.uuid4(); ws = await _mk_workspace(conn, org)
    try:
        payload = await _upload(org, ws, "broken.pdf", "application/pdf", b"%%%not-a-real-pdf%%%")
        fid = uuid.UUID(payload["id"])
        await drain_document_processing_jobs(worker_id="t-worker", limit=10)
        f = await _file(conn, fid)
        assert f["status"] == "failed" and f["status"] != "ready"
        jobs = await _jobs(conn, fid)
        assert jobs[0]["status"] == "failed"
        # A failed file is never exposed to chat.
        ctx = await file_service.load_ready_files_context(org, ws, max_chars=10000)
        assert ctx.count == 0
    finally:
        await conn.execute("DELETE FROM ben.projects WHERE id=$1", ws); await conn.close(); _cleanup_storage(org, ws)


@pytest.mark.asyncio
async def test_drain_transient_failure_requeues_with_backoff(fresh_engine, monkeypatch):
    async def raise_transient(*a, **k):
        raise RuntimeError("transient db blip")
    # Gate 3C: the async executor is the structured pipeline (not process_file).
    monkeypatch.setattr("services.workspace_files.drain.run_structured_extraction", raise_transient)
    conn = await _open()
    org = uuid.uuid4(); ws = await _mk_workspace(conn, org)
    try:
        payload = await _upload(org, ws, "r.txt", "text/plain", b"retry me")
        fid = uuid.UUID(payload["id"])
        summary = await drain_document_processing_jobs(worker_id="t-worker", limit=10)
        assert summary["requeued"] >= 1
        jobs = await _jobs(conn, fid)
        assert jobs[0]["status"] == "queued" and jobs[0]["attempts"] == 1
        assert jobs[0]["available_at"] > await conn.fetchval("SELECT now()")  # future backoff
        assert jobs[0]["worker_id"] is None
    finally:
        await conn.execute("DELETE FROM ben.projects WHERE id=$1", ws); await conn.close(); _cleanup_storage(org, ws)


@pytest.mark.asyncio
async def test_drain_recovers_expired_lease_then_completes(fresh_engine):
    """Worker-crash recovery: a running job with an expired lease is reaped and
    then processed to READY within the drain cycle (idempotent re-run)."""
    conn = await _open()
    org = uuid.uuid4(); ws = await _mk_workspace(conn, org)
    try:
        payload = await _upload(org, ws, "c.txt", "text/plain", b"crash recovery")
        fid = uuid.UUID(payload["id"])
        jid = (await _jobs(conn, fid))[0]["id"]
        # Simulate a worker that claimed then died: running + expired lease.
        await conn.execute(
            "UPDATE ben.document_processing_jobs SET status='running', attempts=1, claimed_at=now(), "
            "lease_expires_at=now() - interval '1 min', worker_id='dead' WHERE id=$1", jid)
        # Drain reaps the expired lease -> requeued with backoff (future available_at),
        # so it is recovered (not stuck 'running', not falsely READY), but not re-claimed
        # in the same cycle.
        summary = await drain_document_processing_jobs(worker_id="t-worker", limit=10)
        assert summary["reaped"] >= 1
        job = (await _jobs(conn, fid))[0]
        assert job["status"] == "queued" and job["worker_id"] is None  # recovered
        assert job["available_at"] > await conn.fetchval("SELECT now()")  # backoff
        assert (await _file(conn, fid))["status"] != "ready"  # never falsely READY
        # After backoff elapses, the next drain claims and completes it.
        await conn.execute(
            "UPDATE ben.document_processing_jobs SET available_at=now() WHERE id=$1", jid)
        await drain_document_processing_jobs(worker_id="t-worker", limit=10)
        f = await _file(conn, fid)
        assert f["status"] == "ready" and f["extracted_text"] == "crash recovery"
        assert (await _jobs(conn, fid))[0]["status"] == "succeeded"
    finally:
        await conn.execute("DELETE FROM ben.projects WHERE id=$1", ws); await conn.close(); _cleanup_storage(org, ws)


@pytest.mark.asyncio
async def test_second_drain_is_noop_no_double_processing(fresh_engine):
    conn = await _open()
    org = uuid.uuid4(); ws = await _mk_workspace(conn, org)
    try:
        await _upload(org, ws, "one.txt", "text/plain", b"once")
        first = await drain_document_processing_jobs(worker_id="t-worker", limit=10)
        assert first["succeeded"] >= 1
        second = await drain_document_processing_jobs(worker_id="t-worker", limit=10)
        assert second["claimed"] == 0 and second["succeeded"] == 0  # nothing left to do
    finally:
        await conn.execute("DELETE FROM ben.projects WHERE id=$1", ws); await conn.close(); _cleanup_storage(org, ws)


@pytest.mark.asyncio
async def test_process_file_rerun_is_idempotent(fresh_engine):
    """Worker dying after extraction but before completion is safe: re-running
    process_file on an already-ready file keeps it ready (no corruption)."""
    conn = await _open()
    org = uuid.uuid4(); ws = await _mk_workspace(conn, org)
    try:
        payload = await _upload(org, ws, "idem.txt", "text/plain", b"idempotent body")
        fid = uuid.UUID(payload["id"])
        r1 = await file_service.process_file(org_id=org, workspace_id=ws, file_id=fid)
        r2 = await file_service.process_file(org_id=org, workspace_id=ws, file_id=fid)
        assert r1["status"] == "ready" and r2["status"] == "ready"
        assert r2["has_extracted_text"] is True
    finally:
        await conn.execute("DELETE FROM ben.projects WHERE id=$1", ws); await conn.close(); _cleanup_storage(org, ws)


@pytest.mark.asyncio
async def test_flag_off_upload_is_synchronous(fresh_engine, monkeypatch):
    """OFF (default/fail-safe): upload behaves exactly as current production —
    synchronous extraction to READY, and NO durable job is created."""
    monkeypatch.setenv("BEN_DOC_PROCESSING_ENABLED", "off")
    conn = await _open()
    org = uuid.uuid4(); ws = await _mk_workspace(conn, org)
    try:
        payload = await _upload(org, ws, "sync.txt", "text/plain", b"sync path body")
        assert payload["status"] == "ready"  # processed within the request
        fid = uuid.UUID(payload["id"])
        f = await _file(conn, fid)
        assert f["status"] == "ready" and f["extracted_text"] == "sync path body"
        # No durable job in the synchronous path.
        assert await conn.fetchval(
            "SELECT count(*) FROM ben.document_processing_jobs WHERE file_id=$1", fid) == 0
        # Immediately retrievable (synchronous READY).
        ctx = await file_service.load_ready_files_context(org, ws, max_chars=10000)
        assert ctx.count == 1 and "sync path body" in ctx.block
    finally:
        await conn.execute("DELETE FROM ben.projects WHERE id=$1", ws); await conn.close(); _cleanup_storage(org, ws)


@pytest.mark.asyncio
async def test_flag_on_upload_is_async(fresh_engine, monkeypatch):
    """ON: upload persists + enqueues (queued, no synchronous extraction, one job)."""
    monkeypatch.setenv("BEN_DOC_PROCESSING_ENABLED", "on")
    conn = await _open()
    org = uuid.uuid4(); ws = await _mk_workspace(conn, org)
    try:
        payload = await _upload(org, ws, "async.txt", "text/plain", b"async path body")
        assert payload["status"] == "queued"  # not processed in the request
        fid = uuid.UUID(payload["id"])
        f = await _file(conn, fid)
        assert f["status"] == "queued" and f["extracted_text"] is None
        assert await conn.fetchval(
            "SELECT count(*) FROM ben.document_processing_jobs WHERE file_id=$1 AND status='queued'", fid) == 1
    finally:
        await conn.execute("DELETE FROM ben.projects WHERE id=$1", ws); await conn.close(); _cleanup_storage(org, ws)


def test_upload_source_is_flag_gated():
    """Static guard: upload_file routes on BEN_DOC_PROCESSING_ENABLED — enqueue when
    ON, synchronous process_file when OFF."""
    import pathlib
    src = pathlib.Path("services/workspace_files/service.py").read_text()
    upload_fn = src.split("async def upload_file", 1)[1].split("async def process_file", 1)[0]
    assert "_doc_processing_enabled" in upload_fn  # flag-gated
    assert "enqueue_document_processing_job" in upload_fn  # ON path
    assert "await process_file(" in upload_fn  # OFF path (synchronous, preserved)
