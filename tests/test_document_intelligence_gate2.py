"""Gate 2 — structured extraction + deterministic chunking.

Two layers:
- Pure unit tests (no DB) for the provider-independent parser contract and the
  deterministic chunker. These always run.
- DB integration tests that drive the real pipeline (run_structured_extraction)
  end-to-end against the migrated `ben` schema, then verify via asyncpg. They
  SKIP cleanly when Postgres/asyncpg or the 023 schema is unavailable.

Covers the 18 required cases (see assertions). No retrieval, embeddings, OCR,
provider calls, or chat/frontend changes.
"""
from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio

from services.workspace_files import storage
from services.workspace_files.chunking import (
    CHUNKING_VERSION,
    Chunk,
    chunk_structured_document,
)
from services.workspace_files.document_parser import (
    EXTRACTION_VERSION,
    PdfDocumentParser,
    StructuredDocument,
    _assemble_document,
    _mk_page,
    resolve_parser,
)
from services.workspace_files.extraction_pipeline import (
    derive_lifecycle,
    run_structured_extraction,
)

try:
    import asyncpg
except Exception:  # pragma: no cover
    asyncpg = None

_DSN = os.getenv("BEN_TEST_PG_DSN") or "postgresql://ben:ben@127.0.0.1:5432/ben"


# --------------------------------------------------------------------------- #
# Synthetic, dependency-free PDF builder (pypdf-extractable text).
# --------------------------------------------------------------------------- #
def make_pdf(pages: list[str]) -> bytes:
    page_nums: list[int] = []
    content_nums: list[int] = []
    num = 4
    for _ in pages:
        page_nums.append(num); num += 1
        content_nums.append(num); num += 1
    objects: dict[int, str] = {}
    objects[1] = "<< /Type /Catalog /Pages 2 0 R >>"
    kids = " ".join(f"{p} 0 R" for p in page_nums)
    objects[2] = f"<< /Type /Pages /Count {len(pages)} /Kids [{kids}] >>"
    objects[3] = "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    for i, tx in enumerate(pages):
        po, co = page_nums[i], content_nums[i]
        objects[po] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {co} 0 R >>"
        )
        safe = tx.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = f"BT /F1 24 Tf 72 720 Td ({safe}) Tj ET" if tx else ""
        objects[co] = f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream"
    out = b"%PDF-1.4\n"
    offsets: dict[int, int] = {}
    for k in sorted(objects):
        offsets[k] = len(out)
        out += f"{k} 0 obj\n{objects[k]}\nendobj\n".encode("latin-1")
    xref = len(out)
    total = max(objects) + 1
    out += f"xref\n0 {total}\n".encode()
    out += b"0000000000 65535 f \n"
    for k in range(1, total):
        out += f"{offsets[k]:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {total} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    return out


def _doc(pages: list) -> StructuredDocument:
    """pages: list of (text|None, has_images, error) for _assemble_document."""
    return _assemble_document(
        pages, source_page_count=len(pages), parser_id="test", parser_version="0",
    )


# =========================================================================== #
# PURE UNIT — parser contract
# =========================================================================== #
def test_page_classification_all_states():
    """empty / needs_ocr / failed / extracted classification (cases 2,3,4)."""
    assert _mk_page(1, text="hello world", has_images=False, error=None).status == "extracted"
    assert _mk_page(2, text="   ", has_images=False, error=None).status == "empty"
    ocr = _mk_page(3, text="", has_images=True, error=None)
    assert ocr.status == "needs_ocr" and ocr.needs_ocr is True and ocr.failure_code == "needs_ocr"
    failed = _mk_page(4, text=None, has_images=False, error="extract_error:ValueError:boom")
    assert failed.status == "failed" and failed.failure_code == "extract_error"


def test_every_source_page_represented_exactly_once():
    doc = _doc([("a", False, None), ("", False, None), ("", True, None)])
    assert doc.source_page_count == 3
    assert [p.page_number for p in doc.pages] == [1, 2, 3]
    assert [p.status for p in doc.pages] == ["extracted", "empty", "needs_ocr"]
    assert doc.truncated is False


