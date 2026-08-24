"""W1 — post-commit scoped upload wake.

Upload returns without waiting for extraction. Fail-closed OFF. Runner must be
ON. Wake calls drain_document_processing_job_for_file only (no FIFO). Cron
remains the recovery path. Real-DB tests SKIP when Postgres is unavailable.
"""
from __future__ import annotations

import asyncio
import io
import os
import pathlib
import shutil
import time
import uuid
from unittest.mock import AsyncMock

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://ben:ben@127.0.0.1:5432/ben")

import pytest
import pytest_asyncio

try:
    import asyncpg
except Exception:  # pragma: no cover
    asyncpg = None

from services.workspace_files import service as file_service
from services.workspace_files import storage
from services.workspace_files.ingest_eligibility import PROTECTED_INGEST_FILE_IDS
from services.workspace_files.upload_wake import (
    DEFAULT_WAKE_CONCURRENCY,
    schedule_upload_wake,
    reset_upload_wake_for_tests,
    upload_wake_enabled,
    wait_for_inflight_wakes,
    wake_concurrency,
    active_wake_count,
)

_DSN = os.getenv("BEN_TEST_PG_DSN") or "postgresql://ben:ben@127.0.0.1:5432/ben"
_HIST_A = next(iter(PROTECTED_INGEST_FILE_IDS))
_PNG = b"\x89PNG\r\n\x1a\n binary image"
_JPG = bytes.fromhex("ffd8ffe000104a46494600010100000100010000ffd9")


class _Upload:
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
        pytest.skip("document_processing_jobs not applied")
    return conn


async def _mk_workspace(conn, org_id) -> uuid.UUID:
    ws = uuid.uuid4()
    await conn.execute(
        "INSERT INTO ben.projects (id, org_id, name, status) VALUES ($1,$2,'w1-wake','active')",
        ws, org_id,
    )
    return ws


async def _job(conn, fid):
    return await conn.fetchrow(
        """
        SELECT id, status, attempts, worker_id, runner_eligible, last_error_code
          FROM ben.document_processing_jobs
         WHERE file_id=$1
         ORDER BY created_at
         LIMIT 1
        """,
        fid,
    )


async def _file(conn, fid):
    return await conn.fetchrow(
        """
        SELECT status, extracted_text, extraction_status, index_status, failure_code
          FROM ben.workspace_files WHERE id=$1
        """,
        fid,
    )


def _cleanup_storage(org, ws):
    try:
        shutil.rmtree(storage.files_root() / str(org) / str(ws), ignore_errors=True)
    except Exception:
        pass


async def _cleanup_ws(conn, *workspaces):
    for ws in workspaces:
        if ws is None:
            continue
        await conn.execute("DELETE FROM ben.document_processing_jobs WHERE workspace_id=$1", ws)
        await conn.execute("DELETE FROM ben.workspace_files WHERE workspace_id=$1", ws)
        await conn.execute("DELETE FROM ben.projects WHERE id=$1", ws)


@pytest_asyncio.fixture
async def fresh_engine():
    from database.connection import dispose_engine
    await dispose_engine()
    yield
    await dispose_engine()


@pytest.fixture(autouse=True)
def _wake_defaults(monkeypatch):
    monkeypatch.setenv("BEN_DOC_PROCESSING_ENABLED", "on")
    monkeypatch.delenv("BEN_DOC_UPLOAD_WAKE_ENABLED", raising=False)
    monkeypatch.delenv("BEN_DOC_RUNNER_ENABLED", raising=False)
    monkeypatch.delenv("BEN_DOC_UPLOAD_WAKE_CONCURRENCY", raising=False)
    reset_upload_wake_for_tests()
    yield
    reset_upload_wake_for_tests()


async def _upload(org, ws, name, ct, data):
    return await file_service.upload_file(
        org_id=org, workspace_id=ws, upload=_Upload(name, ct, data), uploaded_by="tester",
    )


def _all_wake_flags_on(monkeypatch):
    monkeypatch.setenv("BEN_DOC_PROCESSING_ENABLED", "on")
    monkeypatch.setenv("BEN_DOC_RUNNER_ENABLED", "on")
    monkeypatch.setenv("BEN_DOC_UPLOAD_WAKE_ENABLED", "on")


