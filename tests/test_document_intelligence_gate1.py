"""Gate 1 — Document Intelligence data foundation.

Integration tests against the real Postgres schema created by migration
`023_doc_intel_foundation` (WorkspaceFile lifecycle fields, WorkspaceFilePage,
WorkspaceFileChunk). These are read/write DB tests; they SKIP cleanly when
Postgres is unavailable or the migration has not been applied, so CI without a
database stays green. Locally they run against the migrated `ben` database.

Scope: data model, constraints, generated tsvector, GIN index, cascade deletes,
and tenant/RLS isolation (including a real non-superuser RLS-enforcement probe).
No extraction, chunk generation, indexing, retrieval, or provider calls.
"""
from __future__ import annotations

import os
import uuid

import pytest

try:
    import asyncpg
except Exception:  # pragma: no cover
    asyncpg = None

pytestmark = pytest.mark.asyncio

# asyncpg uses a plain postgresql:// DSN (not the SQLAlchemy +asyncpg prefix).
_DSN = os.getenv("BEN_TEST_PG_DSN") or "postgresql://ben:ben@127.0.0.1:5432/ben"


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


async def _mk_workspace(conn, org_id: uuid.UUID) -> uuid.UUID:
    ws = uuid.uuid4()
    await conn.execute(
        "INSERT INTO ben.projects (id, org_id, name, status) VALUES ($1, $2, 'di-gate1', 'active')",
        ws,
        org_id,
    )
    return ws


async def _mk_file(conn, org_id, ws, *, extraction_status=None, index_status=None) -> uuid.UUID:
    fid = uuid.uuid4()
    await conn.execute(
        """
        INSERT INTO ben.workspace_files
            (id, org_id, workspace_id, project_id, original_filename, display_name,
             media_type, byte_size, checksum, storage_key, status,
             extraction_status, index_status)
        VALUES ($1,$2,$3,$3,'f.pdf','f.pdf','application/pdf',0,'x',$4,'ready',
                COALESCE($5,'pending'), COALESCE($6,'not_indexed'))
        """,
        fid, org_id, ws, f"k/{fid}", extraction_status, index_status,
    )
    return fid


async def _mk_page(conn, org_id, ws, fid, *, page_number, status, char_count=0,
                   needs_ocr=False, failure_code=None, failure_detail=None, version=1) -> uuid.UUID:
    pid = uuid.uuid4()
    await conn.execute(
        """
        INSERT INTO ben.workspace_file_pages
            (id, org_id, workspace_id, file_id, page_number, extraction_status,
             char_count, needs_ocr, failure_code, failure_detail, extraction_version)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
        """,
        pid, org_id, ws, fid, page_number, status, char_count, needs_ocr,
        failure_code, failure_detail, version,
    )
    return pid


async def _mk_chunk(conn, org_id, ws, fid, *, document_chunk_index, text, page_id=None,
                    page_number=None, page_chunk_index=None, extraction_version=1,
                    chunking_version=1) -> uuid.UUID:
    cid = uuid.uuid4()
    await conn.execute(
        """
        INSERT INTO ben.workspace_file_chunks
            (id, org_id, workspace_id, file_id, page_id, page_number, page_chunk_index,
             document_chunk_index, text, char_count, extraction_version, chunking_version)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
        """,
        cid, org_id, ws, fid, page_id, page_number, page_chunk_index,
        document_chunk_index, text, len(text), extraction_version, chunking_version,
    )
    return cid


async def _cleanup(conn, *workspaces):
    for ws in workspaces:
        # projects -> workspace_files -> pages/chunks all cascade.
        await conn.execute("DELETE FROM ben.projects WHERE id = $1", ws)


# --------------------------- schema / migration ---------------------------- #

async def test_di_schema_present_and_defaults():
    conn = await _open()
    try:
        # New lifecycle columns exist with safe server defaults (items 5).
        cols = await conn.fetch(
            """
            SELECT column_name, column_default FROM information_schema.columns
            WHERE table_schema='ben' AND table_name='workspace_files'
              AND column_name IN ('extraction_status','index_status','extraction_truncated',
                                  'extraction_version','indexed_at','processing_error')
            """
        )
        names = {r["column_name"] for r in cols}
        assert {"extraction_status", "index_status", "extraction_truncated"} <= names
        org = uuid.uuid4()
        ws = await _mk_workspace(conn, org)
        try:
            fid = await _mk_file(conn, org, ws)  # no explicit lifecycle -> defaults
            row = await conn.fetchrow(
                "SELECT extraction_status, index_status, extraction_truncated "
                "FROM ben.workspace_files WHERE id=$1", fid,
            )
            assert row["extraction_status"] == "pending"
            assert row["index_status"] == "not_indexed"
            assert row["extraction_truncated"] is False
        finally:
            await _cleanup(conn, ws)
    finally:
        await conn.close()