def test_resource_limit_skipped_pages_not_hidden():
    """Case 5: pages beyond the ceiling become explicit skipped/resource_limit."""
    doc = _assemble_document(
        [("p1", False, None), ("p2", False, None)],
        source_page_count=5, parser_id="test", parser_version="0", max_pages=2,
    )
    assert doc.truncated is True
    assert [p.page_number for p in doc.pages] == [1, 2, 3, 4, 5]
    assert [p.status for p in doc.pages[2:]] == ["skipped", "skipped", "skipped"]
    assert all(p.failure_code == "resource_limit" for p in doc.pages[2:])


def test_pdf_adapter_multipage_all_extracted(tmp_path):
    """Case 1: multi-page PDF, every page extracted, in source order."""
    pdf = tmp_path / "m.pdf"
    pdf.write_bytes(make_pdf(["Alpha transformer", "Beta gantry", "Gamma coupling"]))
    doc = PdfDocumentParser().parse(pdf, media_type="application/pdf", filename="m.pdf")
    assert doc.source_page_count == 3
    assert all(p.status == "extracted" for p in doc.pages)
    assert doc.pages[0].text.startswith("Alpha")
    assert doc.parser_id == "pypdf" and doc.extraction_version == EXTRACTION_VERSION


def test_pdf_adapter_failed_page_isolated(tmp_path, monkeypatch):
    """Case 4: a page that raises during extract becomes failed, others survive."""
    pdf = tmp_path / "f.pdf"
    pdf.write_bytes(make_pdf(["ok one", "boom two", "ok three"]))
    parser = PdfDocumentParser()
    orig = parser._page_signals
    calls = {"n": 0}

    def flaky(page):
        calls["n"] += 1
        if calls["n"] == 2:
            return None, False, "extract_error:RuntimeError:synthetic"
        return orig(page)

    monkeypatch.setattr(parser, "_page_signals", flaky)
    doc = parser.parse(pdf, media_type="application/pdf", filename="f.pdf")
    assert [p.status for p in doc.pages] == ["extracted", "failed", "extracted"]
    assert doc.pages[1].failure_code == "extract_error"


# =========================================================================== #
# PURE UNIT — deterministic chunker
# =========================================================================== #
def test_chunker_deterministic_and_indices():
    """Cases 9,10,11: reproducible output; page_chunk_index resets per page;
    document_chunk_index is a stable global sequence; non-extracted pages yield
    no chunks."""
    page1 = "A" * 3200  # -> 3 sub-chunks at 1500
    page2 = "B" * 100   # -> 1 sub-chunk
    doc = _assemble_document(
        [(page1, False, None), ("", True, None), (page2, False, None)],
        source_page_count=3, parser_id="t", parser_version="0",
    )
    a = chunk_structured_document(doc, max_chars=1500)
    b = chunk_structured_document(doc, max_chars=1500)
    assert [(c.page_number, c.page_chunk_index, c.document_chunk_index, c.char_count) for c in a] == \
           [(c.page_number, c.page_chunk_index, c.document_chunk_index, c.char_count) for c in b]
    # needs_ocr page (2) produced no chunks; page 1 -> 3, page 3 -> 1.
    assert [c.page_number for c in a] == [1, 1, 1, 3]
    assert [c.page_chunk_index for c in a] == [0, 1, 2, 0]
    assert [c.document_chunk_index for c in a] == [0, 1, 2, 3]
    assert sum(c.char_count for c in a if c.page_number == 1) == 3200


def test_chunker_empty_document_no_chunks():
    doc = _doc([("", False, None), ("", True, None)])
    assert chunk_structured_document(doc) == []


# =========================================================================== #
# PURE UNIT — lifecycle derivation (cases 6,7,8)
# =========================================================================== #
def test_derive_lifecycle_complete():
    doc = _doc([("real text one", False, None), ("", False, None)])  # extracted + empty
    chunks = chunk_structured_document(doc)
    status, truncated = derive_lifecycle(doc, len(chunks))
    assert status == "complete" and truncated is False


def test_derive_lifecycle_partial_on_problem_page():
    doc = _doc([("real text", False, None), ("", True, None)])  # extracted + needs_ocr
    status, _ = derive_lifecycle(doc, 1)
    assert status == "partial"


def test_derive_lifecycle_partial_on_truncation():
    doc = _assemble_document(
        [("real text", False, None)], source_page_count=3, parser_id="t",
        parser_version="0", max_pages=1,
    )
    status, truncated = derive_lifecycle(doc, 1)
    assert status == "partial" and truncated is True


