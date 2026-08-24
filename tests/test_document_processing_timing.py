"""Document-processing lifecycle timing (observability only).

Proves:
1. Existing claim/retry/complete behavior is unchanged.
2. Successful jobs emit a structured timing record.
3. Failed jobs emit a structured timing record.
4. Retry does not overwrite original upload/job-created timestamps.
5. Telemetry never includes file content or secrets.
6. Requeue (non-terminal) does not emit timing.

Audit (no new schema): surviving timestamps are workspace_files.created_at,
document_processing_jobs.created_at, and workspace_files.updated_at.
DB claimed_at is cleared on complete/requeue, so claim time is stamped in memory.
"""
from __future__ import annotations

import io
import json
import logging
import os
import pathlib
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://ben:ben@127.0.0.1:5432/ben")

from database.models import DocumentProcessingJob, WorkspaceFile
from services.ops.json_log_formatter import STRUCTURED_FIELDS, BenOpsJsonFormatter
from services.workspace_files.drain import drain_document_processing_jobs
from services.workspace_files.job_queue import JOB_TYPE_STRUCTURED_EXTRACTION
from services.workspace_files.processing_timing import (
    TIMING_EVENT,
    TIMING_LOG_KEYS,
    _FORBIDDEN_LOG_KEYS,
    build_timing_payload,
    duration_ms,
    emit_terminal_timing,
    emit_timing_record,
    stamp_claimed_jobs,
)

SECRET_CANARY = "sk-live-must-never-appear-in-timing"
TEXT_CANARY = "PRIVATE_FILE_BODY_MUST_NOT_APPEAR"
CRON_CANARY = "BEN_CRON_SECRET=super-secret-cron"
TOKEN_CANARY = "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.timing"

T0 = datetime(2026, 8, 24, 13, 47, 2, 110000, tzinfo=timezone.utc)
T_JOB = datetime(2026, 8, 24, 13, 47, 2, 123000, tzinfo=timezone.utc)
T_CLAIM = datetime(2026, 8, 24, 13, 50, 5, 441000, tzinfo=timezone.utc)
T_START = datetime(2026, 8, 24, 13, 50, 5, 480000, tzinfo=timezone.utc)
T_FINISH = datetime(2026, 8, 24, 13, 50, 11, 812000, tzinfo=timezone.utc)
T_READY = datetime(2026, 8, 24, 13, 50, 11, 829000, tzinfo=timezone.utc)
T_COMPLETE = datetime(2026, 8, 24, 13, 50, 11, 845000, tzinfo=timezone.utc)
T_RETRY_CLAIM = datetime(2026, 8, 24, 13, 55, 0, 0, tzinfo=timezone.utc)

IDS = {
    "file_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    "job_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
    "org_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
    "workspace_id": "dddddddd-dddd-dddd-dddd-dddddddddddd",
}


def _payload(**overrides):
    base = dict(
        file_id=IDS["file_id"],
        job_id=IDS["job_id"],
        org_id=IDS["org_id"],
        workspace_id=IDS["workspace_id"],
        job_status="succeeded",
        attempts=1,
        uploaded_at=T0,
        job_created_at=T_JOB,
        claimed_at=T_CLAIM,
        processing_started_at=T_START,
        processing_finished_at=T_FINISH,
        ready_at=T_READY,
        file_status="ready",
        job_completed_at=T_COMPLETE,
    )
    base.update(overrides)
    return build_timing_payload(**base)


def _claimed_job(**overrides) -> dict:
    job = {
        "job_id": str(uuid.uuid4()),
        "org_id": str(uuid.uuid4()),
        "workspace_id": str(uuid.uuid4()),
        "file_id": str(uuid.uuid4()),
        "attempts": 1,
        "job_type": JOB_TYPE_STRUCTURED_EXTRACTION,
    }
    job.update(overrides)
    return job