# --------------------------- file lifecycle -------------------------------- #

async def test_extraction_and_index_status_independent():
    """partial extraction + indexed is representable; coverage_complete (derived
    = extraction_status=='complete') is independent of index success (items 7,8,9)."""
    conn = await _open()
    org = uuid.uuid4()
    ws = await _mk_workspace(conn, org)
    try:
        fid = await _mk_file(conn, org, ws, extraction_status="partial", index_status="indexed")
        row = await conn.fetchrow(
            "SELECT extraction_status, index_status FROM ben.workspace_files WHERE id=$1", fid
        )
        assert row["extraction_status"] == "partial"
        assert row["index_status"] == "indexed"
        coverage_complete = row["extraction_status"] == "complete"
        assert coverage_complete is False  # partial extraction, still indexed
    finally:
        await _cleanup(conn, ws)
        await conn.close()


async def test_invalid_lifecycle_values_rejected():
    conn = await _open()
    org = uuid.uuid4()
    ws = await _mk_workspace(conn, org)
    try:
        with pytest.raises(asyncpg.PostgresError):
            await _mk_file(conn, org, ws, extraction_status="banana")
        with pytest.raises(asyncpg.PostgresError):
            await _mk_file(conn, org, ws, index_status="whatever")
    finally:
        await _cleanup(conn, ws)
        await conn.close()


# --------------------------- page model ------------------------------------ #

async def test_all_page_states_representable_with_reason():
    """extracted / empty / needs_ocr / failed / skipped, with machine-readable
    failure reason (items 12-16)."""
    conn = await _open()
    org = uuid.uuid4()
    ws = await _mk_workspace(conn, org)
    try:
        fid = await _mk_file(conn, org, ws)
        await _mk_page(conn, org, ws, fid, page_number=1, status="extracted", char_count=1200)
        await _mk_page(conn, org, ws, fid, page_number=19, status="empty")
        await _mk_page(conn, org, ws, fid, page_number=7, status="needs_ocr", needs_ocr=True,
                       failure_code="needs_ocr")
        await _mk_page(conn, org, ws, fid, page_number=14, status="failed",
                       failure_code="extraction_error", failure_detail="parser raised")
        await _mk_page(conn, org, ws, fid, page_number=81, status="skipped",
                       failure_code="resource_limit")
        rows = await conn.fetch(
            "SELECT page_number, extraction_status, needs_ocr, failure_code "
            "FROM ben.workspace_file_pages WHERE file_id=$1 ORDER BY page_number", fid,
        )
        by_page = {r["page_number"]: r for r in rows}
        assert by_page[1]["extraction_status"] == "extracted"
        assert by_page[19]["extraction_status"] == "empty"
        assert by_page[7]["extraction_status"] == "needs_ocr" and by_page[7]["needs_ocr"] is True
        assert by_page[14]["extraction_status"] == "failed" and by_page[14]["failure_code"] == "extraction_error"
        assert by_page[81]["extraction_status"] == "skipped" and by_page[81]["failure_code"] == "resource_limit"
        # Derived coverage counts (no aggregate columns persisted).
        pages_extracted = await conn.fetchval(
            "SELECT count(*) FROM ben.workspace_file_pages WHERE file_id=$1 AND extraction_status='extracted'", fid
        )
        assert pages_extracted == 1
    finally:
        await _cleanup(conn, ws)
        await conn.close()


async def test_invalid_page_status_rejected():
    conn = await _open()
    org = uuid.uuid4()
    ws = await _mk_workspace(conn, org)
    try:
        fid = await _mk_file(conn, org, ws)
        with pytest.raises(asyncpg.PostgresError):
            await _mk_page(conn, org, ws, fid, page_number=1, status="bogus")
    finally:
        await _cleanup(conn, ws)
        await conn.close()


async def test_page_uniqueness_per_file_version_page():
    conn = await _open()
    org = uuid.uuid4()
    ws = await _mk_workspace(conn, org)
    try:
        fid = await _mk_file(conn, org, ws)
        await _mk_page(conn, org, ws, fid, page_number=5, status="extracted", version=1)
        with pytest.raises(asyncpg.PostgresError):  # duplicate (file, version, page)
            await _mk_page(conn, org, ws, fid, page_number=5, status="extracted", version=1)
        # Different extraction_version is allowed (re-extraction).
        await _mk_page(conn, org, ws, fid, page_number=5, status="extracted", version=2)
    finally:
        await _cleanup(conn, ws)
        await conn.close()


