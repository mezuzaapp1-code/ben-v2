"""Live Postgres tests for durable file_initial_read jobs (migration 030).

Requires a disposable local Postgres with 030 applied. SKIP if unavailable.
Does not target production.
"""
from __future__ import annotations

import asyncio
import os
import pathlib
import uuid

import pytest
import pytest_asyncio

try:
    import asyncpg
except Exception:  # pragma: no cover
    asyncpg = None

from services.workspace_files.ingest_eligibility import PROTECTED_INGEST_FILE_IDS

_DSN = os.getenv("BEN_TEST_PG_DSN") or "postgresql://ben:ben@127.0.0.1:5432/ben"
_CLAIM_IR = "ben.claim_file_initial_read_jobs(text,integer,integer)"
_REAP_IR = "ben.reap_expired_file_initial_read_jobs(integer,integer,integer)"
_HIST_A = uuid.UUID("43cef794-1fff-40ae-bd3c-47d9fc121518")
_HIST_B = uuid.UUID("0bbd0dd0-cfd9-4ef4-a3b9-c1e96bef83a4")
ROOT = pathlib.Path(__file__).resolve().parents[1]


async def _open():
    if asyncpg is None:
        pytest.skip("asyncpg not installed")
    try:
        conn = await asyncpg.connect(_DSN)
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"Postgres unavailable: {exc}")
    present = await conn.fetchval("SELECT to_regprocedure($1) IS NOT NULL", _CLAIM_IR)
    if not present:
        await conn.close()
        pytest.skip("migration 030 claim_file_initial_read_jobs not applied")
    reap = await conn.fetchval("SELECT to_regprocedure($1) IS NOT NULL", _REAP_IR)
    if not reap:
        await conn.close()
        pytest.skip("migration 030 reap_expired_file_initial_read_jobs not applied")
    return conn


async def _mk_workspace(conn, org_id, name="ir-pg"):
    ws = uuid.uuid4()
    await conn.execute(
        "INSERT INTO ben.projects (id, org_id, name, status) VALUES ($1,$2,$3,'active')",
        ws, org_id, name,
    )
    return ws


async def _mk_file(conn, org_id, ws, *, status="ready", ir_status="pending", fid=None):
    fid = fid or uuid.uuid4()
    await conn.execute(
        """
        INSERT INTO ben.workspace_files
            (id, org_id, workspace_id, project_id, original_filename, display_name,
             media_type, byte_size, checksum, storage_key, status, source_chat_id,
             initial_read_status)
        VALUES ($1,$2,$3,$3,'A.pdf','A.pdf','application/pdf',0,'x',$4,$5,$6,$7)
        """,
        fid, org_id, ws, f"k/{fid}", status, str(uuid.uuid4()), ir_status,
    )
    return fid


async def _enqueue(
    conn, org, ws, fid, *, jt="file_initial_read", status="queued",
    attempts=0, max_attempts=5, runner_eligible=True, ev=1, cv=1,
    claimed=False, lease_sql=None, worker=None,
):
    jid = uuid.uuid4()
    claimed_at = "now()" if claimed else "NULL"
    lease = lease_sql if lease_sql else ("now()" if claimed else "NULL")
    worker_sql = f"'{worker}'" if worker else "NULL"
    await conn.execute(
        f"""
        INSERT INTO ben.document_processing_jobs
            (id, org_id, workspace_id, file_id, job_type, status, extraction_version,
             chunking_version, attempts, max_attempts, available_at, claimed_at,
             lease_expires_at, worker_id, runner_eligible)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10, now() - interval '1 second',
                {claimed_at}, {lease}, {worker_sql}, $11)
        """,
        jid, org, ws, fid, jt, status, ev, cv, attempts, max_attempts, runner_eligible,
    )
    return jid


async def _cleanup(conn, *workspaces):
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


def test_historical_deny_uuids_are_the_existing_quarantine_not_a_new_ir_exception():
    """030 copies 027 / ingest_eligibility denylist; IR does not add new file IDs."""
    src_030 = (ROOT / "database" / "migrations" / "versions" / "030_file_initial_read_jobs.py").read_text()
    src_027 = (ROOT / "database" / "migrations" / "versions" / "027_runner_eligible_jobs.py").read_text()
    assert str(_HIST_A) in src_027 and str(_HIST_B) in src_027
    assert str(_HIST_A) in src_030 and str(_HIST_B) in src_030
    assert PROTECTED_INGEST_FILE_IDS == frozenset({_HIST_A, _HIST_B})
    assert "Not a new IR-specific exception" in src_030