def _patch_drain(monkeypatch, *, jobs, executor, complete=None, requeue=None, emit=None):
    monkeypatch.setattr(
        "services.workspace_files.drain.reap_expired_jobs",
        AsyncMock(return_value=[]),
    )

    async def fake_claim(*_a, **_k):
        return jobs

    monkeypatch.setattr("services.workspace_files.drain.claim_jobs", fake_claim)
    monkeypatch.setattr(
        "services.workspace_files.drain.run_structured_extraction",
        executor,
    )
    monkeypatch.setattr(
        "services.workspace_files.drain.complete_job",
        complete or AsyncMock(return_value={"status": "succeeded"}),
    )
    monkeypatch.setattr(
        "services.workspace_files.drain.requeue_job",
        requeue or AsyncMock(return_value={"status": "queued"}),
    )
    captured: list[dict] = []

    async def capturing_emit(**kwargs):
        captured.append(kwargs)
        if emit is not None:
            return await emit(**kwargs)
        return {"event": TIMING_EVENT}

    monkeypatch.setattr(
        "services.workspace_files.drain.emit_terminal_timing",
        capturing_emit,
    )
    return captured


# ---------------------------------------------------------------------------
# Audit: reuse existing columns; do not add schema
# ---------------------------------------------------------------------------
def test_audit_reuses_existing_columns_and_adds_no_schema():
    assert not hasattr(WorkspaceFile, "uploaded_at")
    assert not hasattr(WorkspaceFile, "ready_at")
    assert not hasattr(DocumentProcessingJob, "processing_started_at")
    assert not hasattr(DocumentProcessingJob, "processing_finished_at")
    assert not hasattr(DocumentProcessingJob, "job_created_at")
    assert hasattr(WorkspaceFile, "created_at")
    assert hasattr(WorkspaceFile, "updated_at")
    assert hasattr(DocumentProcessingJob, "created_at")
    assert hasattr(DocumentProcessingJob, "claimed_at")
    assert hasattr(DocumentProcessingJob, "updated_at")
    assert hasattr(DocumentProcessingJob, "available_at")
    versions = [p.name for p in pathlib.Path("database/migrations/versions").glob("*.py")]
    assert not any("timing" in name for name in versions)


def test_instrumentation_does_not_change_claim_or_cron_policy():
    drain = pathlib.Path("services/workspace_files/drain.py").read_text()
    queue = pathlib.Path("services/workspace_files/job_queue.py").read_text()
    router = pathlib.Path("routers/document_processing.py").read_text()
    claim_sql = pathlib.Path(
        "database/migrations/versions/027_runner_eligible_jobs.py"
    ).read_text()
    assert "BEN_DOC_RUNNER_ENABLED" not in drain.split("async def _run_claimed_jobs", 1)[1]
    assert "claim_jobs_for_eligible(" in drain
    assert "runner_eligible IS TRUE" in claim_sql
    assert "BEN_WORKSPACE_CHUNK_RETRIEVAL" not in drain
    assert "chunk_retriever" not in drain
    assert '@router.post("/processing/drain")' in router
    assert "available_at" in queue
    # available_at is backoff/scheduling, never a processing duration input.
    timing = pathlib.Path("services/workspace_files/processing_timing.py").read_text()
    assert "available_at" not in timing.split("def build_timing_payload", 1)[1].split(
        "async def _load_timing_anchors", 1
    )[0]


# ---------------------------------------------------------------------------
# Duration math
# ---------------------------------------------------------------------------
def test_successful_timing_metrics_match_lifecycle_boundaries():
    payload = _payload()
    assert payload["event"] == "document_processing_timing"
    assert payload["job_status"] == "succeeded"
    assert payload["attempts"] == 1
    assert payload["upload_to_job_ms"] == 13
    assert payload["job_to_claim_ms"] == 183318
    assert payload["claim_to_finish_ms"] == 6371
    assert payload["processing_ms"] == 6332
    assert payload["finish_to_ready_ms"] == 33
    assert payload["upload_to_ready_ms"] == 189719
    assert payload["uploaded_at"] == T0.isoformat()
    assert payload["job_created_at"] == T_JOB.isoformat()
    assert payload["claimed_at"] == T_CLAIM.isoformat()
    assert payload["processing_finished_at"] == T_FINISH.isoformat()
    assert payload["ready_at"] == T_READY.isoformat()
    assert set(payload) <= set(TIMING_LOG_KEYS)