async def test_page_cascade_delete_from_file():
    conn = await _open()
    org = uuid.uuid4()
    ws = await _mk_workspace(conn, org)
    try:
        fid = await _mk_file(conn, org, ws)
        await _mk_page(conn, org, ws, fid, page_number=1, status="extracted")
        await conn.execute("DELETE FROM ben.workspace_files WHERE id=$1", fid)
        remaining = await conn.fetchval(
            "SELECT count(*) FROM ben.workspace_file_pages WHERE file_id=$1", fid
        )
        assert remaining == 0
    finally:
        await _cleanup(conn, ws)
        await conn.close()


# --------------------------- chunk model ----------------------------------- #

async def test_chunk_insert_tsvector_and_indices():
    conn = await _open()
    org = uuid.uuid4()
    ws = await _mk_workspace(conn, org)
    try:
        fid = await _mk_file(conn, org, ws)
        pid = await _mk_page(conn, org, ws, fid, page_number=18, status="extracted", char_count=40)
        cid = await _mk_chunk(conn, org, ws, fid, document_chunk_index=47, text="transformer platform layout",
                              page_id=pid, page_number=18, page_chunk_index=2)
        row = await conn.fetchrow(
            "SELECT page_number, page_chunk_index, document_chunk_index, "
            "text_tsv IS NOT NULL AS has_tsv, "
            "(text_tsv @@ plainto_tsquery('simple','transformer')) AS matches "
            "FROM ben.workspace_file_chunks WHERE id=$1", cid,
        )
        assert row["page_number"] == 18
        assert row["page_chunk_index"] == 2
        assert row["document_chunk_index"] == 47
        assert row["has_tsv"] is True
        assert row["matches"] is True
        # GIN index exists (item 24).
        gin = await conn.fetchval(
            "SELECT count(*) FROM pg_indexes WHERE schemaname='ben' "
            "AND tablename='workspace_file_chunks' AND indexdef ILIKE '%gin%'"
        )
        assert gin >= 1
    finally:
        await _cleanup(conn, ws)
        await conn.close()


async def test_chunk_duplicate_document_index_rejected():
    conn = await _open()
    org = uuid.uuid4()
    ws = await _mk_workspace(conn, org)
    try:
        fid = await _mk_file(conn, org, ws)
        await _mk_chunk(conn, org, ws, fid, document_chunk_index=0, text="a", chunking_version=1)
        with pytest.raises(asyncpg.PostgresError):  # duplicate (file, chunking_version, doc idx)
            await _mk_chunk(conn, org, ws, fid, document_chunk_index=0, text="b", chunking_version=1)
        # Different chunking_version allowed (safe re-index).
        await _mk_chunk(conn, org, ws, fid, document_chunk_index=0, text="c", chunking_version=2)
    finally:
        await _cleanup(conn, ws)
        await conn.close()


async def test_multiple_subchunks_per_page():
    conn = await _open()
    org = uuid.uuid4()
    ws = await _mk_workspace(conn, org)
    try:
        fid = await _mk_file(conn, org, ws)
        pid = await _mk_page(conn, org, ws, fid, page_number=3, status="extracted")
        await _mk_chunk(conn, org, ws, fid, document_chunk_index=10, text="part one",
                        page_id=pid, page_number=3, page_chunk_index=0)
        await _mk_chunk(conn, org, ws, fid, document_chunk_index=11, text="part two",
                        page_id=pid, page_number=3, page_chunk_index=1)
        n = await conn.fetchval(
            "SELECT count(*) FROM ben.workspace_file_chunks WHERE page_id=$1", pid
        )
        assert n == 2
    finally:
        await _cleanup(conn, ws)
        await conn.close()


async def test_chunk_cascade_delete_from_file_and_page():
    conn = await _open()
    org = uuid.uuid4()
    ws = await _mk_workspace(conn, org)
    try:
        fid = await _mk_file(conn, org, ws)
        pid = await _mk_page(conn, org, ws, fid, page_number=1, status="extracted")
        await _mk_chunk(conn, org, ws, fid, document_chunk_index=0, text="x", page_id=pid,
                        page_number=1, page_chunk_index=0)
        # Deleting the page cascades to its chunks.
        await conn.execute("DELETE FROM ben.workspace_file_pages WHERE id=$1", pid)
        assert await conn.fetchval(
            "SELECT count(*) FROM ben.workspace_file_chunks WHERE page_id=$1", pid) == 0
        # Deleting the file cascades any remaining chunks.
        await _mk_chunk(conn, org, ws, fid, document_chunk_index=1, text="y")
        await conn.execute("DELETE FROM ben.workspace_files WHERE id=$1", fid)
        assert await conn.fetchval(
            "SELECT count(*) FROM ben.workspace_file_chunks WHERE file_id=$1", fid) == 0
    finally:
        await _cleanup(conn, ws)
        await conn.close()