# =========================================================================== #
# Unit — flags, scheduling, no FIFO, upload does not await
# =========================================================================== #
def test_wake_flag_defaults_fail_closed():
    assert upload_wake_enabled() is False
    assert wake_concurrency() == DEFAULT_WAKE_CONCURRENCY == 2


def test_upload_source_schedules_wake_after_commit_and_does_not_await_drain():
    src = pathlib.Path("services/workspace_files/service.py").read_text()
    upload_fn = src.split("async def upload_file", 1)[1].split("async def process_file", 1)[0]
    assert "schedule_upload_wake" in upload_fn
    assert "await schedule_upload_wake" not in upload_fn
    assert "await drain_document_processing_job_for_file" not in upload_fn
    assert "drain_document_processing_jobs(" not in upload_fn
    assert "claim_jobs_for_eligible" not in upload_fn
    assert "BEN_WORKSPACE_CHUNK_RETRIEVAL" not in upload_fn
    assert upload_fn.index("await session.commit()") < upload_fn.index("schedule_upload_wake")
    assert "await process_file(" in upload_fn  # synchronous OFF path unchanged


def test_wake_module_calls_scoped_drain_never_fifo():
    src = pathlib.Path("services/workspace_files/upload_wake.py").read_text()
    assert "drain_document_processing_job_for_file" in src
    assert "drain_document_processing_jobs(" not in src
    assert "drain_document_processing_jobs_for_runner" not in src
    assert "claim_jobs_for_eligible" not in src
    assert "claim_jobs(" not in src
    assert "BEN_WORKSPACE_CHUNK_RETRIEVAL" not in src


def test_wake_disabled_does_not_schedule(monkeypatch):
    _all_wake_flags_on(monkeypatch)
    monkeypatch.delenv("BEN_DOC_UPLOAD_WAKE_ENABLED", raising=False)
    drain = AsyncMock()
    monkeypatch.setattr(
        "services.workspace_files.drain.drain_document_processing_job_for_file", drain,
    )
    assert schedule_upload_wake(uuid.uuid4()) is False
    drain.assert_not_called()


def test_runner_off_does_not_schedule(monkeypatch):
    monkeypatch.setenv("BEN_DOC_PROCESSING_ENABLED", "on")
    monkeypatch.setenv("BEN_DOC_UPLOAD_WAKE_ENABLED", "on")
    monkeypatch.delenv("BEN_DOC_RUNNER_ENABLED", raising=False)
    drain = AsyncMock()
    monkeypatch.setattr(
        "services.workspace_files.drain.drain_document_processing_job_for_file", drain,
    )
    assert schedule_upload_wake(uuid.uuid4()) is False
    drain.assert_not_called()


def test_quarantined_ids_are_not_woken(monkeypatch):
    _all_wake_flags_on(monkeypatch)
    drain = AsyncMock()
    monkeypatch.setattr(
        "services.workspace_files.drain.drain_document_processing_job_for_file", drain,
    )
    assert schedule_upload_wake(_HIST_A) is False
    drain.assert_not_called()


@pytest.mark.asyncio
async def test_schedule_does_not_wait_for_extraction(monkeypatch):
    _all_wake_flags_on(monkeypatch)
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_drain(file_id, **kwargs):
        started.set()
        await release.wait()
        return {"claimed": 1, "succeeded": 1}

    monkeypatch.setattr(
        "services.workspace_files.drain.drain_document_processing_job_for_file",
        slow_drain,
    )
    t0 = time.perf_counter()
    scheduled = schedule_upload_wake(uuid.uuid4())
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    assert scheduled is True
    assert elapsed_ms < 200
    await asyncio.wait_for(started.wait(), timeout=1)
    assert active_wake_count() == 1
    release.set()
    await wait_for_inflight_wakes()
    assert active_wake_count() == 0