def test_retry_uses_original_created_timestamps_and_latest_claim():
    """Backoff/wait until the latest claim is job_to_claim_ms, not processing time."""
    payload = _payload(
        job_status="succeeded",
        attempts=2,
        claimed_at=T_RETRY_CLAIM,
        processing_started_at=T_RETRY_CLAIM + timedelta(milliseconds=20),
        processing_finished_at=T_RETRY_CLAIM + timedelta(seconds=6),
        ready_at=T_RETRY_CLAIM + timedelta(seconds=6, milliseconds=20),
        job_completed_at=T_RETRY_CLAIM + timedelta(seconds=6, milliseconds=40),
    )
    assert payload["uploaded_at"] == T0.isoformat()
    assert payload["job_created_at"] == T_JOB.isoformat()
    assert payload["attempts"] == 2
    assert payload["claimed_at"] == T_RETRY_CLAIM.isoformat()
    assert payload["job_to_claim_ms"] == duration_ms(T_JOB, T_RETRY_CLAIM)
    assert payload["job_to_claim_ms"] > payload["processing_ms"]
    assert payload["processing_ms"] == 5980
    assert payload["upload_to_job_ms"] == 13


def test_missing_processing_start_falls_back_to_claimed_at():
    payload = _payload(processing_started_at=None)
    assert payload["processing_started_at"] == T_CLAIM.isoformat()
    assert payload["processing_ms"] == payload["claim_to_finish_ms"]


def test_duration_ms_clamps_negative_and_handles_none():
    later = T0 + timedelta(seconds=1)
    assert duration_ms(later, T0) == 0
    assert duration_ms(None, T0) is None
    assert duration_ms(T0, None) is None


def test_stamp_claimed_jobs_is_in_memory_only():
    jobs = [_claimed_job(), _claimed_job()]
    stamp = stamp_claimed_jobs(jobs, claimed_at=T_CLAIM)
    assert stamp == T_CLAIM
    assert all(j["_timing_claimed_at"] is T_CLAIM for j in jobs)


# ---------------------------------------------------------------------------
# Telemetry safety
# ---------------------------------------------------------------------------
def test_timing_payload_rejects_forbidden_keys():
    payload = _payload()
    assert _FORBIDDEN_LOG_KEYS.isdisjoint(payload)
    for key in (
        "extracted_text",
        "content",
        "filename",
        "api_key",
        "authorization",
        "token",
        "secret",
        "storage_key",
        TEXT_CANARY,
        SECRET_CANARY,
        CRON_CANARY,
    ):
        assert key not in payload
    dumped = json.dumps(payload)
    assert TEXT_CANARY not in dumped
    assert SECRET_CANARY not in dumped
    assert CRON_CANARY not in dumped
    assert TOKEN_CANARY not in dumped


def test_emit_timing_record_is_allowlisted_and_formatter_safe():
    payload = _payload()
    payload_with_noise = dict(payload)
    payload_with_noise["extracted_text"] = TEXT_CANARY
    payload_with_noise["api_key"] = SECRET_CANARY
    payload_with_noise["authorization"] = TOKEN_CANARY
    payload_with_noise["filename"] = "secret-contract.pdf"

    logger = logging.getLogger("ben.ops")
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setLevel(logging.INFO)
    handler.setFormatter(BenOpsJsonFormatter())
    logger.addHandler(handler)
    saved = logger.level
    logger.setLevel(logging.INFO)
    try:
        emit_timing_record(payload_with_noise)
    finally:
        logger.removeHandler(handler)
        logger.setLevel(saved)

    line = buf.getvalue().strip().splitlines()[-1]
    parsed = json.loads(line)
    assert parsed["event"] == TIMING_EVENT
    assert parsed["operation"] == "document_processing_timing"
    assert parsed["file_id"] == IDS["file_id"]
    assert parsed["job_id"] == IDS["job_id"]
    assert parsed["upload_to_ready_ms"] == 189719
    assert parsed["claim_to_finish_ms"] == 6371
    assert parsed["processing_ms"] == 6332
    assert TEXT_CANARY not in line
    assert SECRET_CANARY not in line
    assert TOKEN_CANARY not in line
    assert "secret-contract.pdf" not in line
    assert "extracted_text" not in parsed
    assert "api_key" not in parsed
    assert "filename" not in parsed
    for key in (
        "upload_to_job_ms",
        "job_to_claim_ms",
        "claim_to_finish_ms",
        "finish_to_ready_ms",
        "upload_to_ready_ms",
        "file_id",
        "job_id",
        "claimed_at",
        "processing_ms",
    ):
        assert key in STRUCTURED_FIELDS