def test_derive_lifecycle_failed_no_text():
    doc = _doc([("", True, None), ("", False, None)])  # needs_ocr + empty, no usable text
    status, _ = derive_lifecycle(doc, 0)
    assert status == "failed"


def test_no_backfill_upload_path_does_not_invoke_pipeline():
    """Case 18: the upload critical path must not auto-run Gate 2 extraction."""
    import pathlib

    src = pathlib.Path("services/workspace_files/service.py").read_text()
    assert "run_structured_extraction" not in src
    assert "extraction_pipeline" not in src


# =========================================================================== #
# DB INTEGRATION — real pipeline persistence
# =========================================================================== #
async def _open():
    if asyncpg is None:
        pytest.skip("asyncpg not installed")
    try:
        conn = await asyncpg.connect(_DSN)
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"Postgres unavailable: {exc}")
    present = await conn.fetchval("SELECT to_regclass('ben.workspace_file_chunks') IS NOT NULL")
    if not present:
        await conn.close()
        pytest.skip("Document Intelligence schema (023) not applied")
    return conn


async def _mk_workspace(conn, org_id):
    ws = uuid.uuid4()
    await conn.execute(
        "INSERT INTO ben.projects (id, org_id, name, status) VALUES ($1,$2,'di-gate2','active')",
        ws, org_id,
    )
    return ws


async def _mk_file_with_bytes(conn, org_id, ws, *, filename, media_type, data: bytes):
    fid = uuid.uuid4()
    key = storage.storage_key_for(org_id, ws, fid, filename)
    dest = storage.absolute_path_for_key(key)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    await conn.execute(
        """
        INSERT INTO ben.workspace_files
            (id, org_id, workspace_id, project_id, original_filename, display_name,
             media_type, byte_size, checksum, storage_key, status)
        VALUES ($1,$2,$3,$3,$4,$4,$5,$6,'x',$7,'ready')
        """,
        fid, org_id, ws, filename, media_type, len(data), key,
    )
    return fid, key


async def _cleanup(conn, key, *workspaces):
    try:
        storage.delete_storage(key)
    except Exception:
        pass
    for ws in workspaces:
        await conn.execute("DELETE FROM ben.projects WHERE id=$1", ws)


@pytest_asyncio.fixture
async def fresh_engine():
    """Dispose the shared async engine before/after so its connection pool binds
    to THIS test's event loop (the engine is a module-level singleton and other
    async tests may have bound pooled connections to now-closed loops)."""
    from database.connection import dispose_engine

    await dispose_engine()
    yield
    await dispose_engine()


@pytest.mark.asyncio
async def test_pipeline_complete_multipage_persists_pages_and_chunks(fresh_engine):
    """Cases 1,6: happy multi-page -> complete + indexed, per-page rows + chunks."""
    conn = await _open()
    org = uuid.uuid4()
    ws = await _mk_workspace(conn, org)
    fid, key = await _mk_file_with_bytes(
        conn, org, ws, filename="doc.pdf", media_type="application/pdf",
        data=make_pdf(["Alpha " * 400, "Beta gantry page", "Gamma coupling"]),
    )
    try:
        diag = await run_structured_extraction(org, ws, fid)
        assert diag["final_extraction_status"] == "complete"
        assert diag["final_index_status"] == "indexed"
        assert diag["source_page_count"] == 3
        f = await conn.fetchrow(
            "SELECT extraction_status, index_status, page_count, extraction_truncated, "
            "indexed_chunk_count, extraction_version, chunking_version, indexing_version, "
            "indexed_at FROM ben.workspace_files WHERE id=$1", fid,
        )
        assert f["extraction_status"] == "complete"
        assert f["index_status"] == "indexed"
        assert f["page_count"] == 3
        assert f["extraction_truncated"] is False
        assert f["indexed_at"] is not None
        pages = await conn.fetch(
            "SELECT page_number, extraction_status FROM ben.workspace_file_pages "
            "WHERE file_id=$1 ORDER BY page_number", fid,
        )
        assert [p["page_number"] for p in pages] == [1, 2, 3]
        assert all(p["extraction_status"] == "extracted" for p in pages)
        n_chunks = await conn.fetchval(
            "SELECT count(*) FROM ben.workspace_file_chunks WHERE file_id=$1", fid)
        assert n_chunks == f["indexed_chunk_count"] and n_chunks >= 3
        # Global chunk ordering is contiguous 0..n-1.
        idxs = await conn.fetch(
            "SELECT document_chunk_index FROM ben.workspace_file_chunks "
            "WHERE file_id=$1 ORDER BY document_chunk_index", fid)
        assert [r["document_chunk_index"] for r in idxs] == list(range(n_chunks))
    finally:
        await _cleanup(conn, key, ws)
        await conn.close()