@pytest.mark.asyncio
async def test_semaphore_full_skips_without_creating_backlog(monkeypatch):
    _all_wake_flags_on(monkeypatch)
    monkeypatch.setenv("BEN_DOC_UPLOAD_WAKE_CONCURRENCY", "1")
    release = asyncio.Event()
    calls = []

    async def holder(file_id, **kwargs):
        calls.append(str(file_id))
        await release.wait()
        return {"claimed": 1}

    monkeypatch.setattr(
        "services.workspace_files.drain.drain_document_processing_job_for_file",
        holder,
    )
    first = uuid.uuid4()
    second = uuid.uuid4()
    assert schedule_upload_wake(first) is True
    await asyncio.sleep(0)
    assert schedule_upload_wake(second) is False
    assert active_wake_count() == 1
    assert calls == [str(first)]
    release.set()
    await wait_for_inflight_wakes()
    assert active_wake_count() == 0


@pytest.mark.asyncio
async def test_wake_exception_is_swallowed(monkeypatch):
    _all_wake_flags_on(monkeypatch)

    async def boom(file_id, **kwargs):
        raise RuntimeError("drain exploded")

    monkeypatch.setattr(
        "services.workspace_files.drain.drain_document_processing_job_for_file",
        boom,
    )
    assert schedule_upload_wake(uuid.uuid4()) is True
    await wait_for_inflight_wakes()
    assert active_wake_count() == 0


# =========================================================================== #
# Postgres — upload contract
# =========================================================================== #
@pytest.mark.asyncio
async def test_flag_off_leaves_job_queued_for_cron(fresh_engine, monkeypatch):
    from services.workspace_files.drain import drain_document_processing_jobs_for_runner

    monkeypatch.setenv("BEN_DOC_RUNNER_ENABLED", "on")
    monkeypatch.delenv("BEN_DOC_UPLOAD_WAKE_ENABLED", raising=False)
    conn = await _open()
    org = ws = None
    try:
        org = uuid.uuid4()
        ws = await _mk_workspace(conn, org)
        payload = await _upload(org, ws, "cron.txt", "text/plain", b"cron recovery body")
        await wait_for_inflight_wakes()
        fid = uuid.UUID(payload["id"])
        assert payload["status"] == "queued"
        assert (await _job(conn, fid))["status"] == "queued"
        assert (await _file(conn, fid))["status"] == "queued"
        summary = await drain_document_processing_jobs_for_runner(worker_id="cron-fallback", limit=5)
        assert summary["succeeded"] == 1
        assert (await _file(conn, fid))["status"] == "ready"
        assert (await _file(conn, fid))["extracted_text"] == "cron recovery body"
    finally:
        await _cleanup_ws(conn, ws)
        await conn.close()
        if org is not None and ws is not None:
            _cleanup_storage(org, ws)


@pytest.mark.asyncio
async def test_runner_off_does_not_wake_on_upload(fresh_engine, monkeypatch):
    monkeypatch.setenv("BEN_DOC_UPLOAD_WAKE_ENABLED", "on")
    monkeypatch.delenv("BEN_DOC_RUNNER_ENABLED", raising=False)
    conn = await _open()
    org = ws = None
    try:
        org = uuid.uuid4()
        ws = await _mk_workspace(conn, org)
        payload = await _upload(org, ws, "nowake.txt", "text/plain", b"still queued")
        await wait_for_inflight_wakes()
        fid = uuid.UUID(payload["id"])
        assert payload["status"] == "queued"
        assert (await _job(conn, fid))["status"] == "queued"
        assert (await _file(conn, fid))["status"] == "queued"
    finally:
        await _cleanup_ws(conn, ws)
        await conn.close()
        if org is not None and ws is not None:
            _cleanup_storage(org, ws)


@pytest.mark.asyncio
async def test_upload_http_returns_before_slow_extraction(fresh_engine, monkeypatch):
    _all_wake_flags_on(monkeypatch)
    release = asyncio.Event()
    started = asyncio.Event()

    async def slow_drain(file_id, **kwargs):
        started.set()
        await release.wait()
        return {"claimed": 0, "outcome": "held"}

    monkeypatch.setattr(
        "services.workspace_files.drain.drain_document_processing_job_for_file",
        slow_drain,
    )
    conn = await _open()
    org = ws = None
    try:
        org = uuid.uuid4()
        ws = await _mk_workspace(conn, org)
        t0 = time.perf_counter()
        payload = await _upload(org, ws, "fast.txt", "text/plain", b"return immediately")
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        assert elapsed_ms < 5000
        assert payload["status"] == "queued"
        await asyncio.wait_for(started.wait(), timeout=2)
        assert (await _job(conn, uuid.UUID(payload["id"])))["status"] == "queued"
    finally:
        release.set()
        await wait_for_inflight_wakes()
        await _cleanup_ws(conn, ws)
        await conn.close()
        if org is not None and ws is not None:
            _cleanup_storage(org, ws)