def test_timing_log_keys_are_a_closed_allowlist():
    assert "extracted_text" not in TIMING_LOG_KEYS
    assert "content" not in TIMING_LOG_KEYS
    assert "api_key" not in TIMING_LOG_KEYS
    assert "authorization" not in TIMING_LOG_KEYS
    assert "filename" not in TIMING_LOG_KEYS
    assert TIMING_EVENT == "document_processing_timing"
    assert set(TIMING_LOG_KEYS) <= set(STRUCTURED_FIELDS) | {"event"}
    # event is already in STRUCTURED_FIELDS
    assert set(TIMING_LOG_KEYS) <= set(STRUCTURED_FIELDS)


# ---------------------------------------------------------------------------
# Drain wiring (behavior unchanged; emit on terminal only)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_successful_job_emits_timing_and_still_succeeds(monkeypatch):
    job = _claimed_job(attempts=1)

    async def ok_exec(*_a, **_k):
        return {"error": None, "final_extraction_status": "complete"}

    captured = _patch_drain(monkeypatch, jobs=[job], executor=ok_exec)
    summary = await drain_document_processing_jobs(
        worker_id="timing-ok", limit=1, reap=True, max_attempts=5,
    )
    assert summary["succeeded"] == 1
    assert summary["failed"] == 0
    assert summary["requeued"] == 0
    assert summary["claimed"] == 1
    assert len(captured) == 1
    emit = captured[0]
    assert emit["job_status"] == "succeeded"
    assert emit["job_id"] == uuid.UUID(job["job_id"])
    assert emit["file_id"] == uuid.UUID(job["file_id"])
    assert emit["attempts"] == 1
    assert emit["claimed_at"] is not None
    assert emit["processing_started_at"] is not None
    assert emit["processing_finished_at"] is not None
    assert emit["processing_finished_at"] >= emit["processing_started_at"]


@pytest.mark.asyncio
async def test_failed_job_emits_timing(monkeypatch):
    job = _claimed_job(attempts=1)

    async def fail_exec(*_a, **_k):
        return {"error": "missing_bytes"}

    complete = AsyncMock(return_value={"status": "failed"})
    captured = _patch_drain(
        monkeypatch, jobs=[job], executor=fail_exec, complete=complete,
    )
    summary = await drain_document_processing_jobs(
        worker_id="timing-fail", limit=1, max_attempts=5,
    )
    assert summary["failed"] == 1
    assert summary["succeeded"] == 0
    assert summary["requeued"] == 0
    complete.assert_awaited()
    assert complete.await_args.args[1] == "failed"
    assert len(captured) == 1
    assert captured[0]["job_status"] == "failed"
    assert captured[0]["attempts"] == 1


@pytest.mark.asyncio
async def test_requeue_does_not_emit_timing(monkeypatch):
    job = _claimed_job(attempts=1)

    async def boom(*_a, **_k):
        raise RuntimeError("transient blip")

    requeue = AsyncMock(return_value={"status": "queued"})
    complete = AsyncMock()
    captured = _patch_drain(
        monkeypatch, jobs=[job], executor=boom, requeue=requeue, complete=complete,
    )
    summary = await drain_document_processing_jobs(
        worker_id="timing-retry", limit=1, max_attempts=5,
    )
    assert summary["requeued"] == 1
    assert summary["failed"] == 0
    assert summary["succeeded"] == 0
    requeue.assert_awaited()
    complete.assert_not_awaited()
    assert captured == []


@pytest.mark.asyncio
async def test_retry_then_success_keeps_original_lifecycle_inputs(monkeypatch):
    """Second claim uses a new claimed_at; original created timestamps stay inputs."""
    original_created = T_JOB
    original_upload = T0
    payload = build_timing_payload(
        file_id=IDS["file_id"],
        job_id=IDS["job_id"],
        org_id=IDS["org_id"],
        workspace_id=IDS["workspace_id"],
        job_status="succeeded",
        attempts=2,
        uploaded_at=original_upload,
        job_created_at=original_created,
        claimed_at=T_RETRY_CLAIM,
        processing_started_at=T_RETRY_CLAIM,
        processing_finished_at=T_RETRY_CLAIM + timedelta(seconds=4),
        ready_at=T_RETRY_CLAIM + timedelta(seconds=4, milliseconds=10),
        job_completed_at=T_RETRY_CLAIM + timedelta(seconds=4, milliseconds=20),
    )
    assert payload["job_created_at"] == original_created.isoformat()
    assert payload["uploaded_at"] == original_upload.isoformat()
    assert payload["attempts"] == 2
    assert payload["claimed_at"] == T_RETRY_CLAIM.isoformat()

    job = _claimed_job(attempts=2)

    async def ok_exec(*_a, **_k):
        return {"error": None, "final_extraction_status": "complete"}

    captured = _patch_drain(monkeypatch, jobs=[job], executor=ok_exec)
    summary = await drain_document_processing_jobs(
        worker_id="timing-retry-ok", limit=1, max_attempts=5,
    )
    assert summary["succeeded"] == 1
    assert captured[0]["attempts"] == 2
    # Drain stamps claim time in memory and does not rewrite job created_at.
    assert "_timing_claimed_at" in job


