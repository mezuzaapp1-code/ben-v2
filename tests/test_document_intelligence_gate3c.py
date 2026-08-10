"""Gate 3C — structured extraction wired into the durable drain.

Proves the async job now runs the Gate 2 structured pipeline
(run_structured_extraction) instead of legacy process_file: it persists
WorkspaceFilePage + WorkspaceFileChunk + truthful lifecycle, and projects the
legacy status/extracted_text (single parse, one transaction) so current chat
retrieval keeps working. Real-DB tests SKIP when Postgres/024 unavailable.
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
from services.workspace_files.chunking import Chunk
from services.workspace_files.drain import drain_document_processing_jobs
from tests.test_document_intelligence_gate2 import make_pdf

_DSN = os.getenv("BEN_TEST_PG_DSN") or "postgresql://ben:ben@127.0.0.1:5432/ben"


class _Upload:
    def __init__(self, filename, content_type, data: bytes):
        self.filename = filename; self.content_type = content_type; self._b = io.BytesIO(data)

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
        await conn.close(); pytest.skip("Gate 3A schema (024) not applied")
    return conn


async def _mk_workspace(conn, org):
    ws = uuid.uuid4()
    await conn.execute("INSERT INTO ben.projects (id,org_id,name,status) VALUES ($1,$2,'g3c','active')", ws, org)
    return ws


async def _upload(org, ws, name, ct, data):
    return await file_service.upload_file(
        org_id=org, workspace_id=ws, upload=_Upload(name, ct, data), uploaded_by="tester")


async def _file(conn, fid):
    return await conn.fetchrow(
        "SELECT status, extracted_text, extraction_status, index_status, page_count FROM ben.workspace_files WHERE id=$1", fid)


async def _job(conn, fid):
    return await conn.fetchrow(
        "SELECT status, attempts FROM ben.document_processing_jobs WHERE file_id=$1 ORDER BY created_at LIMIT 1", fid)


async def _pages(conn, fid):
    return await conn.fetchval("SELECT count(*) FROM ben.workspace_file_pages WHERE file_id=$1", fid)


async def _chunks(conn, fid):
    return await conn.fetchval("SELECT count(*) FROM ben.workspace_file_chunks WHERE file_id=$1", fid)


def _cleanup_storage(org, ws):
    shutil.rmtree(storage.files_root() / str(org) / str(ws), ignore_errors=True)


@pytest_asyncio.fixture
async def fresh_engine():
    from database.connection import dispose_engine
    await dispose_engine(); yield; await dispose_engine()


@pytest.fixture(autouse=True)
def _enable_async(monkeypatch):
    monkeypatch.setenv("BEN_DOC_PROCESSING_ENABLED", "on")  # exercise the async path


_TEXT_PDF = ["Structured alpha page one long text " * 20, "Structured beta page two text " * 20]


# ---- 1,2,19: drain runs structured pipeline (not process_file), explicit tenant ---
@pytest.mark.asyncio
async def test_drain_invokes_structured_not_process_file(fresh_engine, monkeypatch):
    import services.workspace_files.drain as drain_mod
    real = drain_mod.run_structured_extraction
    calls = []

    async def spy(o, w, f):
        calls.append((o, w, f)); return await real(o, w, f)
    monkeypatch.setattr("services.workspace_files.drain.run_structured_extraction", spy)

    def process_file_must_not_run(**k):
        raise AssertionError("legacy process_file must not be called by the async job")
    monkeypatch.setattr("services.workspace_files.service.process_file", process_file_must_not_run)

    conn = await _open(); org = uuid.uuid4(); ws = await _mk_workspace(conn, org)
    try:
        p = await _upload(org, ws, "d.pdf", "application/pdf", make_pdf(_TEXT_PDF))
        fid = uuid.UUID(p["id"])
        summary = await drain_document_processing_jobs(worker_id="t", limit=10)
        assert summary["succeeded"] >= 1
        assert len(calls) == 1 and calls[0] == (org, ws, fid)  # explicit tenant context
    finally:
        await conn.execute("DELETE FROM ben.projects WHERE id=$1", ws); await conn.close(); _cleanup_storage(org, ws)


# ---- 3,4,5,9,10,12: complete -> pages+chunks, lifecycle, legacy projection, chat ---
@pytest.mark.asyncio
async def test_complete_creates_pages_chunks_and_legacy_projection(fresh_engine):
    conn = await _open(); org = uuid.uuid4(); ws = await _mk_workspace(conn, org)
    try:
        p = await _upload(org, ws, "c.pdf", "application/pdf", make_pdf(_TEXT_PDF))
        fid = uuid.UUID(p["id"])
        await drain_document_processing_jobs(worker_id="t", limit=10)
        f = await _file(conn, fid)
        assert f["extraction_status"] == "complete" and f["index_status"] == "indexed"
        assert f["page_count"] == 2
        assert await _pages(conn, fid) == 2 and await _chunks(conn, fid) >= 2
        # Legacy compatibility projection derived from the structured result.
        assert f["status"] == "ready" and f["extracted_text"] and "Structured alpha" in f["extracted_text"]
        assert (await _job(conn, fid))["status"] == "succeeded"
        # Chat retrieval still sees it (legacy path).
        ctx = await file_service.load_ready_files_context(org, ws, max_chars=100000)
        assert ctx.count == 1 and "Structured alpha" in ctx.block
    finally:
        await conn.execute("DELETE FROM ben.projects WHERE id=$1", ws); await conn.close(); _cleanup_storage(org, ws)


# ---- 6: partial lifecycle (resource-limited) ----
@pytest.mark.asyncio
async def test_partial_lifecycle_resource_limited(fresh_engine, monkeypatch):
    monkeypatch.setattr("services.workspace_files.extraction_pipeline.MAX_EXTRACT_PAGES", 1)
    conn = await _open(); org = uuid.uuid4(); ws = await _mk_workspace(conn, org)
    try:
        p = await _upload(org, ws, "big.pdf", "application/pdf",
                          make_pdf(["p1 text long " * 10, "p2 text", "p3 text"]))
        fid = uuid.UUID(p["id"])
        await drain_document_processing_jobs(worker_id="t", limit=10)
        f = await _file(conn, fid)
        assert f["extraction_status"] == "partial" and f["index_status"] == "indexed"
        assert f["status"] == "ready"  # partial still has usable text -> chat-compatible
        skipped = await conn.fetchval(
            "SELECT count(*) FROM ben.workspace_file_pages WHERE file_id=$1 AND extraction_status='skipped'", fid)
        assert skipped == 2 and (await _job(conn, fid))["status"] == "succeeded"
    finally:
        await conn.execute("DELETE FROM ben.projects WHERE id=$1", ws); await conn.close(); _cleanup_storage(org, ws)


# ---- 7,8,18: no-usable-text / needs_ocr-only is truthful + terminal (no retry storm) ----
@pytest.mark.asyncio
async def test_needs_ocr_only_is_failed_and_terminal(fresh_engine):
    conn = await _open(); org = uuid.uuid4(); ws = await _mk_workspace(conn, org)
    try:
        p = await _upload(org, ws, "scan.png", "image/png", b"\x89PNG\r\n\x1a\n binary image")
        fid = uuid.UUID(p["id"])
        await drain_document_processing_jobs(worker_id="t", limit=10)
        f = await _file(conn, fid)
        assert f["extraction_status"] == "failed" and f["index_status"] == "not_indexed"
        assert f["status"] == "failed" and f["extracted_text"] is None  # never fabricate text
        assert await _chunks(conn, fid) == 0
        job = await _job(conn, fid)
        assert job["status"] == "failed" and job["attempts"] == 1  # terminal, not retried
        # needs_ocr page is represented truthfully.
        assert await conn.fetchval(
            "SELECT count(*) FROM ben.workspace_file_pages WHERE file_id=$1 AND extraction_status='needs_ocr'", fid) == 1
    finally:
        await conn.execute("DELETE FROM ben.projects WHERE id=$1", ws); await conn.close(); _cleanup_storage(org, ws)


@pytest.mark.asyncio
async def test_corrupt_pdf_is_determinate_failed_not_retried(fresh_engine):
    conn = await _open(); org = uuid.uuid4(); ws = await _mk_workspace(conn, org)
    try:
        p = await _upload(org, ws, "broken.pdf", "application/pdf", b"%%% not a real pdf %%%")
        fid = uuid.UUID(p["id"])
        await drain_document_processing_jobs(worker_id="t", limit=10)
        f = await _file(conn, fid); job = await _job(conn, fid)
        assert f["extraction_status"] == "failed" and f["status"] == "failed"
        assert job["status"] == "failed" and job["attempts"] == 1  # no retry storm
    finally:
        await conn.execute("DELETE FROM ben.projects WHERE id=$1", ws); await conn.close(); _cleanup_storage(org, ws)


# ---- 11: single parse (no second parsing pass) ----
@pytest.mark.asyncio
async def test_single_parse_pass(fresh_engine, monkeypatch):
    import services.workspace_files.extraction_pipeline as ep
    real_resolve = ep.resolve_parser
    counter = {"parses": 0}

    def counting_resolve(media_type, filename):
        parser = real_resolve(media_type, filename)
        orig_parse = parser.parse

        def wrapped(*a, **k):
            counter["parses"] += 1; return orig_parse(*a, **k)
        parser.parse = wrapped
        return parser
    monkeypatch.setattr("services.workspace_files.extraction_pipeline.resolve_parser", counting_resolve)

    conn = await _open(); org = uuid.uuid4(); ws = await _mk_workspace(conn, org)
    try:
        await _upload(org, ws, "s.pdf", "application/pdf", make_pdf(_TEXT_PDF))
        await drain_document_processing_jobs(worker_id="t", limit=10)
        assert counter["parses"] == 1  # exactly one parse; legacy text derived from it
    finally:
        await conn.execute("DELETE FROM ben.projects WHERE id=$1", ws); await conn.close(); _cleanup_storage(org, ws)


# ---- 13,14,15: idempotent re-run creates no duplicate pages/chunks ----
@pytest.mark.asyncio
async def test_rerun_idempotent_no_duplicate_pages_or_chunks(fresh_engine):
    from services.workspace_files.extraction_pipeline import run_structured_extraction
    conn = await _open(); org = uuid.uuid4(); ws = await _mk_workspace(conn, org)
    try:
        p = await _upload(org, ws, "i.pdf", "application/pdf", make_pdf(_TEXT_PDF))
        fid = uuid.UUID(p["id"])
        await run_structured_extraction(org, ws, fid)
        pages1, chunks1 = await _pages(conn, fid), await _chunks(conn, fid)
        await run_structured_extraction(org, ws, fid)  # re-run (e.g., crash recovery)
        assert await _pages(conn, fid) == pages1 and await _chunks(conn, fid) == chunks1
    finally:
        await conn.execute("DELETE FROM ben.projects WHERE id=$1", ws); await conn.close(); _cleanup_storage(org, ws)


# ---- 16: persistence failure must not mark job succeeded ----
@pytest.mark.asyncio
async def test_persistence_failure_not_succeeded(fresh_engine, monkeypatch):
    monkeypatch.setattr(
        "services.workspace_files.extraction_pipeline.chunk_structured_document",
        lambda doc, **_: [Chunk(1, 0, 0, None, 0)])  # NOT NULL text -> persist error
    conn = await _open(); org = uuid.uuid4(); ws = await _mk_workspace(conn, org)
    try:
        p = await _upload(org, ws, "pf.pdf", "application/pdf", make_pdf(_TEXT_PDF))
        fid = uuid.UUID(p["id"])
        await drain_document_processing_jobs(worker_id="t", limit=10)
        job = await _job(conn, fid); f = await _file(conn, fid)
        assert job["status"] != "succeeded"  # requeued or failed, never succeeded
        assert f["index_status"] != "indexed"
        assert await _chunks(conn, fid) == 0 and await _pages(conn, fid) == 0  # atomic rollback
    finally:
        await conn.execute("DELETE FROM ben.projects WHERE id=$1", ws); await conn.close(); _cleanup_storage(org, ws)


# ---- 17: transient failure requeues ----
@pytest.mark.asyncio
async def test_transient_failure_requeues(fresh_engine, monkeypatch):
    async def raise_transient(*a, **k):
        raise RuntimeError("transient blip")
    monkeypatch.setattr("services.workspace_files.drain.run_structured_extraction", raise_transient)
    conn = await _open(); org = uuid.uuid4(); ws = await _mk_workspace(conn, org)
    try:
        p = await _upload(org, ws, "t.pdf", "application/pdf", make_pdf(_TEXT_PDF))
        fid = uuid.UUID(p["id"])
        s = await drain_document_processing_jobs(worker_id="t", limit=10)
        assert s["requeued"] >= 1
        job = await _job(conn, fid)
        assert job["status"] == "queued" and job["attempts"] == 1
    finally:
        await conn.execute("DELETE FROM ben.projects WHERE id=$1", ws); await conn.close(); _cleanup_storage(org, ws)


# ---- 20: other-org isolation of persisted rows ----
@pytest.mark.asyncio
async def test_other_org_isolation(fresh_engine):
    conn = await _open()
    orgA, orgB = uuid.uuid4(), uuid.uuid4()
    wsA = await _mk_workspace(conn, orgA); wsB = await _mk_workspace(conn, orgB)
    try:
        pA = await _upload(orgA, wsA, "a.pdf", "application/pdf", make_pdf(["ALPHA only text " * 10]))
        fA = uuid.UUID(pA["id"])
        await drain_document_processing_jobs(worker_id="t", limit=10)
        rows = await conn.fetch("SELECT org_id FROM ben.workspace_file_chunks WHERE file_id=$1", fA)
        assert rows and all(r["org_id"] == orgA for r in rows)
        assert await conn.fetchval(
            "SELECT count(*) FROM ben.workspace_file_chunks WHERE org_id=$1 AND workspace_id=$2", orgB, wsA) == 0
    finally:
        await conn.execute("DELETE FROM ben.projects WHERE id=$1", wsA)
        await conn.execute("DELETE FROM ben.projects WHERE id=$1", wsB)
        await conn.close(); _cleanup_storage(orgA, wsA); _cleanup_storage(orgB, wsB)


# ---- 24,25: flag ON only enqueues (no parse in request); historical files untouched ----
@pytest.mark.asyncio
async def test_flag_on_defers_parse_and_leaves_others_untouched(fresh_engine):
    conn = await _open(); org = uuid.uuid4(); ws = await _mk_workspace(conn, org)
    try:
        # A pre-existing file with no job (historical), inserted directly.
        histn = "hist.pdf"
        hist_key = storage.storage_key_for(org, ws, uuid.uuid4(), histn)
        hist_id = uuid.uuid4()
        await conn.execute(
            "INSERT INTO ben.workspace_files (id,org_id,workspace_id,project_id,original_filename,display_name,"
            "media_type,byte_size,checksum,storage_key,status) VALUES ($1,$2,$3,$3,$4,$4,'application/pdf',0,'x',$5,'uploaded')",
            hist_id, org, ws, histn, hist_key)
        # New async upload: only enqueues; no parsing in the request.
        p = await _upload(org, ws, "new.pdf", "application/pdf", make_pdf(_TEXT_PDF))
        fid = uuid.UUID(p["id"])
        assert p["status"] == "queued"
        assert await _pages(conn, fid) == 0 and await _chunks(conn, fid) == 0  # not parsed yet
        await drain_document_processing_jobs(worker_id="t", limit=10)
        # Historical file untouched (no auto-backfill).
        h = await _file(conn, hist_id)
        assert h["status"] == "uploaded" and h["extraction_status"] == "pending"
        assert await _pages(conn, hist_id) == 0 and await _chunks(conn, hist_id) == 0
    finally:
        await conn.execute("DELETE FROM ben.projects WHERE id=$1", ws); await conn.close(); _cleanup_storage(org, ws)


# ---- 21,22: no provider egress / no document content in async executor source ----
def test_no_provider_calls_or_content_logging_in_async_path():
    import pathlib
    for path in ("services/workspace_files/drain.py", "services/workspace_files/extraction_pipeline.py"):
        src = pathlib.Path(path).read_text()
        for forbidden in ("import httpx", "openai", "anthropic", "genai", "google.generativeai", "requests."):
            assert forbidden not in src, f"{path} must not reference {forbidden}"
    # Drain observability whitelist must not include raw document text fields.
    drain_src = pathlib.Path("services/workspace_files/drain.py").read_text()
    assert "extracted_text" not in drain_src and '"text"' not in drain_src


# ---- 23: flag OFF still synchronous ----
@pytest.mark.asyncio
async def test_flag_off_still_synchronous(fresh_engine, monkeypatch):
    monkeypatch.setenv("BEN_DOC_PROCESSING_ENABLED", "off")
    conn = await _open(); org = uuid.uuid4(); ws = await _mk_workspace(conn, org)
    try:
        p = await _upload(org, ws, "sync.txt", "text/plain", b"legacy sync body")
        fid = uuid.UUID(p["id"])
        assert p["status"] == "ready"  # synchronous
        assert await conn.fetchval(
            "SELECT count(*) FROM ben.document_processing_jobs WHERE file_id=$1", fid) == 0  # no job
    finally:
        await conn.execute("DELETE FROM ben.projects WHERE id=$1", ws); await conn.close(); _cleanup_storage(org, ws)


# ---- 7 (source guard): drain no longer calls process_file ----
def test_drain_source_uses_structured_not_process_file():
    import pathlib
    src = pathlib.Path("services/workspace_files/drain.py").read_text()
    assert "run_structured_extraction" in src
    assert "process_file" not in src  # async executor no longer targets legacy process_file