@pytest.mark.asyncio
async def test_wake_on_text_file_reaches_ready(fresh_engine, monkeypatch):
    _all_wake_flags_on(monkeypatch)
    conn = await _open()
    org = ws = None
    try:
        org = uuid.uuid4()
        ws = await _mk_workspace(conn, org)
        payload = await _upload(org, ws, "notes.txt", "text/plain", b"hello wake text")
        assert payload["status"] == "queued"
        fid = uuid.UUID(payload["id"])
        await wait_for_inflight_wakes()
        job = await _job(conn, fid)
        row = await _file(conn, fid)
        assert job["status"] == "succeeded"
        assert job["attempts"] == 1
        assert row["status"] == "ready"
        assert row["extracted_text"] == "hello wake text"
    finally:
        await _cleanup_ws(conn, ws)
        await conn.close()
        if org is not None and ws is not None:
            _cleanup_storage(org, ws)


@pytest.mark.asyncio
async def test_wake_on_image_no_text_reaches_ready(fresh_engine, monkeypatch):
    _all_wake_flags_on(monkeypatch)
    conn = await _open()
    org = ws = None
    try:
        org = uuid.uuid4()
        ws = await _mk_workspace(conn, org)
        payload = await _upload(org, ws, "scan.png", "image/png", _PNG)
        assert payload["status"] == "queued"
        fid = uuid.UUID(payload["id"])
        await wait_for_inflight_wakes()
        job = await _job(conn, fid)
        row = await _file(conn, fid)
        assert job["status"] == "succeeded"
        assert job["attempts"] == 1
        assert row["status"] == "ready"
        assert row["extracted_text"] == ""
        assert row["failure_code"] is None
        jpg = await _upload(org, ws, "photo.jpg", "image/jpeg", _JPG)
        await wait_for_inflight_wakes()
        jrow = await _file(conn, uuid.UUID(jpg["id"]))
        jjob = await _job(conn, uuid.UUID(jpg["id"]))
        assert jjob["status"] == "succeeded"
        assert jrow["status"] == "ready"
        assert jrow["extracted_text"] == ""
    finally:
        await _cleanup_ws(conn, ws)
        await conn.close()
        if org is not None and ws is not None:
            _cleanup_storage(org, ws)


@pytest.mark.asyncio
async def test_concurrent_cron_and_wake_claim_once(fresh_engine, monkeypatch):
    from services.workspace_files.drain import drain_document_processing_jobs_for_runner

    _all_wake_flags_on(monkeypatch)
    conn = await _open()
    org = ws = None
    try:
        org = uuid.uuid4()
        ws = await _mk_workspace(conn, org)
        payload = await _upload(org, ws, "once.txt", "text/plain", b"single claim body")
        fid = uuid.UUID(payload["id"])
        runner = asyncio.create_task(
            drain_document_processing_jobs_for_runner(worker_id="cron-race", limit=5)
        )
        await wait_for_inflight_wakes()
        summary = await runner
        job = await _job(conn, fid)
        row = await _file(conn, fid)
        assert job["status"] == "succeeded"
        assert job["attempts"] == 1
        assert row["status"] == "ready"
        assert row["extracted_text"] == "single claim body"
        claimed = int(summary.get("claimed") or 0)
        succeeded = int(summary.get("succeeded") or 0)
        assert claimed + succeeded <= 2
        assert claimed <= 1
    finally:
        await _cleanup_ws(conn, ws)
        await conn.close()
        if org is not None and ws is not None:
            _cleanup_storage(org, ws)