@pytest.mark.asyncio
async def test_timing_emit_exception_does_not_change_processing_outcome(monkeypatch):
    job = _claimed_job()

    async def ok_exec(*_a, **_k):
        return {"error": None, "final_extraction_status": "complete"}

    async def exploding_emit(**_k):
        raise RuntimeError("telemetry down")

    _patch_drain(monkeypatch, jobs=[job], executor=ok_exec, emit=exploding_emit)
    summary = await drain_document_processing_jobs(
        worker_id="timing-safe", limit=1, max_attempts=5,
    )
    assert summary["succeeded"] == 1
    assert summary["failed"] == 0


@pytest.mark.asyncio
async def test_emit_terminal_timing_swallows_loader_errors(monkeypatch):
    async def boom(**_k):
        raise RuntimeError("db down")

    monkeypatch.setattr(
        "services.workspace_files.processing_timing._load_timing_anchors",
        boom,
    )
    result = await emit_terminal_timing(
        org_id=uuid.UUID(IDS["org_id"]),
        workspace_id=uuid.UUID(IDS["workspace_id"]),
        file_id=uuid.UUID(IDS["file_id"]),
        job_id=uuid.UUID(IDS["job_id"]),
        job_status="succeeded",
        attempts=1,
        claimed_at=T_CLAIM,
        processing_started_at=T_START,
        processing_finished_at=T_FINISH,
        job_completed_at=T_COMPLETE,
    )
    assert result is None


@pytest.mark.asyncio
async def test_emit_terminal_timing_builds_payload_from_surviving_anchors(monkeypatch):
    emitted: list[dict] = []

    async def anchors(**_k):
        return {
            "uploaded_at": T0,
            "file_updated_at": T_READY,
            "file_status": "ready",
            "job_created_at": T_JOB,
            "attempts": 1,
            "job_status": "succeeded",
        }

    monkeypatch.setattr(
        "services.workspace_files.processing_timing._load_timing_anchors",
        anchors,
    )
    monkeypatch.setattr(
        "services.workspace_files.processing_timing.emit_timing_record",
        lambda payload: emitted.append(payload),
    )
    payload = await emit_terminal_timing(
        org_id=uuid.UUID(IDS["org_id"]),
        workspace_id=uuid.UUID(IDS["workspace_id"]),
        file_id=uuid.UUID(IDS["file_id"]),
        job_id=uuid.UUID(IDS["job_id"]),
        job_status="succeeded",
        attempts=1,
        claimed_at=T_CLAIM,
        processing_started_at=T_START,
        processing_finished_at=T_FINISH,
        job_completed_at=T_COMPLETE,
    )
    assert payload is not None
    assert emitted == [payload]
    assert payload["event"] == TIMING_EVENT
    assert payload["job_status"] == "succeeded"
    assert payload["file_status"] == "ready"
    assert payload["upload_to_ready_ms"] == 189719
    assert payload["job_to_claim_ms"] == 183318
    dumped = json.dumps(payload)
    assert TEXT_CANARY not in dumped
    assert SECRET_CANARY not in dumped


@pytest.mark.asyncio
async def test_unknown_job_type_emits_failed_timing(monkeypatch):
    job = _claimed_job(job_type="not_a_real_type")

    async def must_not_run(*_a, **_k):
        raise AssertionError("executor must not run for unknown job_type")

    complete = AsyncMock(return_value={"status": "failed"})
    captured = _patch_drain(
        monkeypatch, jobs=[job], executor=must_not_run, complete=complete,
    )
    summary = await drain_document_processing_jobs(worker_id="timing-unknown", limit=1)
    assert summary["failed"] == 1
    complete.assert_awaited()
    assert complete.await_args.args[1] == "failed"
    assert len(captured) == 1
    assert captured[0]["job_status"] == "failed"