# --------------------------- isolation / RLS ------------------------------- #

async def test_rls_structural_enabled_forced_with_policies():
    conn = await _open()
    try:
        rows = await conn.fetch(
            "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE relname IN ('workspace_file_pages','workspace_file_chunks')"
        )
        assert len(rows) == 2
        for r in rows:
            assert r["relrowsecurity"] is True
            assert r["relforcerowsecurity"] is True
        policies = await conn.fetchval(
            "SELECT count(*) FROM pg_policies WHERE tablename IN "
            "('workspace_file_pages','workspace_file_chunks')"
        )
        assert policies == 2
    finally:
        await conn.close()


async def test_explicit_tenant_filter_isolation():
    """Service-layer contract: queries must filter org_id + workspace_id, never
    file_id alone (items 28)."""
    conn = await _open()
    orgA, orgB = uuid.uuid4(), uuid.uuid4()
    wsA = await _mk_workspace(conn, orgA)
    wsB = await _mk_workspace(conn, orgB)
    try:
        fA = await _mk_file(conn, orgA, wsA)
        fB = await _mk_file(conn, orgB, wsB)
        await _mk_chunk(conn, orgA, wsA, fA, document_chunk_index=0, text="ALPHA-SECRET")
        await _mk_chunk(conn, orgB, wsB, fB, document_chunk_index=0, text="BRAVO-SECRET")
        rows = await conn.fetch(
            "SELECT text FROM ben.workspace_file_chunks WHERE org_id=$1 AND workspace_id=$2",
            orgA, wsA,
        )
        texts = {r["text"] for r in rows}
        assert "ALPHA-SECRET" in texts and "BRAVO-SECRET" not in texts
    finally:
        await _cleanup(conn, wsA, wsB)
        await conn.close()


async def test_rls_enforced_for_nonsuperuser_cross_org_impossible():
    """Real RLS proof with a non-superuser role: org-A context sees only org-A
    rows, and inserting an org-B row violates WITH CHECK (items 26,27,29)."""
    conn = await _open()
    orgA, orgB = uuid.uuid4(), uuid.uuid4()
    wsA = await _mk_workspace(conn, orgA)
    wsB = await _mk_workspace(conn, orgB)
    try:
        fA = await _mk_file(conn, orgA, wsA)
        fB = await _mk_file(conn, orgB, wsB)
        await _mk_chunk(conn, orgA, wsA, fA, document_chunk_index=0, text="ALPHA")
        await _mk_chunk(conn, orgB, wsB, fB, document_chunk_index=0, text="BRAVO")
        await conn.execute(
            "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='di_rls_probe') "
            "THEN CREATE ROLE di_rls_probe NOLOGIN; END IF; END $$;"
        )
        await conn.execute("GRANT USAGE ON SCHEMA ben TO di_rls_probe")
        await conn.execute("GRANT SELECT, INSERT ON ben.workspace_file_chunks TO di_rls_probe")

        async with conn.transaction():
            await conn.execute("SET LOCAL ROLE di_rls_probe")
            await conn.execute("SELECT set_config('app.current_org_id', $1, true)", str(orgA))
            visible = await conn.fetch("SELECT org_id, text FROM ben.workspace_file_chunks")
            assert visible, "org-A context should see its own rows"
            assert all(r["org_id"] == orgA for r in visible)
            assert all(r["text"] != "BRAVO" for r in visible)
            with pytest.raises(asyncpg.PostgresError):
                # Inserting an org-B row under org-A context violates WITH CHECK.
                await conn.execute(
                    "INSERT INTO ben.workspace_file_chunks "
                    "(org_id, workspace_id, file_id, document_chunk_index, text, char_count, "
                    " extraction_version, chunking_version) "
                    "VALUES ($1,$2,$3,$4,$5,$6,$7,$8)",
                    orgB, wsB, fB, 99, "X", 1, 1, 1,
                )
    finally:
        await _cleanup(conn, wsA, wsB)
        await conn.close()