@pytest.mark.asyncio
async def test_second_wake_is_noop(fresh_engine, monkeypatch):
    from services.workspace_files.drain import drain_document_processing_job_for_file

    _all_wake_flags_on(monkeypatch)
    conn = await _open()
    org = ws = None
    try:
        org = uuid.uuid4()
        ws = await _mk_workspace(conn, org)
        payload = await _upload(org, ws, "once.txt", "text/plain", b"first wake wins")
        fid = uuid.UUID(payload["id"])
        await wait_for_inflight_wakes()
        assert (await _job(conn, fid))["status"] == "succeeded"
        second = await drain_document_processing_job_for_file(fid, worker_id="second-wake")
        assert second["claimed"] == 0
        assert second["succeeded"] == 0
        assert (await _job(conn, fid))["attempts"] == 1
        assert (await _file(conn, fid))["status"] == "ready"
    finally:
        await _cleanup_ws(conn, ws)
        await conn.close()
        if org is not None and ws is not None:
            _cleanup_storage(org, ws)


@pytest.mark.asyncio
async def test_semaphore_skip_leaves_durable_job_recoverable(fresh_engine, monkeypatch):
    from services.workspace_files.drain import drain_document_processing_jobs_for_runner

    _all_wake_flags_on(monkeypatch)
    monkeypatch.setenv("BEN_DOC_UPLOAD_WAKE_CONCURRENCY", "1")
    hold = asyncio.Event()
    real_drain = None

    async def gated_drain(file_id, **kwargs):
        await hold.wait()
        return await real_drain(file_id, **kwargs)

    import services.workspace_files.drain as drain_mod
    real_drain = drain_mod.drain_document_processing_job_for_file
    monkeypatch.setattr(drain_mod, "drain_document_processing_job_for_file", gated_drain)

    conn = await _open()
    org = ws = None
    try:
        org = uuid.uuid4()
        ws = await _mk_workspace(conn, org)
        first = await _upload(org, ws, "held.txt", "text/plain", b"occupies the slot")
        await asyncio.sleep(0)
        second = await _upload(org, ws, "skipped.txt", "text/plain", b"cron will recover")
        sid = uuid.UUID(second["id"])
        assert (await _job(conn, sid))["status"] == "queued"
        hold.set()
        await wait_for_inflight_wakes()
        assert (await _job(conn, sid))["status"] == "queued"
        summary = await drain_document_processing_jobs_for_runner(worker_id="recover-skip", limit=5)
        assert summary["succeeded"] >= 1
        assert (await _file(conn, sid))["status"] == "ready"
        assert (await _file(conn, sid))["extracted_text"] == "cron will recover"
        assert (await _file(conn, uuid.UUID(first["id"])))["status"] == "ready"
    finally:
        hold.set()
        await wait_for_inflight_wakes()
        await _cleanup_ws(conn, ws)
        await conn.close()
        if org is not None and ws is not None:
            _cleanup_storage(org, ws)


@pytest.mark.asyncio
async def test_wake_exception_does_not_fail_upload_http(fresh_engine, monkeypatch):
    _all_wake_flags_on(monkeypatch)

    def boom(file_id):
        raise RuntimeError("wake must not fail upload")

    monkeypatch.setattr(
        "services.workspace_files.service.schedule_upload_wake", boom,
    )
    conn = await _open()
    org = ws = None
    try:
        org = uuid.uuid4()
        ws = await _mk_workspace(conn, org)
        payload = await _upload(org, ws, "survive.txt", "text/plain", b"upload lives")
        fid = uuid.UUID(payload["id"])
        assert payload["status"] == "queued"
        assert payload["id"]
        assert (await _job(conn, fid))["status"] == "queued"
    finally:
        await _cleanup_ws(conn, ws)
        await conn.close()
        if org is not None and ws is not None:
            _cleanup_storage(org, ws)


@pytest.mark.asyncio
async def test_true_extraction_failure_stays_failed(fresh_engine, monkeypatch):
    _all_wake_flags_on(monkeypatch)
    conn = await _open()
    org = ws = None
    try:
        org = uuid.uuid4()
        ws = await _mk_workspace(conn, org)
        payload = await _upload(
            org, ws, "broken.pdf", "application/pdf", b"%%%not-a-real-pdf%%%",
        )
        fid = uuid.UUID(payload["id"])
        await wait_for_inflight_wakes()
        job = await _job(conn, fid)
        row = await _file(conn, fid)
        assert row["status"] == "failed"
        assert job["status"] == "failed"
    finally:
        await _cleanup_ws(conn, ws)
        await conn.close()
        if org is not None and ws is not None:
            _cleanup_storage(org, ws)