@pytest.mark.asyncio
async def test_pipeline_partial_when_resource_limited(monkeypatch, fresh_engine):
    """Cases 5,7: page ceiling -> skipped rows for 81+, extraction_status=partial,
    still indexed on the readable pages (index independent of coverage)."""
    monkeypatch.setattr("services.workspace_files.extraction_pipeline.MAX_EXTRACT_PAGES", 1)
    conn = await _open()
    org = uuid.uuid4()
    ws = await _mk_workspace(conn, org)
    fid, key = await _mk_file_with_bytes(
        conn, org, ws, filename="big.pdf", media_type="application/pdf",
        data=make_pdf(["page one text", "page two text", "page three text"]),
    )
    try:
        diag = await run_structured_extraction(org, ws, fid)
        assert diag["final_extraction_status"] == "partial"
        assert diag["final_index_status"] == "indexed"
        assert diag["truncated"] is True
        f = await conn.fetchrow(
            "SELECT extraction_status, index_status, page_count, extraction_truncated "
            "FROM ben.workspace_files WHERE id=$1", fid)
        assert f["extraction_status"] == "partial"
        assert f["page_count"] == 3 and f["extraction_truncated"] is True
        rows = await conn.fetch(
            "SELECT page_number, extraction_status, failure_code FROM ben.workspace_file_pages "
            "WHERE file_id=$1 ORDER BY page_number", fid)
        assert [r["extraction_status"] for r in rows] == ["extracted", "skipped", "skipped"]
        assert rows[1]["failure_code"] == "resource_limit"
    finally:
        await _cleanup(conn, key, ws)
        await conn.close()


@pytest.mark.asyncio
async def test_pipeline_failed_when_no_usable_text_image_only(fresh_engine):
    """Cases 3,8,16: image-only file -> needs_ocr page, file extraction failed,
    not_indexed, truthful lifecycle, zero chunks."""
    conn = await _open()
    org = uuid.uuid4()
    ws = await _mk_workspace(conn, org)
    fid, key = await _mk_file_with_bytes(
        conn, org, ws, filename="scan.png", media_type="image/png", data=b"\x89PNG\r\n\x1a\n binary",
    )
    try:
        diag = await run_structured_extraction(org, ws, fid)
        assert diag["final_extraction_status"] == "failed"
        assert diag["final_index_status"] == "not_indexed"
        f = await conn.fetchrow(
            "SELECT extraction_status, index_status, indexed_at FROM ben.workspace_files WHERE id=$1", fid)
        assert f["extraction_status"] == "failed" and f["index_status"] == "not_indexed"
        assert f["indexed_at"] is None
        page = await conn.fetchrow(
            "SELECT extraction_status, needs_ocr FROM ben.workspace_file_pages WHERE file_id=$1", fid)
        assert page["extraction_status"] == "needs_ocr" and page["needs_ocr"] is True
        assert await conn.fetchval(
            "SELECT count(*) FROM ben.workspace_file_chunks WHERE file_id=$1", fid) == 0
    finally:
        await _cleanup(conn, key, ws)
        await conn.close()


