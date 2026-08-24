"""Document-processing runner with persisted eligibility.

Proves the runner claims runner_eligible jobs only, never generic FIFO,
never CLAIM_GLOBAL, and that quarantined historical queued jobs stay untouched.
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
    drain_document_processing_jobs,
    drain_document_processing_jobs_for_runner,
    runner_processing_stats,
)
from services.workspace_files.runner_config import (
    parse_uuid_allowlist,
    resolve_runner_claim_policy,
)

_DSN = os.getenv("BEN_TEST_PG_DSN") or "postgresql://ben:ben@127.0.0.1:5432/ben"
_RUNNER_FN = "ben.claim_document_processing_jobs_for_allowlist(text,integer,integer,uuid[],uuid[])"
_ELIGIBLE_FN = "ben.claim_document_processing_jobs_for_eligible(text,integer,integer)"
_HIST_NAME = "PRICE QUOTATION _ TECHNICAL SPECIFICATION.pdf"


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
    if not await conn.fetchval("SELECT to_regprocedure($1) IS NOT NULL", _RUNNER_FN):
        await conn.close()
        pytest.skip("migration 026 claim_document_processing_jobs_for_allowlist not applied")
    if not await conn.fetchval("SELECT to_regprocedure($1) IS NOT NULL", _ELIGIBLE_FN):
        await conn.close()
        pytest.skip("migration 027 claim_document_processing_jobs_for_eligible not applied")
    return conn


async def _mk_workspace(conn, org_id, name="runner-ws") -> uuid.UUID:
    ws = uuid.uuid4()
    await conn.execute(
        "INSERT INTO ben.projects (id, org_id, name, status) VALUES ($1,$2,$3,'active')",
        ws, org_id, name,
    )
    return ws


async def _job(conn, fid):
    return await conn.fetchrow(
        "SELECT id, status, attempts, available_at, worker_id, last_error_code, runner_eligible "
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


async def _cleanup_ws(conn, *workspaces):
    """Remove jobs/files/projects so global FIFO tests stay isolated."""
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


async def _queue_hist_and_canary(conn):
    org = uuid.uuid4()
    ws = await _mk_workspace(conn, org)
    historical = await _upload(org, ws, _HIST_NAME, "application/pdf", b"%%%not-a-real-pdf%%%")
    canary = await _upload(org, ws, "runner_canary.txt", "text/plain", b"runner canary body")
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
    await conn.execute(
        "UPDATE ben.document_processing_jobs SET runner_eligible = false WHERE id=$1",
        hj["id"],
    )
    return org, ws, hid, cid


# =========================================================================== #
# Config: disabled vs persisted-eligible (no DB)
# =========================================================================== #
def test_runner_disabled_by_default():
    assert resolve_runner_claim_policy() == "disabled"


def test_runner_on_empty_allowlist_is_eligible(monkeypatch):
    monkeypatch.setenv("BEN_DOC_RUNNER_ENABLED", "on")
    assert resolve_runner_claim_policy() == "eligible"


def test_invalid_uuids_do_not_open_global(monkeypatch):
    monkeypatch.setenv("BEN_DOC_RUNNER_ENABLED", "on")
    monkeypatch.setenv("BEN_DOC_RUNNER_FILE_IDS", "not-a-uuid, also-bad")
    monkeypatch.setenv("BEN_DOC_RUNNER_CLAIM_GLOBAL", "off")
    assert parse_uuid_allowlist("not-a-uuid, also-bad") == []
    assert resolve_runner_claim_policy() == "eligible"


def test_claim_global_is_ignored_when_runner_enabled(monkeypatch):
    monkeypatch.setenv("BEN_DOC_RUNNER_ENABLED", "on")
    monkeypatch.setenv("BEN_DOC_RUNNER_CLAIM_GLOBAL", "on")
    assert resolve_runner_claim_policy() == "eligible"
    monkeypatch.setenv("BEN_DOC_RUNNER_FILE_IDS", str(uuid.uuid4()))
    assert resolve_runner_claim_policy() == "eligible"


def test_runner_source_never_imports_gate4a():
    drain = pathlib.Path("services/workspace_files/drain.py").read_text()
    cfg = pathlib.Path("services/workspace_files/runner_config.py").read_text()
    router = pathlib.Path("routers/document_processing.py").read_text()
    for src in (drain, cfg, router):
        assert "chunk_retriever" not in src
        assert "BEN_WORKSPACE_CHUNK_RETRIEVAL" not in src
    runner_fn = drain.split("async def drain_document_processing_jobs_for_runner", 1)[1]
    generic = drain.split("async def drain_document_processing_jobs(", 1)[1].split(
        "async def drain_document_processing_job_for_file", 1
    )[0]
    assert "claim_jobs_for_eligible(" in runner_fn
    assert "reap_expired_jobs_for_eligible(" in runner_fn
    assert 'policy != "eligible"' in runner_fn
    stripped = (
        runner_fn
        .replace("claim_jobs_for_eligible(", "")
        .replace("reap_expired_jobs_for_eligible(", "")
    )
    assert "claim_jobs(" not in stripped
    assert "reap_expired_jobs(" not in stripped
    assert "claim_jobs_for_allowlist(" not in runner_fn
    assert 'elif policy == "global"' not in runner_fn
    assert "claim_jobs(" in generic
    assert "claim_jobs_for_allowlist(" not in generic
    assert "claim_jobs_for_eligible(" not in generic


def test_generic_drain_router_unchanged():
    src = pathlib.Path("routers/document_processing.py").read_text()
    assert '@router.post("/processing/drain")' in src
    assert '@router.post("/processing/runner/drain")' in src
    assert '@router.get("/processing/runner/stats")' in src
    generic_fn = src.split("async def drain_processing_jobs(", 1)[1].split("\n@", 1)[0]
    assert "drain_document_processing_jobs(" in generic_fn
    assert "for_runner" not in generic_fn
    assert "file_ids" not in generic_fn


def test_generic_claim_sql_still_unscoped_fifo():
    src = pathlib.Path("database/migrations/versions/024_document_processing_jobs.py").read_text()
    claim_fn = src.split("CREATE OR REPLACE FUNCTION {SCHEMA}.claim_document_processing_jobs", 1)[1]
    claim_fn = claim_fn.split("CREATE OR REPLACE FUNCTION", 1)[0]
    assert "ORDER BY c.available_at, c.created_at, c.id" in claim_fn
    assert "FOR UPDATE SKIP LOCKED" in claim_fn
    assert "p_file_ids" not in claim_fn
    assert "p_workspace_ids" not in claim_fn


def test_eligible_migration_quarantines_historical_on_every_claim_path():
    src = pathlib.Path("database/migrations/versions/027_runner_eligible_jobs.py").read_text()
    assert "43cef794-1fff-40ae-bd3c-47d9fc121518" in src
    assert "0bbd0dd0-cfd9-4ef4-a3b9-c1e96bef83a4" in src
    assert "claim_document_processing_jobs_for_eligible" in src
    assert "reap_expired_document_processing_jobs_for_eligible" in src
    assert "c.runner_eligible IS TRUE" in src
    assert "DEFAULT false" in src
    assert "BEN_WORKSPACE_CHUNK_RETRIEVAL" not in src
    file_claim = src.split("claim_document_processing_job_for_file(", 1)[1]
    file_claim = file_claim.split("CREATE OR REPLACE FUNCTION", 1)[0]
    allow_claim = src.split("claim_document_processing_jobs_for_allowlist(", 1)[1]
    allow_claim = allow_claim.split("CREATE OR REPLACE FUNCTION", 1)[0]
    assert "runner_eligible" not in file_claim
    assert "runner_eligible" not in allow_claim
    generic_claim = src.split("CREATE OR REPLACE FUNCTION {SCHEMA}.claim_document_processing_jobs(", 1)[1]
    generic_claim = generic_claim.split("CREATE OR REPLACE FUNCTION", 1)[0]
    assert "{_ELIGIBLE}" in generic_claim
    eligible_claim = src.split("CREATE OR REPLACE FUNCTION {SCHEMA}.{_CLAIM_ELIGIBLE}(", 1)[1]
    eligible_claim = eligible_claim.split("CREATE OR REPLACE FUNCTION", 1)[0]
    assert "{_ELIGIBLE}" in eligible_claim


def test_health_ready_do_not_consult_queue_depth():
    health = pathlib.Path("services/health_service.py").read_text()
    assert "document_processing_job_stats" not in health
    assert "due_queue_depth" not in health
    assert "runner_processing" not in health
    assert "BEN_DOC_RUNNER" not in health


def test_invalid_enabled_flag_is_off(monkeypatch):
    monkeypatch.setenv("BEN_DOC_RUNNER_ENABLED", "maybe")
    monkeypatch.setenv("BEN_DOC_RUNNER_CLAIM_GLOBAL", "yes-please")
    assert resolve_runner_claim_policy() == "disabled"


@pytest.mark.asyncio
async def test_unknown_policy_cannot_reach_global_fifo(fresh_engine, monkeypatch):
    """Unexpected policy values must fail closed — never generic FIFO."""
    calls = {"claim_jobs": 0, "reap": 0, "eligible_claim": 0, "eligible_reap": 0}

    async def _claim_jobs(*_a, **_k):
        calls["claim_jobs"] += 1
        return []

    async def _reap(*_a, **_k):
        calls["reap"] += 1
        return []

    async def _eligible_claim(*_a, **_k):
        calls["eligible_claim"] += 1
        return []

    async def _eligible_reap(*_a, **_k):
        calls["eligible_reap"] += 1
        return []

    monkeypatch.setattr(
        "services.workspace_files.drain.resolve_runner_claim_policy",
        lambda: "not-a-real-policy",
    )
    monkeypatch.setattr("services.workspace_files.drain.claim_jobs", _claim_jobs)
    monkeypatch.setattr("services.workspace_files.drain.reap_expired_jobs", _reap)
    monkeypatch.setattr(
        "services.workspace_files.drain.claim_jobs_for_eligible", _eligible_claim,
    )
    monkeypatch.setattr(
        "services.workspace_files.drain.reap_expired_jobs_for_eligible", _eligible_reap,
    )

    conn = await _open()
    org = ws = None
    try:
        org, ws, hid, cid = await _queue_hist_and_canary(conn)
        summary = await drain_document_processing_jobs_for_runner(worker_id="r-unknown", limit=10)
        assert summary["claim_policy"] == "not-a-real-policy"
        assert summary["claimed"] == 0
        assert summary["reaped"] == 0
        assert calls == {"claim_jobs": 0, "reap": 0, "eligible_claim": 0, "eligible_reap": 0}
        assert (await _job(conn, hid))["status"] == "queued" and (await _job(conn, hid))["attempts"] == 0
        assert (await _job(conn, cid))["status"] == "queued" and (await _job(conn, cid))["attempts"] == 0
    finally:
        await _cleanup_ws(conn, ws)
        await conn.close()
        if org is not None and ws is not None:
            _cleanup_storage(org, ws)


# =========================================================================== #
# A / B — disabled vs enabled eligible claim
# =========================================================================== #
@pytest.mark.asyncio
async def test_runner_disabled_claims_nothing(fresh_engine, monkeypatch):
    conn = await _open()
    org = ws = None
    try:
        org, ws, hid, cid = await _queue_hist_and_canary(conn)
        monkeypatch.delenv("BEN_DOC_RUNNER_ENABLED", raising=False)
        summary = await drain_document_processing_jobs_for_runner(worker_id="r-off", limit=10)
        assert summary["claim_policy"] == "disabled"
        assert summary["claimed"] == 0
        assert (await _job(conn, hid))["status"] == "queued" and (await _job(conn, hid))["attempts"] == 0
        assert (await _job(conn, cid))["status"] == "queued" and (await _job(conn, cid))["attempts"] == 0
    finally:
        await _cleanup_ws(conn, ws)
        await conn.close()
        if org is not None and ws is not None:
            _cleanup_storage(org, ws)


@pytest.mark.asyncio
async def test_runner_enabled_processes_eligible_not_quarantined(fresh_engine, monkeypatch):
    conn = await _open()
    org = ws = None
    try:
        org, ws, hid, cid = await _queue_hist_and_canary(conn)
        assert (await _job(conn, hid))["runner_eligible"] is False
        assert (await _job(conn, cid))["runner_eligible"] is True
        monkeypatch.setenv("BEN_DOC_RUNNER_ENABLED", "on")
        monkeypatch.setenv("BEN_DOC_RUNNER_CLAIM_GLOBAL", "off")
        summary = await drain_document_processing_jobs_for_runner(worker_id="r-eligible", limit=10)
        assert summary["claim_policy"] == "eligible"
        assert summary["claimed"] == 1
        assert summary["succeeded"] == 1
        assert (await _file(conn, cid))["status"] == "ready"
        assert (await _job(conn, cid))["status"] == "succeeded"
        hist = await _job(conn, hid)
        assert hist["status"] == "queued" and hist["attempts"] == 0
        assert hist["worker_id"] is None
        assert (await _file(conn, hid))["status"] == "queued"
    finally:
        await _cleanup_ws(conn, ws)
        await conn.close()
        if org is not None and ws is not None:
            _cleanup_storage(org, ws)


# =========================================================================== #
# C / E — env file allowlist is not the claim path; historical stays quarantined
# =========================================================================== #
@pytest.mark.asyncio
async def test_file_allowlist_is_ignored_eligible_still_processes_canary(fresh_engine, monkeypatch):
    conn = await _open()
    org = ws = None
    try:
        org, ws, hid, cid = await _queue_hist_and_canary(conn)
        monkeypatch.setenv("BEN_DOC_RUNNER_ENABLED", "on")
        monkeypatch.setenv("BEN_DOC_RUNNER_FILE_IDS", str(cid))
        summary = await drain_document_processing_jobs_for_runner(worker_id="r-file", limit=10)
        assert summary["claim_policy"] == "eligible"
        assert summary["claimed"] == 1
        assert summary["succeeded"] == 1
        assert (await _file(conn, cid))["status"] == "ready"
        assert (await _job(conn, cid))["status"] == "succeeded"
        hist = await _job(conn, hid)
        assert hist["status"] == "queued" and hist["attempts"] == 0
        assert hist["worker_id"] is None
        assert (await _file(conn, hid))["status"] == "queued"
    finally:
        await _cleanup_ws(conn, ws)
        await conn.close()
        if org is not None and ws is not None:
            _cleanup_storage(org, ws)


# =========================================================================== #
# D — env workspace allowlist is not the claim path
# =========================================================================== #
@pytest.mark.asyncio
async def test_workspace_allowlist_does_not_block_other_workspace_new_uploads(fresh_engine, monkeypatch):
    conn = await _open()
    org = ws_a = ws_b = None
    try:
        org = uuid.uuid4()
        ws_a = await _mk_workspace(conn, org, "ws-a")
        ws_b = await _mk_workspace(conn, org, "ws-b")
        a = await _upload(org, ws_a, "a.txt", "text/plain", b"workspace a body")
        b = await _upload(org, ws_b, "b.txt", "text/plain", b"workspace b body")
        aid, bid = uuid.UUID(a["id"]), uuid.UUID(b["id"])
        monkeypatch.setenv("BEN_DOC_RUNNER_ENABLED", "on")
        monkeypatch.setenv("BEN_DOC_RUNNER_WORKSPACE_IDS", str(ws_a))
        summary = await drain_document_processing_jobs_for_runner(worker_id="r-ws", limit=10)
        assert summary["claim_policy"] == "eligible"
        assert summary["claimed"] == 2
        assert (await _file(conn, aid))["status"] == "ready"
        assert (await _file(conn, bid))["status"] == "ready"
    finally:
        await _cleanup_ws(conn, ws_a, ws_b)
        await conn.close()
        if org is not None:
            if ws_a is not None:
                _cleanup_storage(org, ws_a)
            if ws_b is not None:
                _cleanup_storage(org, ws_b)


# =========================================================================== #
# F / H — mixed queue, deterministic order, batch limit
# =========================================================================== #
@pytest.mark.asyncio
async def test_mixed_queue_bounded_deterministic_order(fresh_engine, monkeypatch):
    conn = await _open()
    org = ws = None
    try:
        org, ws, hid, cid = await _queue_hist_and_canary(conn)
        second = await _upload(org, ws, "second.txt", "text/plain", b"second allowed")
        sid = uuid.UUID(second["id"])
        await conn.execute(
            "UPDATE ben.document_processing_jobs SET available_at = now() - interval '30 minutes' "
            "WHERE file_id=$1", sid,
        )
        monkeypatch.setenv("BEN_DOC_RUNNER_ENABLED", "on")
        first = await drain_document_processing_jobs_for_runner(worker_id="r-batch", limit=1)
        assert first["claimed"] == 1
        # Older eligible job (canary, 1h) before second (30m). Historical is older but ineligible.
        assert (await _job(conn, cid))["status"] == "succeeded"
        assert (await _job(conn, sid))["status"] == "queued"
        assert (await _job(conn, hid))["status"] == "queued" and (await _job(conn, hid))["attempts"] == 0
        second_cycle = await drain_document_processing_jobs_for_runner(worker_id="r-batch", limit=1)
        assert second_cycle["claimed"] == 1
        assert (await _job(conn, sid))["status"] == "succeeded"
        assert (await _job(conn, hid))["attempts"] == 0
    finally:
        await _cleanup_ws(conn, ws)
        await conn.close()
        if org is not None and ws is not None:
            _cleanup_storage(org, ws)


# =========================================================================== #
# G — invalid config cannot claim
# =========================================================================== #
@pytest.mark.asyncio
async def test_invalid_allowlist_tokens_claim_nothing(fresh_engine, monkeypatch):
    conn = await _open()
    org = ws = None
    try:
        org, ws, hid, cid = await _queue_hist_and_canary(conn)
        monkeypatch.setenv("BEN_DOC_RUNNER_ENABLED", "on")
        monkeypatch.setenv("BEN_DOC_RUNNER_FILE_IDS", "nope,still-bad")
        summary = await drain_document_processing_jobs_for_runner(worker_id="r-bad", limit=10)
        assert summary["claim_policy"] == "eligible"
        assert summary["claimed"] == 1
        assert (await _job(conn, hid))["attempts"] == 0
        assert (await _job(conn, cid))["status"] == "succeeded"
    finally:
        await _cleanup_ws(conn, ws)
        await conn.close()
        if org is not None and ws is not None:
            _cleanup_storage(org, ws)


# =========================================================================== #
# I — overlapping SKIP LOCKED
# =========================================================================== #
@pytest.mark.asyncio
async def test_overlapping_allowlist_claim_skip_locked(fresh_engine):
    conn = await _open()
    org = ws = None
    conn_b = None
    try:
        org, ws, hid, cid = await _queue_hist_and_canary(conn)
        extra = await _upload(org, ws, "extra.txt", "text/plain", b"extra allowed")
        eid = uuid.UUID(extra["id"])
        conn_b = await asyncpg.connect(_DSN)
        tx = conn.transaction()
        await tx.start()
        a = await conn.fetch(
            "SELECT * FROM ben.claim_document_processing_jobs_for_allowlist("
            "'A', 300, 1, $1::uuid[], $2::uuid[])",
            [cid, eid], [],
        )
        assert len(a) == 1
        b = await conn_b.fetch(
            "SELECT * FROM ben.claim_document_processing_jobs_for_allowlist("
            "'B', 300, 1, $1::uuid[], $2::uuid[])",
            [cid, eid], [],
        )
        assert len(b) == 1
        assert a[0]["file_id"] != b[0]["file_id"]
        assert {a[0]["file_id"], b[0]["file_id"]} == {cid, eid}
        hist = await conn_b.fetchrow(
            "SELECT status, attempts FROM ben.document_processing_jobs WHERE file_id=$1", hid,
        )
        assert hist["status"] == "queued" and hist["attempts"] == 0
        denied = await conn_b.fetch(
            "SELECT * FROM ben.claim_document_processing_jobs_for_allowlist("
            "'deny-hist', 300, 10, $1::uuid[], $2::uuid[])",
            [hid], [],
        )
        # Ineligible non-protected jobs remain operator-selectable via allowlist.
        assert len(denied) == 1 and denied[0]["file_id"] == hid
        await conn_b.execute(
            "UPDATE ben.document_processing_jobs SET status='queued', attempts=0, "
            "claimed_at=NULL, lease_expires_at=NULL, worker_id=NULL WHERE file_id=$1",
            hid,
        )
        await tx.commit()
        await conn.execute(
            "UPDATE ben.document_processing_jobs SET status='queued', attempts=0, "
            "claimed_at=NULL, lease_expires_at=NULL, worker_id=NULL "
            "WHERE file_id = ANY($1::uuid[])",
            [cid, eid],
        )
    finally:
        if conn_b is not None:
            await conn_b.close()
        await _cleanup_ws(conn, ws)
        await conn.close()
        if org is not None and ws is not None:
            _cleanup_storage(org, ws)


@pytest.mark.asyncio
async def test_overlapping_eligible_claim_skip_locked(fresh_engine):
    conn = await _open()
    org = ws = None
    conn_b = None
    try:
        org, ws, hid, cid = await _queue_hist_and_canary(conn)
        extra = await _upload(org, ws, "extra.txt", "text/plain", b"extra allowed")
        eid = uuid.UUID(extra["id"])
        await conn.execute(
            """
            UPDATE ben.document_processing_jobs
               SET available_at = now() - interval '3 hours'
             WHERE file_id = $1
            """,
            cid,
        )
        await conn.execute(
            """
            UPDATE ben.document_processing_jobs
               SET available_at = now() - interval '2 hours'
             WHERE file_id = $1
            """,
            eid,
        )
        conn_b = await asyncpg.connect(_DSN)
        tx = conn.transaction()
        await tx.start()
        a = await conn.fetch(
            "SELECT * FROM ben.claim_document_processing_jobs_for_eligible('A', 300, 1)",
        )
        assert len(a) == 1
        b = await conn_b.fetch(
            "SELECT * FROM ben.claim_document_processing_jobs_for_eligible('B', 300, 1)",
        )
        assert len(b) == 1
        assert a[0]["file_id"] != b[0]["file_id"]
        assert {a[0]["file_id"], b[0]["file_id"]} == {cid, eid}
        hist = await conn_b.fetchrow(
            "SELECT status, attempts FROM ben.document_processing_jobs WHERE file_id=$1", hid,
        )
        assert hist["status"] == "queued" and hist["attempts"] == 0
        await tx.commit()
        await conn.execute(
            "UPDATE ben.document_processing_jobs SET status='queued', attempts=0, "
            "claimed_at=NULL, lease_expires_at=NULL, worker_id=NULL "
            "WHERE file_id = ANY($1::uuid[])",
            [cid, eid],
        )
    finally:
        if conn_b is not None:
            await conn_b.close()
        await _cleanup_ws(conn, ws)
        await conn.close()
        if org is not None and ws is not None:
            _cleanup_storage(org, ws)


# =========================================================================== #
# J / K — retry and terminal failure reuse existing machinery
# =========================================================================== #
@pytest.mark.asyncio
async def test_runner_transient_failure_requeues_only_eligible(fresh_engine, monkeypatch):
    async def raise_transient(*a, **k):
        raise RuntimeError("transient db blip")

    monkeypatch.setattr("services.workspace_files.drain.run_structured_extraction", raise_transient)
    conn = await _open()
    org = ws = None
    try:
        org, ws, hid, cid = await _queue_hist_and_canary(conn)
        monkeypatch.setenv("BEN_DOC_RUNNER_ENABLED", "on")
        summary = await drain_document_processing_jobs_for_runner(worker_id="r-retry", limit=10)
        assert summary["requeued"] == 1
        canary = await _job(conn, cid)
        assert canary["status"] == "queued" and canary["attempts"] == 1
        assert canary["runner_eligible"] is True
        assert canary["available_at"] > await conn.fetchval("SELECT now()")
        hist = await _job(conn, hid)
        assert hist["status"] == "queued" and hist["attempts"] == 0
        assert hist["runner_eligible"] is False
    finally:
        await _cleanup_ws(conn, ws)
        await conn.close()
        if org is not None and ws is not None:
            _cleanup_storage(org, ws)


@pytest.mark.asyncio
async def test_runner_terminal_failure_does_not_ready_or_touch_historical(fresh_engine, monkeypatch):
    conn = await _open()
    org = ws = None
    try:
        org, ws, hid, cid = await _queue_hist_and_canary(conn)
        broken = await _upload(org, ws, "broken.pdf", "application/pdf", b"%%%not-a-real-pdf%%%")
        bid = uuid.UUID(broken["id"])
        monkeypatch.setenv("BEN_DOC_RUNNER_ENABLED", "on")
        summary = await drain_document_processing_jobs_for_runner(worker_id="r-fail", limit=10)
        assert summary["failed"] >= 1
        assert summary["succeeded"] >= 1
        f = await _file(conn, bid)
        assert f["status"] == "failed"
        assert (await _job(conn, bid))["status"] == "failed"
        assert (await _file(conn, cid))["status"] == "ready"
        hist = await _job(conn, hid)
        assert hist["status"] == "queued" and hist["attempts"] == 0
        assert (await _file(conn, hid))["status"] == "queued"
    finally:
        await _cleanup_ws(conn, ws)
        await conn.close()
        if org is not None and ws is not None:
            _cleanup_storage(org, ws)


# =========================================================================== #
# Empty allowlist SQL is fail-closed even if called directly
# =========================================================================== #
@pytest.mark.asyncio
async def test_sql_empty_allowlist_claims_nothing(fresh_engine):
    conn = await _open()
    org = ws = None
    try:
        org, ws, hid, cid = await _queue_hist_and_canary(conn)
        claimed = await conn.fetch(
            "SELECT * FROM ben.claim_document_processing_jobs_for_allowlist("
            "'sql', 300, 10, $1::uuid[], $2::uuid[])",
            [], [],
        )
        assert claimed == []
        assert (await _job(conn, hid))["attempts"] == 0
        assert (await _job(conn, cid))["attempts"] == 0
    finally:
        await _cleanup_ws(conn, ws)
        await conn.close()
        if org is not None and ws is not None:
            _cleanup_storage(org, ws)


# =========================================================================== #
# CLAIM_GLOBAL is ignored; runner still uses persisted eligibility
# =========================================================================== #
@pytest.mark.asyncio
async def test_claim_global_does_not_open_fifo_or_touch_historical(fresh_engine, monkeypatch):
    conn = await _open()
    org = ws = None
    try:
        org, ws, hid, cid = await _queue_hist_and_canary(conn)
        monkeypatch.setenv("BEN_DOC_RUNNER_ENABLED", "on")
        monkeypatch.setenv("BEN_DOC_RUNNER_CLAIM_GLOBAL", "on")
        monkeypatch.setenv("BEN_DOC_RUNNER_FILE_IDS", str(cid))
        summary = await drain_document_processing_jobs_for_runner(worker_id="r-noglobal", limit=10)
        assert summary["claim_policy"] == "eligible"
        assert summary["claimed"] == 1
        assert (await _job(conn, cid))["status"] == "succeeded"
        hist = await _job(conn, hid)
        assert hist["status"] == "queued" and hist["attempts"] == 0
        assert hist["worker_id"] is None
        assert hist["runner_eligible"] is False
    finally:
        await _cleanup_ws(conn, ws)
        await conn.close()
        if org is not None and ws is not None:
            _cleanup_storage(org, ws)


# =========================================================================== #
# M — generic FIFO drain still claims the oldest due job
# =========================================================================== #
@pytest.mark.asyncio
async def test_generic_fifo_drain_still_claims_oldest_due_job(fresh_engine, monkeypatch):
    conn = await _open()
    org = ws = None
    try:
        org = uuid.uuid4()
        ws = await _mk_workspace(conn, org, "generic-fifo")
        older = await _upload(org, ws, "older.txt", "text/plain", b"older fifo body")
        newer = await _upload(org, ws, "newer.txt", "text/plain", b"newer fifo body")
        oid, nid = uuid.UUID(older["id"]), uuid.UUID(newer["id"])
        hist = await _upload(org, ws, _HIST_NAME, "application/pdf", b"%%%not-a-real-pdf%%%")
        hid = uuid.UUID(hist["id"])
        await conn.execute(
            """
            UPDATE ben.document_processing_jobs
               SET runner_eligible = false,
                   available_at = (
                       SELECT COALESCE(min(available_at), now()) - interval '4 hours'
                         FROM ben.document_processing_jobs
                        WHERE status = 'queued'
                   )
             WHERE file_id = $1
            """,
            hid,
        )
        # Generic claim is eligible-only FIFO. Make this pair the oldest due eligible rows.
        await conn.execute(
            """
            UPDATE ben.document_processing_jobs
               SET available_at = (
                   SELECT COALESCE(min(available_at), now()) - interval '2 hours'
                     FROM ben.document_processing_jobs
                    WHERE status = 'queued' AND runner_eligible IS TRUE
               )
             WHERE file_id = $1
            """,
            oid,
        )
        await conn.execute(
            """
            UPDATE ben.document_processing_jobs
               SET available_at = (
                   SELECT COALESCE(min(available_at), now()) + interval '1 hour'
                     FROM ben.document_processing_jobs
                    WHERE file_id = $1
               )
             WHERE file_id = $2
            """,
            oid, nid,
        )
        monkeypatch.delenv("BEN_DOC_RUNNER_ENABLED", raising=False)
        summary = await drain_document_processing_jobs(worker_id="generic-fifo", limit=1)
        assert summary["claimed"] == 1
        assert (await _job(conn, oid))["status"] == "succeeded"
        assert (await _job(conn, nid))["status"] == "queued" and (await _job(conn, nid))["attempts"] == 0
        hist_job = await _job(conn, hid)
        assert hist_job["status"] == "queued" and hist_job["attempts"] == 0
        assert hist_job["runner_eligible"] is False
    finally:
        await _cleanup_ws(conn, ws)
        await conn.close()
        if org is not None and ws is not None:
            _cleanup_storage(org, ws)


# =========================================================================== #
# Stats are read-only and do not claim
# =========================================================================== #
@pytest.mark.asyncio
async def test_runner_stats_are_read_only(fresh_engine, monkeypatch):
    conn = await _open()
    org = ws = None
    try:
        org, ws, hid, cid = await _queue_hist_and_canary(conn)
        monkeypatch.setenv("BEN_DOC_RUNNER_ENABLED", "on")
        monkeypatch.setenv("BEN_DOC_RUNNER_FILE_IDS", str(cid))
        stats = await runner_processing_stats()
        for key in (
            "due_queue_depth", "oldest_due_queued_age_s", "running_count",
            "failed_count", "retry_count", "succeeded_24h", "claim_policy",
        ):
            assert key in stats
        assert stats["claim_policy"] == "eligible"
        assert stats["due_queue_depth"] >= 2
        assert stats["oldest_due_queued_age_s"] is not None
        assert stats["oldest_due_queued_age_s"] >= 3600
        hist = await _job(conn, hid)
        canary = await _job(conn, cid)
        assert hist["status"] == "queued" and hist["attempts"] == 0
        assert canary["status"] == "queued" and canary["attempts"] == 0
    finally:
        await _cleanup_ws(conn, ws)
        await conn.close()
        if org is not None and ws is not None:
            _cleanup_storage(org, ws)
