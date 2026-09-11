"""Gate B — customer retry uses the structured job ledger, not process_file.

Retry must enqueue file_extraction and scoped-drain that job so WorkspaceFilePage
and WorkspaceFileChunk are preserved or created. Legacy extract_text-only READY
is a fail. Image-only stays a valid READY source with empty text.
Real-DB tests SKIP when Postgres / Gate 3A schema is unavailable.
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
from services.workspace_files.job_queue import JOB_TYPE_FILE_EXTRACTION

_DSN = os.getenv("BEN_TEST_PG_DSN") or "postgresql://ben:ben@127.0.0.1:5432/ben"
_PNG = b"\x89PNG\r\n\x1a\n binary image"


class _Upload:
    def __init__(self, filename, content_type, data: bytes):
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
    if not await conn.fetchval("SELECT to_regclass('ben.workspace_file_pages') IS NOT NULL"):
        await conn.close()
        pytest.skip("Gate 2 schema (023) not applied")
    return conn


async def _mk_workspace(conn, org):
    ws = uuid.uuid4()
    await conn.execute(
        "INSERT INTO ben.projects (id,org_id,name,status) VALUES ($1,$2,'gB','active')",
        ws,
        org,
    )
    return ws


async def _upload(org, ws, name, ct, data):
    return await file_service.upload_file(
        org_id=org, workspace_id=ws, upload=_Upload(name, ct, data), uploaded_by="tester"
    )


async def _file(conn, fid):
    return await conn.fetchrow(
        "SELECT status, extracted_text, failure_code, extraction_status, index_status, "
        "page_count, indexed_chunk_count, extraction_version, chunking_version, storage_key "
        "FROM ben.workspace_files WHERE id=$1",
        fid,
    )


async def _latest_job(conn, fid):
    return await conn.fetchrow(
        "SELECT status, job_type, attempts, extraction_version, chunking_version "
        "FROM ben.document_processing_jobs WHERE file_id=$1 ORDER BY created_at DESC, id DESC LIMIT 1",
        fid,
    )


async def _pages(conn, fid):
    return await conn.fetchval(
        "SELECT count(*) FROM ben.workspace_file_pages WHERE file_id=$1", fid
    )


async def _chunks(conn, fid):
    return await conn.fetchval(
        "SELECT count(*) FROM ben.workspace_file_chunks WHERE file_id=$1", fid
    )


def _cleanup_storage(org, ws):
    shutil.rmtree(storage.files_root() / str(org) / str(ws), ignore_errors=True)


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


def _forbid_process_file(monkeypatch):
    def process_file_must_not_run(**k):
        raise AssertionError("legacy process_file must not be called by retry")

    monkeypatch.setattr("services.workspace_files.service.process_file", process_file_must_not_run)


# ---- source guards ----------------------------------------------------------
def test_retry_route_does_not_call_process_file():
    src = pathlib.Path("routers/workspace_files.py").read_text()
    retry_fn = src.split("async def retry_workspace_file", 1)[1].split(
        "async def delete_workspace_file", 1
    )[0]
    assert "process_file" not in retry_fn
    assert "retry_file" in retry_fn
    alias = src.split("async def retry_project_file", 1)[1].split(
        "async def delete_project_file", 1
    )[0]
    assert "process_file" not in alias


def test_retry_service_uses_enqueue_and_scoped_drain_not_process_file():
    src = pathlib.Path("services/workspace_files/service.py").read_text()
    retry_fn = src.split("async def retry_file", 1)[1].split("\nasync def ", 1)[0]
    assert "enqueue_document_processing_job" in retry_fn
    assert "JOB_TYPE_FILE_EXTRACTION" in retry_fn
    assert "drain_document_processing_job_for_file" in retry_fn
    assert "await process_file" not in retry_fn
    assert "process_file(" not in retry_fn
    assert "run_structured_extraction" not in retry_fn
    assert "drain_document_processing_jobs(" not in retry_fn


def test_drain_source_still_uses_structured_not_process_file():
    src = pathlib.Path("services/workspace_files/drain.py").read_text()
    assert "run_structured_extraction" in src
    assert "process_file" not in src


# ---- Path B READY retry preserves pages/chunks ------------------------------
@pytest.mark.asyncio
async def test_retry_after_path_b_ready_preserves_pages_and_chunks(fresh_engine, monkeypatch):
    _forbid_process_file(monkeypatch)
    conn = await _open()
    org = uuid.uuid4()
    ws = await _mk_workspace(conn, org)
    try:
        p = await _upload(org, ws, "gate_b_ready.txt", "text/plain", b"gate b ready body")
        fid = uuid.UUID(p["id"])
        first = await drain_document_processing_job_for_file(fid, worker_id="gB-ready")
        assert first["succeeded"] == 1
        before = await _file(conn, fid)
        pages1, chunks1 = await _pages(conn, fid), await _chunks(conn, fid)
        assert before["status"] == "ready"
        assert before["index_status"] == "indexed"
        assert pages1 >= 1 and chunks1 >= 1
        assert before["indexed_chunk_count"] == chunks1

        out = await file_service.retry_file(org_id=org, workspace_id=ws, file_id=fid)
        after = await _file(conn, fid)
        pages2, chunks2 = await _pages(conn, fid), await _chunks(conn, fid)
        job = await _latest_job(conn, fid)

        assert out["status"] == "ready"
        assert after["status"] == "ready"
        assert after["index_status"] == "indexed"
        assert pages2 == pages1
        assert chunks2 == chunks1
        assert after["indexed_chunk_count"] == chunks2
        assert after["extraction_version"] == before["extraction_version"]
        assert after["chunking_version"] == before["chunking_version"]
        assert job["status"] == "succeeded"
        assert job["job_type"] == JOB_TYPE_FILE_EXTRACTION
        assert await conn.fetchval(
            "SELECT count(*) FROM ben.document_processing_jobs WHERE file_id=$1", fid
        ) == 2
    finally:
        await conn.execute("DELETE FROM ben.projects WHERE id=$1", ws)
        await conn.close()
        _cleanup_storage(org, ws)


# ---- failed structured retry is not extract_text-only READY -----------------
@pytest.mark.asyncio
async def test_retry_failed_structured_file_creates_pages_and_chunks(fresh_engine, monkeypatch):
    _forbid_process_file(monkeypatch)
    conn = await _open()
    org = uuid.uuid4()
    ws = await _mk_workspace(conn, org)
    try:
        body = b"structured retry restored body"
        p = await _upload(org, ws, "gate_b_failed.txt", "text/plain", body)
        fid = uuid.UUID(p["id"])
        row = await _file(conn, fid)
        path = storage.absolute_path_for_key(row["storage_key"])
        path.unlink()
        failed_drain = await drain_document_processing_job_for_file(fid, worker_id="gB-fail")
        assert failed_drain["failed"] == 1
        failed = await _file(conn, fid)
        assert failed["status"] == "failed"
        assert failed["failure_code"] == "missing_bytes"
        assert await _pages(conn, fid) == 0
        assert await _chunks(conn, fid) == 0
        failed_job = await _latest_job(conn, fid)
        assert failed_job["status"] == "failed"

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        out = await file_service.retry_file(org_id=org, workspace_id=ws, file_id=fid)
        after = await _file(conn, fid)
        pages, chunks = await _pages(conn, fid), await _chunks(conn, fid)
        job = await _latest_job(conn, fid)

        assert out["status"] == "ready"
        assert after["status"] == "ready"
        assert after["failure_code"] is None
        assert after["index_status"] == "indexed"
        assert pages >= 1 and chunks >= 1
        assert after["indexed_chunk_count"] == chunks
        assert (after["extracted_text"] or "").strip()
        assert job["status"] == "succeeded"
        assert job["job_type"] == JOB_TYPE_FILE_EXTRACTION
    finally:
        await conn.execute("DELETE FROM ben.projects WHERE id=$1", ws)
        await conn.close()
        _cleanup_storage(org, ws)


@pytest.mark.asyncio
async def test_retry_invokes_structured_drain_not_process_file(fresh_engine, monkeypatch):
    import services.workspace_files.drain as drain_mod

    real = drain_mod.run_structured_extraction
    calls = []

    async def spy(o, w, f):
        calls.append((o, w, f))
        return await real(o, w, f)

    monkeypatch.setattr("services.workspace_files.drain.run_structured_extraction", spy)
    _forbid_process_file(monkeypatch)

    conn = await _open()
    org = uuid.uuid4()
    ws = await _mk_workspace(conn, org)
    try:
        p = await _upload(org, ws, "gate_b_spy.txt", "text/plain", b"spy structured retry")
        fid = uuid.UUID(p["id"])
        await file_service.retry_file(org_id=org, workspace_id=ws, file_id=fid)
        assert calls == [(org, ws, fid)]
        assert await _chunks(conn, fid) >= 1
    finally:
        await conn.execute("DELETE FROM ben.projects WHERE id=$1", ws)
        await conn.close()
        _cleanup_storage(org, ws)


# ---- image-only remains valid READY empty text ------------------------------
@pytest.mark.asyncio
async def test_retry_image_only_stays_ready_empty_text_not_failed(fresh_engine, monkeypatch):
    _forbid_process_file(monkeypatch)
    conn = await _open()
    org = uuid.uuid4()
    ws = await _mk_workspace(conn, org)
    try:
        p = await _upload(org, ws, "scan.png", "image/png", _PNG)
        fid = uuid.UUID(p["id"])
        out1 = await file_service.retry_file(org_id=org, workspace_id=ws, file_id=fid)
        f1 = await _file(conn, fid)
        job1 = await _latest_job(conn, fid)
        assert out1["status"] == "ready"
        assert f1["status"] == "ready"
        assert f1["extracted_text"] == ""
        assert f1["failure_code"] is None
        assert f1["index_status"] == "not_indexed"
        assert await _chunks(conn, fid) == 0
        assert await conn.fetchval(
            "SELECT count(*) FROM ben.workspace_file_pages "
            "WHERE file_id=$1 AND extraction_status='needs_ocr'",
            fid,
        ) == 1
        assert job1["status"] == "succeeded"

        out2 = await file_service.retry_file(org_id=org, workspace_id=ws, file_id=fid)
        f2 = await _file(conn, fid)
        assert out2["status"] == "ready"
        assert f2["status"] == "ready"
        assert f2["extracted_text"] == ""
        assert f2["failure_code"] is None
        assert await _chunks(conn, fid) == 0
    finally:
        await conn.execute("DELETE FROM ben.projects WHERE id=$1", ws)
        await conn.close()
        _cleanup_storage(org, ws)
