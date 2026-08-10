"""Gate 3A — durable orchestration substrate (document_processing_jobs).

Covers enqueue/dedup/tenant-integrity, atomic claim (FOR UPDATE SKIP LOCKED),
lease/reaper recovery, retry scheduling, state-machine invariants, RLS isolation
+ the SECURITY-DEFINER cross-tenant privilege boundary, and proofs that Gate 3A
is inert (no extraction, no WorkspaceFile/page/chunk mutation, no upload wiring).

DB tests SKIP cleanly when Postgres/asyncpg or migration 024 is unavailable.
RLS-enforcement tests run under SET LOCAL ROLE to non-superuser roles (local `ben`
is superuser and would otherwise bypass RLS).
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest
import pytest_asyncio

try:
    import asyncpg
except Exception:  # pragma: no cover
    asyncpg = None

_DSN = os.getenv("BEN_TEST_PG_DSN") or "postgresql://ben:ben@127.0.0.1:5432/ben"
_PRODUCT_ROLE = "dpj_product_probe"

_ALLOWED_CLAIM_KEYS = {
    "job_id", "org_id", "workspace_id", "file_id", "job_type",
    "attempts", "extraction_version", "chunking_version", "lease_expires_at",
}
_CONTENT_LIKE = {"text", "content", "bytes", "extracted_text", "payload", "data"}


async def _open():
    if asyncpg is None:
        pytest.skip("asyncpg not installed")
    try:
        conn = await asyncpg.connect(_DSN)
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"Postgres unavailable: {exc}")
    present = await conn.fetchval("SELECT to_regclass('ben.document_processing_jobs') IS NOT NULL")
    if not present:
        await conn.close()
        pytest.skip("Gate 3A schema (024) not applied")
    return conn


async def _mk_workspace(conn, org_id):
    ws = uuid.uuid4()
    await conn.execute(
        "INSERT INTO ben.projects (id, org_id, name, status) VALUES ($1,$2,'dpj-g3a','active')",
        ws, org_id,
    )
    return ws


async def _mk_file(conn, org_id, ws) -> uuid.UUID:
    fid = uuid.uuid4()
    await conn.execute(
        """
        INSERT INTO ben.workspace_files
            (id, org_id, workspace_id, project_id, original_filename, display_name,
             media_type, byte_size, checksum, storage_key, status)
        VALUES ($1,$2,$3,$3,'f.pdf','f.pdf','application/pdf',0,'x',$4,'uploaded')
        """,
        fid, org_id, ws, f"k/{fid}",
    )
    return fid


async def _enqueue_sql(conn, org, ws, fid, *, ev=1, cv=1, jt="structured_extraction",
                       status="queued", available_at_sql="now()", attempts=0, max_attempts=5,
                       claimed=False, lease_sql=None, worker=None) -> uuid.UUID:
    """Insert a job row directly (test fixture; bypasses the wrapper)."""
    jid = uuid.uuid4()
    claimed_at = "now()" if claimed else "NULL"
    lease = lease_sql if lease_sql else ("now()" if claimed else "NULL")
    worker_sql = f"'{worker}'" if worker else "NULL"
    await conn.execute(
        f"""
        INSERT INTO ben.document_processing_jobs
            (id, org_id, workspace_id, file_id, job_type, status, extraction_version,
             chunking_version, attempts, max_attempts, available_at, claimed_at,
             lease_expires_at, worker_id)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,{available_at_sql},{claimed_at},{lease},{worker_sql})
        """,
        jid, org, ws, fid, jt, status, ev, cv, attempts, max_attempts,
    )
    return jid


async def _ensure_product_role(conn):
    await conn.execute(
        f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='{_PRODUCT_ROLE}') "
        f"THEN CREATE ROLE {_PRODUCT_ROLE} NOLOGIN; END IF; END $$;"
    )
    await conn.execute(f"GRANT USAGE ON SCHEMA ben TO {_PRODUCT_ROLE}")
    await conn.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ben.document_processing_jobs TO {_PRODUCT_ROLE}"
    )


async def _cleanup(conn, *workspaces):
    for ws in workspaces:
        await conn.execute("DELETE FROM ben.projects WHERE id=$1", ws)


@pytest_asyncio.fixture
async def fresh_engine():
    from database.connection import dispose_engine
    await dispose_engine()
    yield
    await dispose_engine()


# =========================================================================== #
# Enqueue + dedup + tenant integrity (wrapper + DB)
# =========================================================================== #
@pytest.mark.asyncio
async def test_enqueue_valid_job(fresh_engine):
    from services.workspace_files.job_queue import enqueue_document_processing_job
    conn = await _open()
    org = uuid.uuid4(); ws = await _mk_workspace(conn, org); fid = await _mk_file(conn, org, ws)
    try:
        res = await enqueue_document_processing_job(org, ws, fid)
        assert res["created"] is True and res["status"] == "queued"
        row = await conn.fetchrow(
            "SELECT org_id, workspace_id, file_id, status, attempts FROM ben.document_processing_jobs WHERE id=$1",
            uuid.UUID(res["id"]))
        assert row["org_id"] == org and row["workspace_id"] == ws and row["file_id"] == fid
        assert row["status"] == "queued" and row["attempts"] == 0
    finally:
        await _cleanup(conn, ws); await conn.close()


@pytest.mark.asyncio
async def test_duplicate_enqueue_idempotent(fresh_engine):
    from services.workspace_files.job_queue import enqueue_document_processing_job
    conn = await _open()
    org = uuid.uuid4(); ws = await _mk_workspace(conn, org); fid = await _mk_file(conn, org, ws)
    try:
        a = await enqueue_document_processing_job(org, ws, fid)
        b = await enqueue_document_processing_job(org, ws, fid)
        assert a["created"] is True and b["created"] is False
        assert a["id"] == b["id"]
        assert await conn.fetchval(
            "SELECT count(*) FROM ben.document_processing_jobs WHERE file_id=$1", fid) == 1
    finally:
        await _cleanup(conn, ws); await conn.close()


@pytest.mark.asyncio
async def test_cross_org_enqueue_rejected(fresh_engine):
    from services.workspace_files.job_queue import enqueue_document_processing_job, TenantOwnershipError
    conn = await _open()
    orgA, orgB = uuid.uuid4(), uuid.uuid4()
    wsA = await _mk_workspace(conn, orgA); wsB = await _mk_workspace(conn, orgB)
    fB = await _mk_file(conn, orgB, wsB)
    try:
        with pytest.raises(TenantOwnershipError):
            await enqueue_document_processing_job(orgA, wsA, fB)  # file belongs to orgB
        # DB composite FK also rejects a forged triple (context-independent proof).
        with pytest.raises(asyncpg.PostgresError):
            await _enqueue_sql(conn, orgA, wsA, fB)  # (fB, orgA, wsA) is not a real tuple
    finally:
        await _cleanup(conn, wsA, wsB); await conn.close()


@pytest.mark.asyncio
async def test_mismatched_workspace_file_rejected(fresh_engine):
    from services.workspace_files.job_queue import enqueue_document_processing_job, TenantOwnershipError
    conn = await _open()
    org = uuid.uuid4()
    wsA = await _mk_workspace(conn, org); wsB = await _mk_workspace(conn, org)
    fA = await _mk_file(conn, org, wsA)
    try:
        with pytest.raises(TenantOwnershipError):
            await enqueue_document_processing_job(org, wsB, fA)  # fA is in wsA
        with pytest.raises(asyncpg.PostgresError):
            await _enqueue_sql(conn, org, wsB, fA)  # composite FK rejects
    finally:
        await _cleanup(conn, wsA, wsB); await conn.close()


# =========================================================================== #
# RLS isolation + privilege boundary
# =========================================================================== #
@pytest.mark.asyncio
async def test_product_cannot_see_other_org_jobs():
    conn = await _open()
    await _ensure_product_role(conn)
    orgA, orgB = uuid.uuid4(), uuid.uuid4()
    wsA = await _mk_workspace(conn, orgA); wsB = await _mk_workspace(conn, orgB)
    fA = await _mk_file(conn, orgA, wsA); fB = await _mk_file(conn, orgB, wsB)
    try:
        await _enqueue_sql(conn, orgA, wsA, fA)
        await _enqueue_sql(conn, orgB, wsB, fB)
        async with conn.transaction():
            await conn.execute(f"SET LOCAL ROLE {_PRODUCT_ROLE}")
            await conn.execute("SELECT set_config('app.current_org_id',$1,true)", str(orgA))
            rows = await conn.fetch("SELECT org_id FROM ben.document_processing_jobs")
            assert rows and all(r["org_id"] == orgA for r in rows)
    finally:
        await _cleanup(conn, wsA, wsB); await conn.close()


@pytest.mark.asyncio
async def test_product_cannot_mutate_other_org_jobs():
    conn = await _open()
    await _ensure_product_role(conn)
    orgA, orgB = uuid.uuid4(), uuid.uuid4()
    wsA = await _mk_workspace(conn, orgA); wsB = await _mk_workspace(conn, orgB)
    fA = await _mk_file(conn, orgA, wsA); fB = await _mk_file(conn, orgB, wsB)
    try:
        jB = await _enqueue_sql(conn, orgB, wsB, fB)
        async with conn.transaction():
            await conn.execute(f"SET LOCAL ROLE {_PRODUCT_ROLE}")
            await conn.execute("SELECT set_config('app.current_org_id',$1,true)", str(orgA))
            status = await conn.execute(
                "UPDATE ben.document_processing_jobs SET worker_id='x' WHERE id=$1", jB)
            assert status.endswith(" 0")  # RLS hides org-B row -> 0 rows updated
        # Verify unchanged from a privileged view.
        assert await conn.fetchval(
            "SELECT worker_id FROM ben.document_processing_jobs WHERE id=$1", jB) is None
    finally:
        await _cleanup(conn, wsA, wsB); await conn.close()


@pytest.mark.asyncio
async def test_privileged_claim_across_orgs_and_product_cannot_execute():
    conn = await _open()
    await _ensure_product_role(conn)
    orgA, orgB = uuid.uuid4(), uuid.uuid4()
    wsA = await _mk_workspace(conn, orgA); wsB = await _mk_workspace(conn, orgB)
    fA = await _mk_file(conn, orgA, wsA); fB = await _mk_file(conn, orgB, wsB)
    try:
        await _enqueue_sql(conn, orgA, wsA, fA)
        await _enqueue_sql(conn, orgB, wsB, fB)
        # SECURITY DEFINER claim (runs as ben_doc_processor) sees BOTH orgs.
        claimed = await conn.fetch(
            "SELECT * FROM ben.claim_document_processing_jobs('sys', 300, 10)")
        orgs = {r["org_id"] for r in claimed}
        assert orgA in orgs and orgB in orgs
        # Product role has no EXECUTE on the system function.
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with conn.transaction():
                await conn.execute(f"SET LOCAL ROLE {_PRODUCT_ROLE}")
                await conn.fetch("SELECT * FROM ben.claim_document_processing_jobs('x',300,1)")
    finally:
        await _cleanup(conn, wsA, wsB); await conn.close()


@pytest.mark.asyncio
async def test_privileged_function_exposes_no_document_content():
    conn = await _open()
    org = uuid.uuid4(); ws = await _mk_workspace(conn, org); fid = await _mk_file(conn, org, ws)
    try:
        await _enqueue_sql(conn, org, ws, fid)
        claimed = await conn.fetch("SELECT * FROM ben.claim_document_processing_jobs('sys',300,1)")
        assert claimed and set(claimed[0].keys()) <= _ALLOWED_CLAIM_KEYS
        cols = {r["column_name"] for r in await conn.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='ben' AND table_name='document_processing_jobs'")}
        assert cols.isdisjoint(_CONTENT_LIKE)  # no content/bytes column exists at all
    finally:
        await _cleanup(conn, ws); await conn.close()


# =========================================================================== #
# Claim semantics
# =========================================================================== #
@pytest.mark.asyncio
async def test_queued_job_is_claimed_and_attempts_increment_once():
    conn = await _open()
    org = uuid.uuid4(); ws = await _mk_workspace(conn, org); fid = await _mk_file(conn, org, ws)
    try:
        jid = await _enqueue_sql(conn, org, ws, fid)
        claimed = await conn.fetch("SELECT * FROM ben.claim_document_processing_jobs('w1',300,5)")
        assert len(claimed) == 1 and claimed[0]["job_id"] == jid and claimed[0]["attempts"] == 1
        row = await conn.fetchrow(
            "SELECT status, attempts, claimed_at, lease_expires_at, worker_id "
            "FROM ben.document_processing_jobs WHERE id=$1", jid)
        assert row["status"] == "running" and row["attempts"] == 1
        assert row["claimed_at"] and row["lease_expires_at"] and row["worker_id"] == "w1"
    finally:
        await _cleanup(conn, ws); await conn.close()


@pytest.mark.asyncio
async def test_deterministic_claim_order():
    conn = await _open()
    org = uuid.uuid4(); ws = await _mk_workspace(conn, org)
    f1 = await _mk_file(conn, org, ws); f2 = await _mk_file(conn, org, ws); f3 = await _mk_file(conn, org, ws)
    try:
        # Distinct available_at => deterministic (available_at, created_at, id) order.
        j2 = await _enqueue_sql(conn, org, ws, f2, available_at_sql="now() - interval '10 s'")
        j1 = await _enqueue_sql(conn, org, ws, f1, available_at_sql="now() - interval '30 s'")
        j3 = await _enqueue_sql(conn, org, ws, f3, available_at_sql="now() - interval '20 s'")
        order = []
        for _ in range(3):
            r = await conn.fetch("SELECT * FROM ben.claim_document_processing_jobs('w',300,1)")
            order.append(r[0]["job_id"])
        assert order == [j1, j3, j2]  # oldest available_at first
    finally:
        await _cleanup(conn, ws); await conn.close()


@pytest.mark.asyncio
async def test_concurrent_claimers_and_skip_locked():
    conn = await _open()
    org = uuid.uuid4(); ws = await _mk_workspace(conn, org)
    f1 = await _mk_file(conn, org, ws); f2 = await _mk_file(conn, org, ws)
    connB = await asyncpg.connect(_DSN)
    try:
        j1 = await _enqueue_sql(conn, org, ws, f1, available_at_sql="now() - interval '5 s'")
        j2 = await _enqueue_sql(conn, org, ws, f2, available_at_sql="now() - interval '1 s'")
        # A claims one inside an open transaction (row locked, uncommitted).
        txA = conn.transaction(); await txA.start()
        a = await conn.fetch("SELECT * FROM ben.claim_document_processing_jobs('A',300,1)")
        assert len(a) == 1
        # B concurrently claims: SKIP LOCKED must skip A's row and take the other.
        b = await connB.fetch("SELECT * FROM ben.claim_document_processing_jobs('B',300,1)")
        assert len(b) == 1
        assert a[0]["job_id"] != b[0]["job_id"]
        assert {a[0]["job_id"], b[0]["job_id"]} == {j1, j2}
        await txA.commit()
    finally:
        await connB.close()
        await _cleanup(conn, ws); await conn.close()


@pytest.mark.asyncio
async def test_single_job_cannot_be_claimed_twice():
    conn = await _open()
    org = uuid.uuid4(); ws = await _mk_workspace(conn, org); fid = await _mk_file(conn, org, ws)
    connB = await asyncpg.connect(_DSN)
    try:
        await _enqueue_sql(conn, org, ws, fid)
        txA = conn.transaction(); await txA.start()
        a = await conn.fetch("SELECT * FROM ben.claim_document_processing_jobs('A',300,5)")
        assert len(a) == 1
        b = await connB.fetch("SELECT * FROM ben.claim_document_processing_jobs('B',300,5)")
        assert b == []  # only queued job is locked by A -> B gets nothing
        await txA.commit()
    finally:
        await connB.close()
        await _cleanup(conn, ws); await conn.close()


@pytest.mark.asyncio
async def test_future_and_unavailable_jobs_not_claimed():
    conn = await _open()
    org = uuid.uuid4(); ws = await _mk_workspace(conn, org); fid = await _mk_file(conn, org, ws)
    try:
        await _enqueue_sql(conn, org, ws, fid, available_at_sql="now() + interval '1 hour'")
        assert await conn.fetch("SELECT * FROM ben.claim_document_processing_jobs('w',300,5)") == []
    finally:
        await _cleanup(conn, ws); await conn.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["succeeded", "failed", "cancelled"])
async def test_terminal_jobs_not_claimed(terminal):
    conn = await _open()
    org = uuid.uuid4(); ws = await _mk_workspace(conn, org); fid = await _mk_file(conn, org, ws)
    try:
        await _enqueue_sql(conn, org, ws, fid, status=terminal)
        assert await conn.fetch("SELECT * FROM ben.claim_document_processing_jobs('w',300,5)") == []
    finally:
        await _cleanup(conn, ws); await conn.close()


# =========================================================================== #
# Lease / reaper / retry
# =========================================================================== #
@pytest.mark.asyncio
async def test_expired_lease_requeued_with_backoff():
    conn = await _open()
    org = uuid.uuid4(); ws = await _mk_workspace(conn, org); fid = await _mk_file(conn, org, ws)
    try:
        jid = await _enqueue_sql(conn, org, ws, fid, status="running", attempts=1, max_attempts=5,
                                 claimed=True, lease_sql="now() - interval '1 min'", worker="w1")
        reaped = await conn.fetch(
            "SELECT * FROM ben.reap_expired_document_processing_jobs(30, 3600, 100)")
        assert any(r["job_id"] == jid and r["outcome"] == "queued" for r in reaped)
        row = await conn.fetchrow(
            "SELECT status, attempts, available_at, claimed_at, lease_expires_at, worker_id, last_error_code "
            "FROM ben.document_processing_jobs WHERE id=$1", jid)
        assert row["status"] == "queued" and row["attempts"] == 1  # attempts unchanged by reaper
        assert row["claimed_at"] is None and row["lease_expires_at"] is None and row["worker_id"] is None
        assert row["available_at"] > await conn.fetchval("SELECT now()")
        assert row["last_error_code"] == "lease_expired"
    finally:
        await _cleanup(conn, ws); await conn.close()


@pytest.mark.asyncio
async def test_expired_lease_at_retry_limit_fails():
    conn = await _open()
    org = uuid.uuid4(); ws = await _mk_workspace(conn, org); fid = await _mk_file(conn, org, ws)
    try:
        jid = await _enqueue_sql(conn, org, ws, fid, status="running", attempts=5, max_attempts=5,
                                 claimed=True, lease_sql="now() - interval '1 min'", worker="w1")
        reaped = await conn.fetch(
            "SELECT * FROM ben.reap_expired_document_processing_jobs(30, 3600, 100)")
        assert any(r["job_id"] == jid and r["outcome"] == "failed" for r in reaped)
        row = await conn.fetchrow(
            "SELECT status, last_error_code FROM ben.document_processing_jobs WHERE id=$1", jid)
        assert row["status"] == "failed" and row["last_error_code"] == "max_attempts_exceeded"
    finally:
        await _cleanup(conn, ws); await conn.close()


@pytest.mark.asyncio
async def test_nonexpired_running_job_untouched_by_reaper():
    conn = await _open()
    org = uuid.uuid4(); ws = await _mk_workspace(conn, org); fid = await _mk_file(conn, org, ws)
    try:
        jid = await _enqueue_sql(conn, org, ws, fid, status="running", attempts=1, max_attempts=5,
                                 claimed=True, lease_sql="now() + interval '5 min'", worker="w1")
        reaped = await conn.fetch(
            "SELECT * FROM ben.reap_expired_document_processing_jobs(30, 3600, 100)")
        assert all(r["job_id"] != jid for r in reaped)
        assert await conn.fetchval(
            "SELECT status FROM ben.document_processing_jobs WHERE id=$1", jid) == "running"
    finally:
        await _cleanup(conn, ws); await conn.close()


@pytest.mark.asyncio
async def test_concurrent_reapers_safe():
    conn = await _open()
    org = uuid.uuid4(); ws = await _mk_workspace(conn, org)
    f1 = await _mk_file(conn, org, ws); f2 = await _mk_file(conn, org, ws)
    connB = await asyncpg.connect(_DSN)
    try:
        j1 = await _enqueue_sql(conn, org, ws, f1, status="running", attempts=1, claimed=True,
                                lease_sql="now() - interval '2 min'", worker="w1")
        j2 = await _enqueue_sql(conn, org, ws, f2, status="running", attempts=1, claimed=True,
                                lease_sql="now() - interval '2 min'", worker="w2")
        txA = conn.transaction(); await txA.start()
        a = await conn.fetch("SELECT * FROM ben.reap_expired_document_processing_jobs(30,3600,1)")
        b = await connB.fetch("SELECT * FROM ben.reap_expired_document_processing_jobs(30,3600,10)")
        await txA.commit()
        handled = {r["job_id"] for r in a} | {r["job_id"] for r in b}
        assert handled == {j1, j2}
        # No job processed twice: total distinct == total reaped rows.
        assert len(a) + len(b) == 2
    finally:
        await connB.close()
        await _cleanup(conn, ws); await conn.close()


@pytest.mark.asyncio
async def test_requeue_schedules_future_available_at(fresh_engine):
    from services.workspace_files.job_queue import requeue_job
    conn = await _open()
    org = uuid.uuid4(); ws = await _mk_workspace(conn, org); fid = await _mk_file(conn, org, ws)
    try:
        jid = await _enqueue_sql(conn, org, ws, fid, status="running", attempts=1, claimed=True,
                                 lease_sql="now() + interval '5 min'", worker="w1")
        res = await requeue_job(jid, delay_seconds=120, error_code="transient")
        assert res and res["status"] == "queued"
        row = await conn.fetchrow(
            "SELECT status, available_at, worker_id, last_error_code FROM ben.document_processing_jobs WHERE id=$1",
            jid)
        assert row["status"] == "queued" and row["worker_id"] is None
        delta = (row["available_at"] - await conn.fetchval("SELECT now()")).total_seconds()
        assert 60 < delta <= 121 and row["last_error_code"] == "transient"
    finally:
        await _cleanup(conn, ws); await conn.close()


# =========================================================================== #
# Constraints / schema / RLS structure
# =========================================================================== #
@pytest.mark.asyncio
async def test_fk_cascade_on_workspace_file_deletion():
    conn = await _open()
    org = uuid.uuid4(); ws = await _mk_workspace(conn, org); fid = await _mk_file(conn, org, ws)
    try:
        await _enqueue_sql(conn, org, ws, fid)
        await conn.execute("DELETE FROM ben.workspace_files WHERE id=$1", fid)
        assert await conn.fetchval(
            "SELECT count(*) FROM ben.document_processing_jobs WHERE file_id=$1", fid) == 0
    finally:
        await _cleanup(conn, ws); await conn.close()


@pytest.mark.asyncio
async def test_active_job_partial_uniqueness():
    conn = await _open()
    org = uuid.uuid4(); ws = await _mk_workspace(conn, org); fid = await _mk_file(conn, org, ws)
    try:
        await _enqueue_sql(conn, org, ws, fid, status="queued")
        with pytest.raises(asyncpg.UniqueViolationError):
            await _enqueue_sql(conn, org, ws, fid, status="queued")
        # A terminal job does not block a new active job (partial predicate).
        await conn.execute(
            "UPDATE ben.document_processing_jobs SET status='succeeded', claimed_at=NULL, "
            "lease_expires_at=NULL, worker_id=NULL WHERE file_id=$1", fid)
        await _enqueue_sql(conn, org, ws, fid, status="queued")  # allowed now
    finally:
        await _cleanup(conn, ws); await conn.close()


@pytest.mark.asyncio
async def test_rls_enabled_forced_with_policies():
    conn = await _open()
    try:
        row = await conn.fetchrow(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE relname='document_processing_jobs'")
        assert row["relrowsecurity"] is True   # RLS ENABLE
        assert row["relforcerowsecurity"] is True  # RLS FORCE
        pol = {p["policyname"]: p for p in await conn.fetch(
            "SELECT policyname, roles, coalesce(qual,'') q, coalesce(with_check,'') w "
            "FROM pg_policies WHERE tablename='document_processing_jobs'")}
        iso = pol["document_processing_jobs_org_isolation"]
        assert "app.current_org_id" in iso["q"] and "app.current_org_id" in iso["w"]
        sysp = pol["document_processing_jobs_system"]
        assert "ben_doc_processor" in sysp["roles"] and sysp["q"] == "true"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_state_machine_check_constraints():
    conn = await _open()
    org = uuid.uuid4(); ws = await _mk_workspace(conn, org); fid = await _mk_file(conn, org, ws)
    try:
        # running requires lease/claim/worker
        with pytest.raises(asyncpg.CheckViolationError):
            await _enqueue_sql(conn, org, ws, fid, status="running")  # no claim/lease/worker
        # queued must not carry lease/worker
        with pytest.raises(asyncpg.CheckViolationError):
            await _enqueue_sql(conn, org, ws, fid, status="queued", claimed=True,
                               lease_sql="now()+interval '1 min'", worker="w")
        # invalid status rejected
        with pytest.raises(asyncpg.PostgresError):
            await _enqueue_sql(conn, org, ws, fid, status="banana")
    finally:
        await _cleanup(conn, ws); await conn.close()


# =========================================================================== #
# Inertness proofs
# =========================================================================== #
@pytest.mark.asyncio
async def test_no_workspacefile_or_page_chunk_mutation(fresh_engine):
    from services.workspace_files.job_queue import (
        enqueue_document_processing_job, claim_jobs, requeue_job, complete_job,
    )
    conn = await _open()
    org = uuid.uuid4(); ws = await _mk_workspace(conn, org); fid = await _mk_file(conn, org, ws)
    try:
        before = await conn.fetchrow(
            "SELECT extraction_status, index_status FROM ben.workspace_files WHERE id=$1", fid)
        res = await enqueue_document_processing_job(org, ws, fid)
        claimed = await claim_jobs("sys", lease_seconds=300, limit=5)
        await requeue_job(uuid.UUID(res["id"]), delay_seconds=0)
        await claim_jobs("sys", lease_seconds=300, limit=5)
        await complete_job(uuid.UUID(res["id"]), "succeeded")
        after = await conn.fetchrow(
            "SELECT extraction_status, index_status FROM ben.workspace_files WHERE id=$1", fid)
        assert dict(after) == dict(before)  # lifecycle untouched
        assert after["extraction_status"] == "pending" and after["index_status"] == "not_indexed"
        assert await conn.fetchval(
            "SELECT count(*) FROM ben.workspace_file_pages WHERE file_id=$1", fid) == 0
        assert await conn.fetchval(
            "SELECT count(*) FROM ben.workspace_file_chunks WHERE file_id=$1", fid) == 0
    finally:
        await _cleanup(conn, ws); await conn.close()


def test_no_extraction_invocation_in_substrate():
    """Gate 3A must not import or call extraction/parse/chunk anywhere in the substrate."""
    import pathlib
    src = pathlib.Path("services/workspace_files/job_queue.py").read_text()
    for forbidden in (
        "run_structured_extraction", "extraction_pipeline",
        "chunk_structured_document", ".parse(", "resolve_parser",
    ):
        assert forbidden not in src, f"substrate must not reference {forbidden}"


def test_upload_path_wired_to_jobs_as_of_gate3b():
    """As of Gate 3B, upload_file can enqueue a durable job. The wiring is behind the
    BEN_DOC_PROCESSING_ENABLED flag (OFF preserves the synchronous path); the Gate 3A
    substrate itself still performs no extraction."""
    import pathlib
    src = pathlib.Path("services/workspace_files/service.py").read_text()
    assert "enqueue_document_processing_job" in src  # Gate 3B wiring present
    upload_fn = src.split("async def upload_file", 1)[1].split("async def process_file", 1)[0]
    assert "enqueue_document_processing_job" in upload_fn
    assert "_doc_processing_enabled" in upload_fn  # flag-gated activation