@pytest.mark.asyncio
async def test_extraction_claim_cannot_take_initial_read_jobs(fresh_engine):
    conn = await _open()
    org = uuid.uuid4()
    ws = await _mk_workspace(conn, org)
    try:
        ir_file = await _mk_file(conn, org, ws)
        ex_file = await _mk_file(conn, org, ws)
        ir_id = await _enqueue(conn, org, ws, ir_file, jt="file_initial_read", runner_eligible=True)
        ex_id = await _enqueue(conn, org, ws, ex_file, jt="file_extraction", runner_eligible=True)
        claimed = await conn.fetch(
            "SELECT * FROM ben.claim_document_processing_jobs('extract', 300, 50)"
        )
        ids = {r["job_id"] for r in claimed}
        types = {r["job_type"] for r in claimed}
        assert ir_id not in ids
        assert "file_initial_read" not in types
        assert ex_id in ids
        ir_claimed = await conn.fetch(
            "SELECT * FROM ben.claim_file_initial_read_jobs('ir', 300, 50)"
        )
        ir_ids = {r["job_id"] for r in ir_claimed}
        ir_types = {r["job_type"] for r in ir_claimed}
        assert ir_id in ir_ids
        assert ex_id not in ir_ids
        assert ir_types == {"file_initial_read"}
    finally:
        await _cleanup(conn, ws)
        await conn.close()


@pytest.mark.asyncio
async def test_skip_locked_prevents_double_claim(fresh_engine):
    conn = await _open()
    org = uuid.uuid4()
    ws = await _mk_workspace(conn, org)
    try:
        fid = await _mk_file(conn, org, ws)
        jid = await _enqueue(conn, org, ws, fid, jt="file_initial_read")

        async def _claim(worker):
            c = await asyncpg.connect(_DSN)
            try:
                return await c.fetch(
                    "SELECT * FROM ben.claim_file_initial_read_jobs($1, 300, 10)", worker
                )
            finally:
                await c.close()

        a, b = await asyncio.gather(_claim("w-a"), _claim("w-b"))
        rows = list(a) + list(b)
        assert len(rows) == 1
        assert rows[0]["job_id"] == jid
        assert rows[0]["attempts"] == 1
        status = await conn.fetchval(
            "SELECT status FROM ben.document_processing_jobs WHERE id=$1", jid
        )
        assert status == "running"
    finally:
        await _cleanup(conn, ws)
        await conn.close()


@pytest.mark.asyncio
async def test_ineligible_chat_ir_job_is_still_claimed(fresh_engine):
    conn = await _open()
    org = uuid.uuid4()
    ws = await _mk_workspace(conn, org)
    try:
        fid = await _mk_file(conn, org, ws)
        jid = await _enqueue(
            conn, org, ws, fid, jt="file_initial_read", runner_eligible=False
        )
        eligible = await conn.fetch(
            "SELECT * FROM ben.claim_document_processing_jobs_for_eligible('elig', 300, 50)"
        )
        assert jid not in {r["job_id"] for r in eligible}
        claimed = await conn.fetch(
            "SELECT * FROM ben.claim_file_initial_read_jobs('ir-inel', 300, 50)"
        )
        assert len(claimed) == 1
        assert claimed[0]["job_id"] == jid
        assert claimed[0]["attempts"] == 1
    finally:
        await _cleanup(conn, ws)
        await conn.close()


@pytest.mark.asyncio
async def test_stale_lease_is_reaped_and_reclaim_increments_attempts(fresh_engine):
    conn = await _open()
    org = uuid.uuid4()
    ws = await _mk_workspace(conn, org)
    try:
        fid = await _mk_file(conn, org, ws)
        jid = await _enqueue(
            conn, org, ws, fid, jt="file_initial_read", runner_eligible=False,
            claimed=True, lease_sql="now() - interval '301 seconds'", worker="dead",
            attempts=1, status="running",
        )
        reaped = await conn.fetch(
            "SELECT * FROM ben.reap_expired_file_initial_read_jobs(30, 3600, 100)"
        )
        assert any(r["job_id"] == jid for r in reaped)
        row = await conn.fetchrow(
            "SELECT status, attempts, worker_id, lease_expires_at "
            "FROM ben.document_processing_jobs WHERE id=$1",
            jid,
        )
        assert row["status"] == "queued"
        assert row["attempts"] == 1
        assert row["worker_id"] is None
        await conn.execute(
            "UPDATE ben.document_processing_jobs SET available_at = now() - interval '1 second' WHERE id=$1",
            jid,
        )
        claimed = await conn.fetch(
            "SELECT * FROM ben.claim_file_initial_read_jobs('ir-reclaim', 300, 50)"
        )
        assert claimed[0]["job_id"] == jid
        assert claimed[0]["attempts"] == 2
    finally:
        await _cleanup(conn, ws)
        await conn.close()


