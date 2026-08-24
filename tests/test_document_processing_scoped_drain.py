"""File-id-scoped document-processing drain.

Proves an operator can process exactly one queued WorkspaceFile without claiming
an earlier historical job. Generic drain is unchanged and is never used as a
fallback. Real-DB tests SKIP when Postgres or migration 025 is unavailable.
"""
from __future__ import annotations

import io
import os
import pathlib
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
from services.workspace_files.drain import drain_document_processing_job_for_file

_DSN = os.getenv("BEN_TEST_PG_DSN") or "postgresql://ben:ben@127.0.0.1:5432/ben"
_SCOPED_FN = "ben.claim_document_processing_job_for_file(text,integer,uuid)"


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
    if not await conn.fetchval("SELECT to_regprocedure($1) IS NOT NULL", _SCOPED_FN):
        await conn.close()
        pytest.skip("migration 025 claim_document_processing_job_for_file not applied")
    return conn


async def _mk_workspace(conn, org_id) -> uuid.UUID:
    ws = uuid.uuid4()
    await conn.execute(
        "INSERT INTO ben.projects (id, org_id, name, status) VALUES ($1,$2,'scoped-drain','active')",
        ws, org_id,
    )
    return ws


async def _job(conn, fid):
    return await conn.fetchrow(
        "SELECT id, status, attempts, available_at, worker_id, last_error_code "
        "FROM ben.document_processing_jobs WHERE file_id=$1 ORDER BY created_at LIMIT 1",
        fid,
    )