# ---------------------------------------------------------------------------
# Optional real-DB: retry must not rewrite created_at (upload/job birth)
# ---------------------------------------------------------------------------
_DSN = os.getenv("BEN_TEST_PG_DSN") or "postgresql://ben:ben@127.0.0.1:5432/ben"


async def _open_pg():
    try:
        import asyncpg
    except Exception:
        pytest.skip("asyncpg not installed")
    try:
        conn = await asyncpg.connect(_DSN)
    except Exception as exc:
        pytest.skip(f"Postgres unavailable: {exc}")
    if not await conn.fetchval(
        "SELECT to_regclass('ben.document_processing_jobs') IS NOT NULL"
    ):
        await conn.close()
        pytest.skip("Gate 3A schema (024) not applied")
    return conn


@pytest.mark.asyncio
async def test_retry_does_not_destroy_original_created_at_or_file_created_at():
    from services.workspace_files.job_queue import (
        claim_job_for_file,
        complete_job,
        requeue_job,
    )

    conn = await _open_pg()
    org = uuid.uuid4()
    ws = uuid.uuid4()
    fid = uuid.uuid4()
    try:
        await conn.execute(
            "INSERT INTO ben.projects (id,org_id,name,status) VALUES ($1,$2,'timing','active')",
            ws, org,
        )
        await conn.execute(
            """
            INSERT INTO ben.workspace_files
                (id, org_id, workspace_id, project_id, original_filename, display_name,
                 media_type, byte_size, checksum, storage_key, status)
            VALUES ($1,$2,$3,$3,'t.txt','t.txt','text/plain',0,'x',$4,'uploaded')
            """,
            fid, org, ws, f"k/{fid}",
        )
        jid = uuid.uuid4()
        await conn.execute(
            """
            INSERT INTO ben.document_processing_jobs
                (id, org_id, workspace_id, file_id, job_type, status,
                 extraction_version, chunking_version, attempts, max_attempts,
                 available_at, runner_eligible)
            VALUES ($1,$2,$3,$4,'structured_extraction','queued',1,1,0,5,now(),true)
            """,
            jid, org, ws, fid,
        )
        birth = await conn.fetchrow(
            """
            SELECT f.created_at AS uploaded_at, j.created_at AS job_created_at
              FROM ben.workspace_files f
              JOIN ben.document_processing_jobs j ON j.file_id = f.id
             WHERE j.id=$1
            """,
            jid,
        )
        claimed = await claim_job_for_file("timing-retry-db", fid, lease_seconds=300)
        assert any(c["job_id"] == str(jid) for c in claimed)
        await requeue_job(jid, delay_seconds=30, error_code="transient", error_detail="blip")
        after_retry = await conn.fetchrow(
            """
            SELECT f.created_at AS uploaded_at, j.created_at AS job_created_at,
                   j.status, j.claimed_at, j.available_at, j.attempts
              FROM ben.workspace_files f
              JOIN ben.document_processing_jobs j ON j.file_id = f.id
             WHERE j.id=$1
            """,
            jid,
        )
        assert after_retry["uploaded_at"] == birth["uploaded_at"]
        assert after_retry["job_created_at"] == birth["job_created_at"]
        assert after_retry["status"] == "queued"
        assert after_retry["claimed_at"] is None
        assert after_retry["available_at"] > await conn.fetchval("SELECT now()")

        await conn.execute(
            "UPDATE ben.document_processing_jobs SET available_at=now() WHERE id=$1",
            jid,
        )
        claimed2 = await claim_job_for_file("timing-retry-db-2", fid, lease_seconds=300)
        assert any(c["job_id"] == str(jid) for c in claimed2)
        await complete_job(jid, "succeeded")
        terminal = await conn.fetchrow(
            """
            SELECT f.created_at AS uploaded_at, j.created_at AS job_created_at,
                   j.status, j.claimed_at
              FROM ben.workspace_files f
              JOIN ben.document_processing_jobs j ON j.file_id = f.id
             WHERE j.id=$1
            """,
            jid,
        )
        assert terminal["uploaded_at"] == birth["uploaded_at"]
        assert terminal["job_created_at"] == birth["job_created_at"]
        assert terminal["status"] == "succeeded"
        assert terminal["claimed_at"] is None  # complete_job NULLs claimed_at
    finally:
        await conn.execute("DELETE FROM ben.projects WHERE id=$1", ws)
        await conn.close()