@pytest.mark.asyncio
async def test_pipeline_idempotent_on_retry(fresh_engine):
    """Cases 12,13: re-running replaces cleanly (no duplicate pages/chunks, stable counts)."""
    conn = await _open()
    org = uuid.uuid4()
    ws = await _mk_workspace(conn, org)
    fid, key = await _mk_file_with_bytes(
        conn, org, ws, filename="r.pdf", media_type="application/pdf",
        data=make_pdf(["retry one longer text", "retry two"]),
    )
    try:
        d1 = await run_structured_extraction(org, ws, fid)
        p1 = await conn.fetchval("SELECT count(*) FROM ben.workspace_file_pages WHERE file_id=$1", fid)
        c1 = await conn.fetchval("SELECT count(*) FROM ben.workspace_file_chunks WHERE file_id=$1", fid)
        d2 = await run_structured_extraction(org, ws, fid)
        p2 = await conn.fetchval("SELECT count(*) FROM ben.workspace_file_pages WHERE file_id=$1", fid)
        c2 = await conn.fetchval("SELECT count(*) FROM ben.workspace_file_chunks WHERE file_id=$1", fid)
        assert p1 == p2 == 2
        assert c1 == c2 == d1["chunk_count"] == d2["chunk_count"]
    finally:
        await _cleanup(conn, key, ws)
        await conn.close()


@pytest.mark.asyncio
async def test_pipeline_chunk_persistence_failure_not_falsely_indexed(monkeypatch, fresh_engine):
    """Case 17: if chunk persistence fails, index_status must not be 'indexed'
    and no partial rows survive (single transaction rolls back)."""
    bad = [Chunk(page_number=1, page_chunk_index=0, document_chunk_index=0, text=None, char_count=0)]  # type: ignore[arg-type]
    monkeypatch.setattr(
        "services.workspace_files.extraction_pipeline.chunk_structured_document",
        lambda doc, **_: bad,
    )
    conn = await _open()
    org = uuid.uuid4()
    ws = await _mk_workspace(conn, org)
    fid, key = await _mk_file_with_bytes(
        conn, org, ws, filename="c.pdf", media_type="application/pdf",
        data=make_pdf(["will fail to index"]),
    )
    try:
        diag = await run_structured_extraction(org, ws, fid)
        assert diag.get("final_index_status") != "indexed"
        f = await conn.fetchrow(
            "SELECT extraction_status, index_status FROM ben.workspace_files WHERE id=$1", fid)
        assert f["index_status"] in ("failed", "not_indexed")
        assert f["index_status"] != "indexed"
        # Atomic: no chunks and no partially-written pages persisted.
        assert await conn.fetchval(
            "SELECT count(*) FROM ben.workspace_file_chunks WHERE file_id=$1", fid) == 0
        assert await conn.fetchval(
            "SELECT count(*) FROM ben.workspace_file_pages WHERE file_id=$1", fid) == 0
    finally:
        await _cleanup(conn, key, ws)
        await conn.close()


@pytest.mark.asyncio
async def test_pipeline_tenant_isolation_other_org_and_workspace(fresh_engine):
    """Cases 14,15: persisted pages/chunks carry correct org+workspace; queries
    scoped to another tenant see nothing."""
    conn = await _open()
    orgA, orgB = uuid.uuid4(), uuid.uuid4()
    wsA = await _mk_workspace(conn, orgA)
    wsB = await _mk_workspace(conn, orgB)
    fA, keyA = await _mk_file_with_bytes(
        conn, orgA, wsA, filename="a.pdf", media_type="application/pdf",
        data=make_pdf(["ALPHA content here"]),
    )
    fB, keyB = await _mk_file_with_bytes(
        conn, orgB, wsB, filename="b.pdf", media_type="application/pdf",
        data=make_pdf(["BRAVO content here"]),
    )
    try:
        await run_structured_extraction(orgA, wsA, fA)
        await run_structured_extraction(orgB, wsB, fB)
        a_chunks = await conn.fetch(
            "SELECT org_id, workspace_id FROM ben.workspace_file_chunks WHERE org_id=$1 AND workspace_id=$2",
            orgA, wsA)
        assert a_chunks and all(r["org_id"] == orgA and r["workspace_id"] == wsA for r in a_chunks)
        # Other-org / other-workspace scope sees none of A's rows.
        assert await conn.fetchval(
            "SELECT count(*) FROM ben.workspace_file_chunks WHERE org_id=$1 AND workspace_id=$2",
            orgB, wsA) == 0
        assert await conn.fetchval(
            "SELECT count(*) FROM ben.workspace_file_pages WHERE org_id=$1 AND workspace_id=$2",
            orgA, wsB) == 0
    finally:
        await _cleanup(conn, keyA, wsA)
        await _cleanup(conn, keyB, wsB)
        await conn.close()