async def _file(conn, fid):
    return await conn.fetchrow(
        "SELECT status, extracted_text, extraction_status, index_status, indexed_chunk_count "
        "FROM ben.workspace_files WHERE id=$1",
        fid,
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
    monkeypatch.setenv("BEN_DOC_PROCESSING_ENABLED", "on")
    monkeypatch.delenv("BEN_DOC_UPLOAD_WAKE_ENABLED", raising=False)


async def _upload(org, ws, name, ct, data):
    return await file_service.upload_file(
        org_id=org, workspace_id=ws, upload=_Upload(name, ct, data), uploaded_by="tester",
    )


async def _queue_pair(conn):
    """Historical job first (older available_at), then a canary — production claim order."""
    org = uuid.uuid4()
    ws = await _mk_workspace(conn, org)
    historical = await _upload(
        org, ws, "PRICE QUOTATION _ TECHNICAL SPECIFICATION.pdf",
        "application/pdf", b"%%%not-a-real-pdf%%%",
    )
    canary = await _upload(org, ws, "gate3e_canary_20260815.txt", "text/plain", b"canary scoped drain")
    hid = uuid.UUID(historical["id"])
    cid = uuid.UUID(canary["id"])
    hj = await _job(conn, hid)
    cj = await _job(conn, cid)
    await conn.execute(
        "UPDATE ben.document_processing_jobs SET available_at = now() - interval '2 hours' WHERE id=$1",
        hj["id"],
    )
    await conn.execute(
        "UPDATE ben.document_processing_jobs SET available_at = now() - interval '1 hour' WHERE id=$1",
        cj["id"],
    )
    return org, ws, hid, cid


# =========================================================================== #
# Isolation
# =========================================================================== #
@pytest.mark.asyncio
async def test_scoped_drain_claims_canary_and_leaves_earlier_job_untouched(fresh_engine):
    conn = await _open()
    org = ws = None
    try:
        org, ws, hid, cid = await _queue_pair(conn)
        before_h = await _job(conn, hid)
        assert before_h["status"] == "queued" and before_h["attempts"] == 0

        summary = await drain_document_processing_job_for_file(cid, worker_id="scoped-canary")
        assert summary["claimed"] == 1
        assert summary["succeeded"] == 1
        assert summary["file_id"] == str(cid)
        assert summary["outcome"] == "succeeded"
        assert summary["job_id"] == str((await _job(conn, cid))["id"])

        canary = await _file(conn, cid)
        assert canary["status"] == "ready"
        assert canary["extracted_text"] == "canary scoped drain"
        assert (await _job(conn, cid))["status"] == "succeeded"

        historical = await _job(conn, hid)
        assert historical["status"] == "queued"
        assert historical["attempts"] == 0
        assert historical["worker_id"] is None
        assert historical["last_error_code"] is None
        hf = await _file(conn, hid)
        assert hf["status"] == "queued"
        assert hf["extracted_text"] is None
    finally:
        if ws is not None:
            await conn.execute("DELETE FROM ben.projects WHERE id=$1", ws)
        await conn.close()
        if org is not None and ws is not None:
            _cleanup_storage(org, ws)


@pytest.mark.asyncio
async def test_scoped_drain_nonexistent_file_id_touches_nothing(fresh_engine):
    conn = await _open()
    org = ws = None
    try:
        org, ws, hid, cid = await _queue_pair(conn)
        missing = uuid.uuid4()
        summary = await drain_document_processing_job_for_file(missing, worker_id="scoped-miss")
        assert summary["claimed"] == 0
        assert summary["succeeded"] == 0
        assert summary["failed"] == 0
        assert summary["requeued"] == 0
        assert summary["outcome"] == "no_eligible_job"
        assert summary["file_id"] == str(missing)
        assert summary["job_id"] is None

        for fid in (hid, cid):
            job = await _job(conn, fid)
            assert job["status"] == "queued" and job["attempts"] == 0
            assert (await _file(conn, fid))["status"] == "queued"
    finally:
        if ws is not None:
            await conn.execute("DELETE FROM ben.projects WHERE id=$1", ws)
        await conn.close()
        if org is not None and ws is not None:
            _cleanup_storage(org, ws)


@pytest.mark.asyncio
async def test_scoped_drain_wrong_file_id_touches_nothing_else(fresh_engine):
    conn = await _open()
    org = ws = None
    try:
        org, ws, hid, cid = await _queue_pair(conn)
        other = await _upload(org, ws, "other.txt", "text/plain", b"not the canary")
        oid = uuid.UUID(other["id"])

        summary = await drain_document_processing_job_for_file(oid, worker_id="scoped-other")
        assert summary["claimed"] == 1 and summary["file_id"] == str(oid)

        for fid in (hid, cid):
            job = await _job(conn, fid)
            assert job["status"] == "queued" and job["attempts"] == 0
            assert (await _file(conn, fid))["status"] == "queued"
        assert (await _file(conn, oid))["status"] == "ready"
    finally:
        if ws is not None:
            await conn.execute("DELETE FROM ben.projects WHERE id=$1", ws)
        await conn.close()
        if org is not None and ws is not None:
            _cleanup_storage(org, ws)


@pytest.mark.asyncio
async def test_scoped_claim_sql_never_returns_another_file(fresh_engine):
    conn = await _open()
    org = ws = None
    try:
        org, ws, hid, cid = await _queue_pair(conn)
        claimed = await conn.fetch(
            "SELECT * FROM ben.claim_document_processing_job_for_file('sql-scoped', 300, $1)",
            cid,
        )
        assert len(claimed) == 1
        assert claimed[0]["file_id"] == cid
        historical = await _job(conn, hid)
        assert historical["status"] == "queued" and historical["attempts"] == 0
        empty = await conn.fetch(
            "SELECT * FROM ben.claim_document_processing_job_for_file('sql-scoped', 300, $1)",
            uuid.uuid4(),
        )
        assert empty == []
        assert (await _job(conn, hid))["status"] == "queued"
        # Targeted job is running (claimed); do not leave a lease dangling for other tests.
        await conn.execute(
            "UPDATE ben.document_processing_jobs SET status='queued', attempts=0, "
            "claimed_at=NULL, lease_expires_at=NULL, worker_id=NULL WHERE file_id=$1",
            cid,
        )
    finally:
        if ws is not None:
            await conn.execute("DELETE FROM ben.projects WHERE id=$1", ws)
        await conn.close()
        if org is not None and ws is not None:
            _cleanup_storage(org, ws)


# =========================================================================== #
# Lease / retry semantics (target only)
# =========================================================================== #
@pytest.mark.asyncio
async def test_scoped_drain_transient_failure_requeues_only_target(fresh_engine, monkeypatch):
    async def raise_transient(*a, **k):
        raise RuntimeError("transient db blip")

    monkeypatch.setattr("services.workspace_files.drain.run_structured_extraction", raise_transient)
    conn = await _open()
    org = ws = None
    try:
        org, ws, hid, cid = await _queue_pair(conn)
        summary = await drain_document_processing_job_for_file(cid, worker_id="scoped-retry")
        assert summary["requeued"] == 1
        assert summary["outcome"] == "requeued"

        canary = await _job(conn, cid)
        assert canary["status"] == "queued" and canary["attempts"] == 1
        assert canary["available_at"] > await conn.fetchval("SELECT now()")
        assert canary["worker_id"] is None

        historical = await _job(conn, hid)
        assert historical["status"] == "queued" and historical["attempts"] == 0
        assert historical["last_error_code"] is None
    finally:
        if ws is not None:
            await conn.execute("DELETE FROM ben.projects WHERE id=$1", ws)
        await conn.close()
        if org is not None and ws is not None:
            _cleanup_storage(org, ws)


@pytest.mark.asyncio
async def test_scoped_drain_expired_lease_reaped_only_for_target(fresh_engine):
    conn = await _open()
    org = ws = None
    try:
        org, ws, hid, cid = await _queue_pair(conn)
        cj = await _job(conn, cid)
        await conn.execute(
            "UPDATE ben.document_processing_jobs SET status='running', attempts=1, claimed_at=now(), "
            "lease_expires_at=now() - interval '1 min', worker_id='dead' WHERE id=$1",
            cj["id"],
        )
        summary = await drain_document_processing_job_for_file(cid, worker_id="scoped-reap")
        assert summary["reaped"] == 1
        assert summary["claimed"] == 0  # backoff makes it unavailable this cycle
        assert summary["outcome"] == "reaped"

        canary = await _job(conn, cid)
        assert canary["status"] == "queued" and canary["worker_id"] is None
        assert canary["available_at"] > await conn.fetchval("SELECT now()")
        assert (await _file(conn, cid))["status"] != "ready"

        historical = await _job(conn, hid)
        assert historical["status"] == "queued" and historical["attempts"] == 0
        assert historical["worker_id"] is None
    finally:
        if ws is not None:
            await conn.execute("DELETE FROM ben.projects WHERE id=$1", ws)
        await conn.close()
        if org is not None and ws is not None:
            _cleanup_storage(org, ws)


@pytest.mark.asyncio
async def test_scoped_drain_second_call_is_noop(fresh_engine):
    conn = await _open()
    org = ws = None
    try:
        org, ws, hid, cid = await _queue_pair(conn)
        first = await drain_document_processing_job_for_file(cid, worker_id="scoped-once")
        assert first["succeeded"] == 1
        second = await drain_document_processing_job_for_file(cid, worker_id="scoped-once")
        assert second["claimed"] == 0 and second["succeeded"] == 0
        assert second["outcome"] == "no_eligible_job"
        assert (await _job(conn, hid))["status"] == "queued"
    finally:
        if ws is not None:
            await conn.execute("DELETE FROM ben.projects WHERE id=$1", ws)
        await conn.close()
        if org is not None and ws is not None:
            _cleanup_storage(org, ws)


# =========================================================================== #
# Generic drain must stay queue-ordered (unchanged)
# =========================================================================== #
def test_generic_claim_sql_still_unscoped_queue_order():
    """Generic claim must stay global FIFO — no file_id filter, no scoped fallback."""
    src = pathlib.Path("database/migrations/versions/024_document_processing_jobs.py").read_text()
    assert "ORDER BY c.available_at, c.created_at, c.id" in src
    claim_fn = src.split("CREATE OR REPLACE FUNCTION {SCHEMA}.claim_document_processing_jobs", 1)[1]
    claim_fn = claim_fn.split("CREATE OR REPLACE FUNCTION", 1)[0]
    assert "p_file_id" not in claim_fn
    assert "c.file_id =" not in claim_fn


def test_scoped_drain_source_never_calls_generic_claim():
    src = pathlib.Path("services/workspace_files/drain.py").read_text()
    scoped = src.split("async def drain_document_processing_job_for_file", 1)[1].split(
        "async def drain_document_processing_jobs_for_runner", 1
    )[0]
    generic = src.split("async def drain_document_processing_jobs(", 1)[1].split(
        "async def drain_document_processing_job_for_file", 1
    )[0]
    assert "claim_job_for_file(" in scoped
    assert "reap_expired_jobs_for_file(" in scoped
    assert "claim_jobs(" not in scoped
    assert "reap_expired_jobs(" not in scoped.replace("reap_expired_jobs_for_file", "")
    assert "claim_jobs(" in generic
    assert "claim_job_for_file(" not in generic


def test_generic_drain_router_unchanged_and_scoped_route_exists():
    src = pathlib.Path("routers/document_processing.py").read_text()
    assert '@router.post("/processing/drain")' in src
    assert '@router.post("/processing/files/{file_id}/drain")' in src
    assert "drain_document_processing_jobs(" in src
    assert "drain_document_processing_job_for_file(" in src
    # Generic handler must not accept a file_id that could silently scope it.
    generic_fn = src.split("async def drain_processing_jobs", 1)[1]
    generic_fn = generic_fn.split("\n@", 1)[0]
    assert "file_id" not in generic_fn