@pytest.mark.asyncio
async def test_max_attempts_reap_and_sync_marks_file_failed(fresh_engine):
    conn = await _open()
    org = uuid.uuid4()
    ws = await _mk_workspace(conn, org)
    try:
        fid = await _mk_file(conn, org, ws, ir_status="pending")
        jid = await _enqueue(
            conn, org, ws, fid, jt="file_initial_read", runner_eligible=False,
            claimed=True, lease_sql="now() - interval '301 seconds'", worker="dead",
            attempts=5, max_attempts=5, status="running",
        )
        reaped = await conn.fetch(
            "SELECT * FROM ben.reap_expired_file_initial_read_jobs(30, 3600, 100)"
        )
        match = next(r for r in reaped if r["job_id"] == jid)
        assert match["outcome"] == "failed"
        n = await conn.fetchval("SELECT ben.sync_failed_file_initial_reads()")
        assert int(n) >= 1
        status = await conn.fetchval(
            "SELECT initial_read_status FROM ben.workspace_files WHERE id=$1", fid
        )
        assert status == "failed"
        again = await conn.fetch(
            "SELECT * FROM ben.claim_file_initial_read_jobs('ir-done', 300, 50)"
        )
        assert jid not in {r["job_id"] for r in again}
    finally:
        await _cleanup(conn, ws)
        await conn.close()


@pytest.mark.asyncio
async def test_succeeded_ir_job_cannot_be_reclaimed(fresh_engine):
    conn = await _open()
    org = uuid.uuid4()
    ws = await _mk_workspace(conn, org)
    try:
        fid = await _mk_file(conn, org, ws, ir_status="complete")
        jid = await _enqueue(conn, org, ws, fid, jt="file_initial_read")
        first = await conn.fetch(
            "SELECT * FROM ben.claim_file_initial_read_jobs('ir-ok', 300, 50)"
        )
        assert first[0]["job_id"] == jid
        await conn.execute(
            """
            UPDATE ben.document_processing_jobs
               SET status='succeeded', claimed_at=NULL, lease_expires_at=NULL, worker_id=NULL
             WHERE id=$1
            """,
            jid,
        )
        second = await conn.fetch(
            "SELECT * FROM ben.claim_file_initial_read_jobs('ir-ok-2', 300, 50)"
        )
        assert jid not in {r["job_id"] for r in second}
    finally:
        await _cleanup(conn, ws)
        await conn.close()


@pytest.mark.asyncio
async def test_historical_deny_blocks_ir_claim(fresh_engine):
    conn = await _open()
    org = uuid.uuid4()
    ws = await _mk_workspace(conn, org)
    try:
        await conn.execute(
            "DELETE FROM ben.document_processing_jobs WHERE file_id = ANY($1::uuid[])",
            [_HIST_A, _HIST_B],
        )
        await conn.execute(
            "DELETE FROM ben.workspace_files WHERE id = ANY($1::uuid[])",
            [_HIST_A, _HIST_B],
        )
        await _mk_file(conn, org, ws, fid=_HIST_A)
        jid = await _enqueue(conn, org, ws, _HIST_A, jt="file_initial_read", runner_eligible=True)
        claimed = await conn.fetch(
            "SELECT * FROM ben.claim_file_initial_read_jobs('ir-hist', 300, 50)"
        )
        assert jid not in {r["job_id"] for r in claimed}
        live = await _mk_file(conn, org, ws)
        live_j = await _enqueue(conn, org, ws, live, jt="file_initial_read")
        claimed2 = await conn.fetch(
            "SELECT * FROM ben.claim_file_initial_read_jobs('ir-hist-2', 300, 50)"
        )
        ids = {r["job_id"] for r in claimed2}
        assert live_j in ids
        assert jid not in ids
    finally:
        await conn.execute(
            "DELETE FROM ben.document_processing_jobs WHERE file_id = ANY($1::uuid[])",
            [_HIST_A, _HIST_B],
        )
        await conn.execute(
            "DELETE FROM ben.workspace_files WHERE id = ANY($1::uuid[])",
            [_HIST_A, _HIST_B],
        )
        await _cleanup(conn, ws)
        await conn.close()


@pytest.mark.asyncio
async def test_security_definer_owner_and_grants(fresh_engine):
    conn = await _open()
    try:
        rows = await conn.fetch(
            """
            SELECT p.proname, p.prosecdef, pg_get_userbyid(p.proowner) AS owner
              FROM pg_proc p
              JOIN pg_namespace n ON n.oid = p.pronamespace
             WHERE n.nspname = 'ben'
               AND p.proname IN (
                    'claim_file_initial_read_jobs',
                    'claim_file_initial_read_job_for_file',
                    'reap_expired_file_initial_read_jobs',
                    'sync_failed_file_initial_reads'
               )
            """
        )
        names = {r["proname"] for r in rows}
        assert "claim_file_initial_read_jobs" in names
        assert "reap_expired_file_initial_read_jobs" in names
        assert "sync_failed_file_initial_reads" in names
        for r in rows:
            assert r["prosecdef"] is True
            assert r["owner"] == "ben_doc_processor"
        grants = await conn.fetch(
            """
            SELECT routine_name, grantee, privilege_type
              FROM information_schema.routine_privileges
             WHERE routine_schema = 'ben'
               AND routine_name = 'claim_file_initial_read_jobs'
               AND privilege_type = 'EXECUTE'
            """
        )
        grantees = {r["grantee"] for r in grants}
        assert "PUBLIC" not in grantees
        assert "ben_doc_processor" in grantees
    finally:
        await conn.close()
