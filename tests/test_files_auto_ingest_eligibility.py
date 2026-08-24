"""Automatic ingest eligibility for new uploads.

New enqueue is runner_eligible. Existing/historical rows default ineligible.
The two production queued file IDs cannot be claimed, reaped, or marked eligible.
CLAIM_GLOBAL / generic FIFO is not the runner path. Gate 4A stays dormant.
Real-DB tests SKIP when Postgres or migration 027 is unavailable.
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
from services.workspace_files.drain import (
    drain_document_processing_job_for_file,
    drain_document_processing_jobs,
    drain_document_processing_jobs_for_runner,
)
from services.workspace_files.ingest_eligibility import (
    PROTECTED_INGEST_FILE_IDS,
    file_is_ingest_protected,
    new_job_is_runner_eligible,
)
from services.workspace_files.job_queue import (
    claim_job_for_file,
    claim_jobs,
    claim_jobs_for_allowlist,
    reap_expired_jobs,
    reap_expired_jobs_for_allowlist,
    reap_expired_jobs_for_eligible,
    reap_expired_jobs_for_file,
)

_DSN = os.getenv("BEN_TEST_PG_DSN") or "postgresql://ben:ben@127.0.0.1:5432/ben"
_ELIGIBLE_FN = "ben.claim_document_processing_jobs_for_eligible(text,integer,integer)"
_HIST_A = uuid.UUID("43cef794-1fff-40ae-bd3c-47d9fc121518")
_HIST_B = uuid.UUID("0bbd0dd0-cfd9-4ef4-a3b9-c1e96bef83a4")


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
    if not await conn.fetchval("SELECT to_regprocedure($1) IS NOT NULL", _ELIGIBLE_FN):
        await conn.close()
        pytest.skip("migration 027 claim_document_processing_jobs_for_eligible not applied")
    return conn


async def _mk_workspace(conn, org_id, name="ingest-ws") -> uuid.UUID:
    ws = uuid.uuid4()
    await conn.execute(
        "INSERT INTO ben.projects (id, org_id, name, status) VALUES ($1,$2,$3,'active')",
        ws, org_id, name,
    )
    return ws


async def _job(conn, fid):
    return await conn.fetchrow(
        """
        SELECT id, status, attempts, available_at, worker_id, last_error_code, runner_eligible
          FROM ben.document_processing_jobs
         WHERE file_id=$1
         ORDER BY created_at
         LIMIT 1
        """,
        fid,
    )


async def _file(conn, fid):
    return await conn.fetchrow(
        "SELECT status, extracted_text FROM ben.workspace_files WHERE id=$1",
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


async def _purge_protected(conn):
    ids = list(PROTECTED_INGEST_FILE_IDS)
    await conn.execute(
        "DELETE FROM ben.document_processing_jobs WHERE file_id = ANY($1::uuid[])", ids,
    )
    await conn.execute(
        "DELETE FROM ben.workspace_files WHERE id = ANY($1::uuid[])", ids,
    )


async def _insert_protected_queued(conn, org, ws, file_id: uuid.UUID, *, running=False):
    await conn.execute(
        """
        INSERT INTO ben.workspace_files
            (id, org_id, workspace_id, project_id, original_filename, display_name,
             media_type, byte_size, checksum, storage_key, status)
        VALUES ($1,$2,$3,$3,'protected.pdf','protected.pdf','application/pdf',0,'x',$4,'queued')
        """,
        file_id, org, ws, f"k/{file_id}",
    )
    status = "running" if running else "queued"
    claimed = "now()" if running else "NULL"
    lease = "now() - interval '2 minutes'" if running else "NULL"
    worker = "'dead'" if running else "NULL"
    await conn.execute(
        f"""
        INSERT INTO ben.document_processing_jobs
            (org_id, workspace_id, file_id, job_type, status, extraction_version,
             chunking_version, attempts, max_attempts, available_at, claimed_at,
             lease_expires_at, worker_id, runner_eligible)
        VALUES ($1,$2,$3,'structured_extraction',$4,1,1,
                $5,5, now() - interval '2 hours', {claimed}, {lease}, {worker}, true)
        """,
        org, ws, file_id, status, 1 if running else 0,
    )


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
    monkeypatch.delenv("BEN_DOC_RUNNER_ENABLED", raising=False)
    monkeypatch.delenv("BEN_DOC_RUNNER_FILE_IDS", raising=False)
    monkeypatch.delenv("BEN_DOC_RUNNER_WORKSPACE_IDS", raising=False)
    monkeypatch.delenv("BEN_DOC_RUNNER_CLAIM_GLOBAL", raising=False)


async def _upload(org, ws, name, ct, data):
    return await file_service.upload_file(
        org_id=org, workspace_id=ws, upload=_Upload(name, ct, data), uploaded_by="tester",
    )


# =========================================================================== #
# Unit (no DB)
# =========================================================================== #
def test_protected_ids_are_exactly_the_two_historical_files():
    assert PROTECTED_INGEST_FILE_IDS == frozenset({_HIST_A, _HIST_B})
    assert file_is_ingest_protected(_HIST_A)
    assert file_is_ingest_protected(str(_HIST_B))
    assert not file_is_ingest_protected(uuid.uuid4())
    assert new_job_is_runner_eligible(uuid.uuid4()) is True
    assert new_job_is_runner_eligible(_HIST_A) is False


def test_runner_source_never_uses_global_fifo_or_gate4a():
    drain = pathlib.Path("services/workspace_files/drain.py").read_text()
    cfg = pathlib.Path("services/workspace_files/runner_config.py").read_text()
    runner_fn = drain.split("async def drain_document_processing_jobs_for_runner", 1)[1]
    assert "claim_jobs_for_eligible(" in runner_fn
    stripped = (
        runner_fn
        .replace("claim_jobs_for_eligible(", "")
        .replace("reap_expired_jobs_for_eligible(", "")
    )
    assert "claim_jobs(" not in stripped
    assert "claim_jobs_for_allowlist(" not in runner_fn
    assert "claim_global_enabled" not in runner_fn
    assert "BEN_WORKSPACE_CHUNK_RETRIEVAL" not in drain
    assert "chunk_retriever" not in drain
    assert "eligible" in cfg
    assert "CLAIM_GLOBAL is ignored" in cfg or "CLAIM_GLOBAL is ignored" in drain or "ignored" in cfg


def test_gate4a_production_flags_not_enabled_in_this_change():
    env = pathlib.Path(".env.example").read_text()
    assert "BEN_WORKSPACE_CHUNK_RETRIEVAL=off" in env
    assert "BEN_DOC_RUNNER_ENABLED=off" in env
    assert "BEN_DOC_UPLOAD_WAKE_ENABLED=off" in env
    mig = pathlib.Path("database/migrations/versions/027_runner_eligible_jobs.py").read_text()
    assert "BEN_WORKSPACE_CHUNK_RETRIEVAL" not in mig


# =========================================================================== #
# New upload → eligible → READY without allowlist
# =========================================================================== #
@pytest.mark.asyncio
async def test_new_upload_is_eligible_and_runner_reaches_ready(fresh_engine, monkeypatch):
    conn = await _open()
    org = ws = None
    try:
        org = uuid.uuid4()
        ws = await _mk_workspace(conn, org)
        uploaded = await _upload(org, ws, "auto.txt", "text/plain", b"auto ingest body")
        fid = uuid.UUID(uploaded["id"])
        job = await _job(conn, fid)
        assert job["status"] == "queued"
        assert job["runner_eligible"] is True
        monkeypatch.setenv("BEN_DOC_RUNNER_ENABLED", "on")
        summary = await drain_document_processing_jobs_for_runner(worker_id="auto-new", limit=10)
        assert summary["claim_policy"] == "eligible"
        assert summary["claimed"] == 1
        assert summary["succeeded"] == 1
        assert (await _file(conn, fid))["status"] == "ready"
        assert (await _file(conn, fid))["extracted_text"] == "auto ingest body"
        assert (await _job(conn, fid))["status"] == "succeeded"
        assert (await _job(conn, fid))["runner_eligible"] is True
    finally:
        await _cleanup_ws(conn, ws)
        await conn.close()
        if org is not None and ws is not None:
            _cleanup_storage(org, ws)


@pytest.mark.asyncio
async def test_sql_insert_without_eligible_defaults_quarantined(fresh_engine, monkeypatch):
    conn = await _open()
    org = ws = None
    try:
        org = uuid.uuid4()
        ws = await _mk_workspace(conn, org)
        fid = uuid.uuid4()
        await conn.execute(
            """
            INSERT INTO ben.workspace_files
                (id, org_id, workspace_id, project_id, original_filename, display_name,
                 media_type, byte_size, checksum, storage_key, status)
            VALUES ($1,$2,$3,$3,'old.txt','old.txt','text/plain',0,'x',$4,'queued')
            """,
            fid, org, ws, f"k/{fid}",
        )
        await conn.execute(
            """
            INSERT INTO ben.document_processing_jobs
                (org_id, workspace_id, file_id, job_type, status, extraction_version,
                 chunking_version, max_attempts, available_at)
            VALUES ($1,$2,$3,'structured_extraction','queued',1,1,5, now() - interval '1 hour')
            """,
            org, ws, fid,
        )
        job = await _job(conn, fid)
        assert job["runner_eligible"] is False
        monkeypatch.setenv("BEN_DOC_RUNNER_ENABLED", "on")
        summary = await drain_document_processing_jobs_for_runner(worker_id="auto-old", limit=10)
        assert summary["claimed"] == 0
        assert (await _job(conn, fid))["status"] == "queued"
        assert (await _job(conn, fid))["attempts"] == 0
        generic = await drain_document_processing_jobs(worker_id="generic-old", limit=10)
        assert generic["claimed"] == 0
        assert (await _job(conn, fid))["attempts"] == 0
    finally:
        await _cleanup_ws(conn, ws)
        await conn.close()
        if org is not None and ws is not None:
            _cleanup_storage(org, ws)


# =========================================================================== #
# Operator-selected recovery of ineligible non-protected jobs
# =========================================================================== #
@pytest.mark.asyncio
async def test_scoped_drain_claims_selected_ineligible_non_protected(fresh_engine, monkeypatch):
    conn = await _open()
    org = ws = None
    try:
        org = uuid.uuid4()
        ws = await _mk_workspace(conn, org)
        uploaded = await _upload(org, ws, "recover.txt", "text/plain", b"operator recover body")
        fid = uuid.UUID(uploaded["id"])
        await conn.execute(
            "UPDATE ben.document_processing_jobs SET runner_eligible=false WHERE file_id=$1",
            fid,
        )
        assert (await _job(conn, fid))["runner_eligible"] is False
        monkeypatch.setenv("BEN_DOC_RUNNER_ENABLED", "on")
        auto = await drain_document_processing_jobs_for_runner(worker_id="auto-skip", limit=10)
        generic = await drain_document_processing_jobs(worker_id="generic-skip", limit=10)
        skipped = await _job(conn, fid)
        assert skipped["status"] == "queued" and skipped["attempts"] == 0
        assert skipped["runner_eligible"] is False
        scoped = await drain_document_processing_job_for_file(fid, worker_id="op-scoped")
        assert scoped["claimed"] == 1
        assert scoped["succeeded"] == 1
        assert (await _file(conn, fid))["status"] == "ready"
        assert (await _file(conn, fid))["extracted_text"] == "operator recover body"
        job = await _job(conn, fid)
        assert job["status"] == "succeeded"
        assert job["runner_eligible"] is False
    finally:
        await _cleanup_ws(conn, ws)
        await conn.close()
        if org is not None and ws is not None:
            _cleanup_storage(org, ws)


@pytest.mark.asyncio
async def test_scoped_reap_recovers_selected_ineligible_running_job(fresh_engine, monkeypatch):
    conn = await _open()
    org = ws = None
    try:
        org = uuid.uuid4()
        ws = await _mk_workspace(conn, org)
        uploaded = await _upload(org, ws, "reap-me.txt", "text/plain", b"reap body")
        fid = uuid.UUID(uploaded["id"])
        j = await _job(conn, fid)
        await conn.execute(
            """
            UPDATE ben.document_processing_jobs
               SET runner_eligible=false, status='running', attempts=1,
                   claimed_at=now(), lease_expires_at=now() - interval '2 minutes',
                   worker_id='crashed'
             WHERE id=$1
            """,
            j["id"],
        )
        monkeypatch.setenv("BEN_DOC_RUNNER_ENABLED", "on")
        await drain_document_processing_jobs_for_runner(worker_id="auto-reap-skip", limit=10)
        still = await _job(conn, fid)
        assert still["status"] == "running" and still["attempts"] == 1
        generic_reap = await reap_expired_jobs()
        assert j["id"] not in {uuid.UUID(r.get("job_id")) for r in generic_reap}
        assert (await _job(conn, fid))["status"] == "running"
        reaped = await reap_expired_jobs_for_file(fid)
        assert len(reaped) == 1
        job = await _job(conn, fid)
        assert job["status"] == "queued"
        assert job["runner_eligible"] is False
        assert job["worker_id"] is None
    finally:
        await _cleanup_ws(conn, ws)
        await conn.close()
        if org is not None and ws is not None:
            _cleanup_storage(org, ws)


@pytest.mark.asyncio
async def test_allowlist_claim_and_reap_selected_ineligible_non_protected(fresh_engine):
    conn = await _open()
    org = ws = None
    try:
        org = uuid.uuid4()
        ws = await _mk_workspace(conn, org)
        a = await _upload(org, ws, "al-a.txt", "text/plain", b"allow a")
        b = await _upload(org, ws, "al-b.txt", "text/plain", b"allow b")
        aid, bid = uuid.UUID(a["id"]), uuid.UUID(b["id"])
        await conn.execute(
            "UPDATE ben.document_processing_jobs SET runner_eligible=false WHERE file_id = ANY($1::uuid[])",
            [aid, bid],
        )
        claimed = await claim_jobs_for_allowlist(
            "op-al", file_ids=[aid], workspace_ids=[], limit=10,
        )
        assert len(claimed) == 1
        assert uuid.UUID(claimed[0]["file_id"]) == aid
        aj = await _job(conn, aid)
        assert aj["status"] == "running"
        assert aj["runner_eligible"] is False
        assert (await _job(conn, bid))["status"] == "queued"

        bj = await _job(conn, bid)
        await conn.execute(
            """
            UPDATE ben.document_processing_jobs
               SET status='running', attempts=1, claimed_at=now(),
                   lease_expires_at=now() - interval '2 minutes', worker_id='dead'
             WHERE id=$1
            """,
            bj["id"],
        )
        eligible_reap = await reap_expired_jobs_for_eligible()
        assert bj["id"] not in {uuid.UUID(r.get("job_id")) for r in eligible_reap}
        reaped = await reap_expired_jobs_for_allowlist(file_ids=[bid], workspace_ids=[])
        assert len(reaped) == 1
        b_after = await _job(conn, bid)
        assert b_after["status"] == "queued"
        assert b_after["runner_eligible"] is False
        await conn.execute(
            "UPDATE ben.document_processing_jobs SET status='queued', attempts=0, "
            "claimed_at=NULL, lease_expires_at=NULL, worker_id=NULL WHERE file_id=$1",
            aid,
        )
    finally:
        await _cleanup_ws(conn, ws)
        await conn.close()
        if org is not None and ws is not None:
            _cleanup_storage(org, ws)


# =========================================================================== #
# Exact production historical file IDs
# =========================================================================== #
@pytest.mark.asyncio
async def test_protected_ids_cannot_be_marked_eligible_or_claimed(fresh_engine, monkeypatch):
    conn = await _open()
    org = ws = None
    try:
        org = uuid.uuid4()
        ws = await _mk_workspace(conn, org)
        await _purge_protected(conn)
        for fid in (_HIST_A, _HIST_B):
            await _insert_protected_queued(conn, org, ws, fid)
            row = await _job(conn, fid)
            assert row["runner_eligible"] is False
            await conn.execute(
                "UPDATE ben.document_processing_jobs SET runner_eligible = true WHERE file_id=$1",
                fid,
            )
            assert (await _job(conn, fid))["runner_eligible"] is False

        canary = await _upload(org, ws, "live.txt", "text/plain", b"live body")
        cid = uuid.UUID(canary["id"])

        empty_eligible = await conn.fetch(
            "SELECT * FROM ben.claim_document_processing_jobs_for_eligible('p', 300, 50)",
        )
        claimed_ids = {r["file_id"] for r in empty_eligible}
        assert _HIST_A not in claimed_ids and _HIST_B not in claimed_ids
        assert cid in claimed_ids
        await conn.execute(
            "UPDATE ben.document_processing_jobs SET status='queued', attempts=0, "
            "claimed_at=NULL, lease_expires_at=NULL, worker_id=NULL WHERE file_id=$1",
            cid,
        )

        for fid in (_HIST_A, _HIST_B):
            assert await conn.fetch(
                "SELECT * FROM ben.claim_document_processing_job_for_file('p', 300, $1)", fid,
            ) == []
            assert await claim_job_for_file("py", fid) == []
            scoped = await drain_document_processing_job_for_file(fid, worker_id="scoped-hist")
            assert scoped["claimed"] == 0
            assert scoped["outcome"] == "no_eligible_job"
            assert (await _job(conn, fid))["status"] == "queued"
            assert (await _job(conn, fid))["attempts"] == 0

        allowlisted = await claim_jobs_for_allowlist(
            "al", file_ids=[_HIST_A, _HIST_B, cid], workspace_ids=[], limit=10,
        )
        assert {uuid.UUID(j["file_id"]) for j in allowlisted} == {cid}
        await conn.execute(
            "UPDATE ben.document_processing_jobs SET status='queued', attempts=0, "
            "claimed_at=NULL, lease_expires_at=NULL, worker_id=NULL WHERE file_id=$1",
            cid,
        )

        generic = await claim_jobs("generic", limit=10)
        gids = {uuid.UUID(j["file_id"]) for j in generic}
        assert cid in gids
        assert _HIST_A not in gids and _HIST_B not in gids
        await conn.execute(
            "UPDATE ben.document_processing_jobs SET status='queued', attempts=0, "
            "claimed_at=NULL, lease_expires_at=NULL, worker_id=NULL WHERE file_id=$1",
            cid,
        )

        monkeypatch.setenv("BEN_DOC_RUNNER_ENABLED", "on")
        monkeypatch.setenv("BEN_DOC_RUNNER_CLAIM_GLOBAL", "on")
        monkeypatch.setenv("BEN_DOC_RUNNER_FILE_IDS", f"{_HIST_A},{_HIST_B}")
        summary = await drain_document_processing_jobs_for_runner(worker_id="runner-hist", limit=10)
        assert summary["claim_policy"] == "eligible"
        assert (await _file(conn, cid))["status"] == "ready"
        for fid in (_HIST_A, _HIST_B):
            hist = await _job(conn, fid)
            assert hist["status"] == "queued" and hist["attempts"] == 0
            assert hist["runner_eligible"] is False
            assert hist["worker_id"] is None
    finally:
        await _purge_protected(conn)
        await _cleanup_ws(conn, ws)
        await conn.close()
        if org is not None and ws is not None:
            _cleanup_storage(org, ws)


@pytest.mark.asyncio
async def test_protected_expired_leases_are_not_reaped(fresh_engine):
    conn = await _open()
    org = ws = None
    try:
        org = uuid.uuid4()
        ws = await _mk_workspace(conn, org)
        await _purge_protected(conn)
        await _insert_protected_queued(conn, org, ws, _HIST_A, running=True)
        await _insert_protected_queued(conn, org, ws, _HIST_B, running=True)
        canary = await _upload(org, ws, "lease.txt", "text/plain", b"lease body")
        cid = uuid.UUID(canary["id"])
        cj = await _job(conn, cid)
        await conn.execute(
            """
            UPDATE ben.document_processing_jobs
               SET status='running', attempts=1, claimed_at=now(),
                   lease_expires_at=now() - interval '2 minutes', worker_id='dead'
             WHERE id=$1
            """,
            cj["id"],
        )
        reaped = await reap_expired_jobs_for_eligible()
        reaped_ids = {uuid.UUID(r.get("job_id")) for r in reaped}
        assert cj["id"] in reaped_ids
        generic_reap = await reap_expired_jobs()
        for fid in (_HIST_A, _HIST_B):
            hist = await _job(conn, fid)
            assert hist["status"] == "running"
            assert hist["attempts"] == 1
            assert await reap_expired_jobs_for_file(fid) == []
            assert hist["id"] not in reaped_ids
            assert hist["id"] not in {uuid.UUID(r.get("job_id")) for r in generic_reap}
        canary_job = await _job(conn, cid)
        assert canary_job["status"] == "queued"
        assert canary_job["runner_eligible"] is True
    finally:
        await _purge_protected(conn)
        await _cleanup_ws(conn, ws)
        await conn.close()
        if org is not None and ws is not None:
            _cleanup_storage(org, ws)


# =========================================================================== #
# Retry / lease recovery stay inside the eligibility boundary
# =========================================================================== #
@pytest.mark.asyncio
async def test_retry_keeps_runner_eligible_true(fresh_engine, monkeypatch):
    async def raise_transient(*_a, **_k):
        raise RuntimeError("transient blip")

    monkeypatch.setattr("services.workspace_files.drain.run_structured_extraction", raise_transient)
    conn = await _open()
    org = ws = None
    try:
        org = uuid.uuid4()
        ws = await _mk_workspace(conn, org)
        uploaded = await _upload(org, ws, "retry.txt", "text/plain", b"retry body")
        fid = uuid.UUID(uploaded["id"])
        monkeypatch.setenv("BEN_DOC_RUNNER_ENABLED", "on")
        summary = await drain_document_processing_jobs_for_runner(worker_id="retry", limit=10)
        assert summary["requeued"] == 1
        job = await _job(conn, fid)
        assert job["status"] == "queued"
        assert job["attempts"] == 1
        assert job["runner_eligible"] is True
    finally:
        await _cleanup_ws(conn, ws)
        await conn.close()
        if org is not None and ws is not None:
            _cleanup_storage(org, ws)


@pytest.mark.asyncio
async def test_retry_then_success_without_changing_eligibility(fresh_engine, monkeypatch):
    calls = {"n": 0}

    async def flaky(*a, **k):
        from services.workspace_files.extraction_pipeline import run_structured_extraction
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient blip")
        return await run_structured_extraction(*a, **k)

    monkeypatch.setattr("services.workspace_files.drain.run_structured_extraction", flaky)
    conn = await _open()
    org = ws = None
    try:
        org = uuid.uuid4()
        ws = await _mk_workspace(conn, org)
        uploaded = await _upload(org, ws, "flaky.txt", "text/plain", b"flaky body")
        fid = uuid.UUID(uploaded["id"])
        monkeypatch.setenv("BEN_DOC_RUNNER_ENABLED", "on")
        first = await drain_document_processing_jobs_for_runner(worker_id="flaky1", limit=10)
        assert first["requeued"] == 1
        assert (await _job(conn, fid))["runner_eligible"] is True
        await conn.execute(
            "UPDATE ben.document_processing_jobs SET available_at = now() - interval '1 second' WHERE file_id=$1",
            fid,
        )
        second = await drain_document_processing_jobs_for_runner(worker_id="flaky2", limit=10)
        assert second["succeeded"] == 1
        job = await _job(conn, fid)
        assert job["status"] == "succeeded"
        assert job["runner_eligible"] is True
        assert (await _file(conn, fid))["status"] == "ready"
    finally:
        await _cleanup_ws(conn, ws)
        await conn.close()
        if org is not None and ws is not None:
            _cleanup_storage(org, ws)


@pytest.mark.asyncio
async def test_eligible_expired_lease_recovered_hist_frozen(fresh_engine, monkeypatch):
    conn = await _open()
    org = ws = None
    try:
        org = uuid.uuid4()
        ws = await _mk_workspace(conn, org)
        hist = await _upload(org, ws, "hist.txt", "text/plain", b"hist body")
        live = await _upload(org, ws, "live.txt", "text/plain", b"live body")
        hid, lid = uuid.UUID(hist["id"]), uuid.UUID(live["id"])
        await conn.execute(
            "UPDATE ben.document_processing_jobs SET runner_eligible=false WHERE file_id=$1", hid,
        )
        for fid in (hid, lid):
            j = await _job(conn, fid)
            await conn.execute(
                """
                UPDATE ben.document_processing_jobs
                   SET status='running', attempts=1, claimed_at=now(),
                       lease_expires_at=now() - interval '3 minutes', worker_id='crashed'
                 WHERE id=$1
                """,
                j["id"],
            )
        monkeypatch.setenv("BEN_DOC_RUNNER_ENABLED", "on")
        summary = await drain_document_processing_jobs_for_runner(worker_id="reap-live", limit=10)
        assert summary["reaped"] >= 1
        live_job = await _job(conn, lid)
        assert live_job["status"] in ("queued", "succeeded")
        assert live_job["runner_eligible"] is True
        hist_job = await _job(conn, hid)
        assert hist_job["status"] == "running"
        assert hist_job["attempts"] == 1
        assert hist_job["runner_eligible"] is False
        assert hist_job["worker_id"] == "crashed"
    finally:
        await _cleanup_ws(conn, ws)
        await conn.close()
        if org is not None and ws is not None:
            _cleanup_storage(org, ws)


# =========================================================================== #
# Isolation
# =========================================================================== #
@pytest.mark.asyncio
async def test_new_uploads_in_two_orgs_are_both_eligible(fresh_engine, monkeypatch):
    conn = await _open()
    org_a = org_b = ws_a = ws_b = None
    try:
        org_a, org_b = uuid.uuid4(), uuid.uuid4()
        ws_a = await _mk_workspace(conn, org_a, "org-a")
        ws_b = await _mk_workspace(conn, org_b, "org-b")
        a = await _upload(org_a, ws_a, "a.txt", "text/plain", b"org a body")
        b = await _upload(org_b, ws_b, "b.txt", "text/plain", b"org b body")
        aid, bid = uuid.UUID(a["id"]), uuid.UUID(b["id"])
        monkeypatch.setenv("BEN_DOC_RUNNER_ENABLED", "on")
        summary = await drain_document_processing_jobs_for_runner(worker_id="iso", limit=10)
        assert summary["claimed"] == 2
        assert (await _file(conn, aid))["status"] == "ready"
        assert (await _file(conn, bid))["status"] == "ready"
        assert (await _file(conn, aid))["extracted_text"] == "org a body"
        assert (await _file(conn, bid))["extracted_text"] == "org b body"
    finally:
        await _cleanup_ws(conn, ws_a, ws_b)
        await conn.close()
        if org_a is not None and ws_a is not None:
            _cleanup_storage(org_a, ws_a)
        if org_b is not None and ws_b is not None:
            _cleanup_storage(org_b, ws_b)
