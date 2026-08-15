"""Allowlisted document-processing runner.

Proves fail-closed flags, file/workspace allowlists, no silent FIFO fallback,
and that an older historical queued job outside the allowlist stays untouched.
Real-DB tests SKIP when Postgres or migration 026 is unavailable.
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
    return org, ws, hid, cid


# =========================================================================== #
# Config fail-closed (no DB)
# =========================================================================== #
def test_runner_disabled_by_default():
    assert resolve_runner_claim_policy() == "disabled"


def test_runner_on_empty_allowlist_is_fail_closed(monkeypatch):
    monkeypatch.setenv("BEN_DOC_RUNNER_ENABLED", "on")
    assert resolve_runner_claim_policy() == "fail_closed"


def test_invalid_uuids_do_not_open_global(monkeypatch):
    monkeypatch.setenv("BEN_DOC_RUNNER_ENABLED", "on")
    monkeypatch.setenv("BEN_DOC_RUNNER_FILE_IDS", "not-a-uuid, also-bad")
    monkeypatch.setenv("BEN_DOC_RUNNER_CLAIM_GLOBAL", "off")
    assert parse_uuid_allowlist("not-a-uuid, also-bad") == []
    assert resolve_runner_claim_policy() == "fail_closed"


def test_claim_global_only_when_explicit_and_allowlists_empty(monkeypatch):
    monkeypatch.setenv("BEN_DOC_RUNNER_ENABLED", "on")
    monkeypatch.setenv("BEN_DOC_RUNNER_CLAIM_GLOBAL", "on")
    assert resolve_runner_claim_policy() == "global"
    monkeypatch.setenv("BEN_DOC_RUNNER_FILE_IDS", str(uuid.uuid4()))
    assert resolve_runner_claim_policy() == "allowlist"


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
    assert "claim_jobs_for_allowlist(" in runner_fn
    assert "claim_jobs(" in generic
    # Generic path must not call the allowlist claim.
    assert "claim_jobs_for_allowlist(" not in generic


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


# =========================================================================== #
# A / B — disabled and fail-closed
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
async def test_runner_fail_closed_empty_allowlist(fresh_engine, monkeypatch):
    conn = await _open()
    org = ws = None
    try:
        org, ws, hid, cid = await _queue_hist_and_canary(conn)
        monkeypatch.setenv("BEN_DOC_RUNNER_ENABLED", "on")
        monkeypatch.setenv("BEN_DOC_RUNNER_CLAIM_GLOBAL", "off")
        summary = await drain_document_processing_jobs_for_runner(worker_id="r-closed", limit=10)
        assert summary["claim_policy"] == "fail_closed"
        assert summary["claimed"] == 0
        assert (await _job(conn, hid))["attempts"] == 0
        assert (await _job(conn, cid))["attempts"] == 0
    finally:
        await _cleanup_ws(conn, ws)
        await conn.close()
        if org is not None and ws is not None:
            _cleanup_storage(org, ws)


# =========================================================================== #
# C / E — file allowlist; historical FIFO head untouched
# =========================================================================== #
@pytest.mark.asyncio
async def test_file_allowlist_claims_only_match_and_leaves_historical(fresh_engine, monkeypatch):
    conn = await _open()
    org = ws = None
    try:
        org, ws, hid, cid = await _queue_hist_and_canary(conn)
        monkeypatch.setenv("BEN_DOC_RUNNER_ENABLED", "on")
        monkeypatch.setenv("BEN_DOC_RUNNER_FILE_IDS", str(cid))
        summary = await drain_document_processing_jobs_for_runner(worker_id="r-file", limit=10)
        assert summary["claim_policy"] == "allowlist"
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
# D — workspace allowlist
# =========================================================================== #
@pytest.mark.asyncio
async def test_workspace_allowlist_does_not_claim_other_workspace(fresh_engine, monkeypatch):
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
        assert summary["claimed"] == 1
        assert (await _file(conn, aid))["status"] == "ready"
        assert (await _job(conn, bid))["status"] == "queued" and (await _job(conn, bid))["attempts"] == 0
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
        monkeypatch.setenv("BEN_DOC_RUNNER_FILE_IDS", f"{cid},{sid}")
        first = await drain_document_processing_jobs_for_runner(worker_id="r-batch", limit=1)
        assert first["claimed"] == 1
        # Older allowed job (canary, 1h) before second (30m). Historical is older but not allowed.
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
        assert summary["claim_policy"] == "fail_closed"
        assert summary["claimed"] == 0
        assert (await _job(conn, hid))["attempts"] == 0
        assert (await _job(conn, cid))["attempts"] == 0
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
async def test_runner_transient_failure_requeues_only_allowlisted(fresh_engine, monkeypatch):
    async def raise_transient(*a, **k):
        raise RuntimeError("transient db blip")

    monkeypatch.setattr("services.workspace_files.drain.run_structured_extraction", raise_transient)
    conn = await _open()
    org = ws = None
    try:
        org, ws, hid, cid = await _queue_hist_and_canary(conn)
        monkeypatch.setenv("BEN_DOC_RUNNER_ENABLED", "on")
        monkeypatch.setenv("BEN_DOC_RUNNER_FILE_IDS", str(cid))
        summary = await drain_document_processing_jobs_for_runner(worker_id="r-retry", limit=10)
        assert summary["requeued"] == 1
        canary = await _job(conn, cid)
        assert canary["status"] == "queued" and canary["attempts"] == 1
        assert canary["available_at"] > await conn.fetchval("SELECT now()")
        hist = await _job(conn, hid)
        assert hist["status"] == "queued" and hist["attempts"] == 0
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
        monkeypatch.setenv("BEN_DOC_RUNNER_ENABLED", "on")
        monkeypatch.setenv("BEN_DOC_RUNNER_FILE_IDS", str(hid))
        # Historical-like broken PDF is allowlisted only in this test fixture (not prod).
        summary = await drain_document_processing_jobs_for_runner(worker_id="r-fail", limit=10)
        assert summary["failed"] >= 1
        f = await _file(conn, hid)
        assert f["status"] == "failed"
        assert (await _job(conn, hid))["status"] == "failed"
        assert (await _job(conn, cid))["status"] == "queued" and (await _job(conn, cid))["attempts"] == 0
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
# CLAIM_GLOBAL must not override a present allowlist
# =========================================================================== #
@pytest.mark.asyncio
async def test_claim_global_does_not_override_file_allowlist(fresh_engine, monkeypatch):
    conn = await _open()
    org = ws = None
    try:
        org, ws, hid, cid = await _queue_hist_and_canary(conn)
        monkeypatch.setenv("BEN_DOC_RUNNER_ENABLED", "on")
        monkeypatch.setenv("BEN_DOC_RUNNER_CLAIM_GLOBAL", "on")
        monkeypatch.setenv("BEN_DOC_RUNNER_FILE_IDS", str(cid))
        summary = await drain_document_processing_jobs_for_runner(worker_id="r-noglobal", limit=10)
        assert summary["claim_policy"] == "allowlist"
        assert summary["claimed"] == 1
        assert (await _job(conn, cid))["status"] == "succeeded"
        hist = await _job(conn, hid)
        assert hist["status"] == "queued" and hist["attempts"] == 0
        assert hist["worker_id"] is None
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
        # Generic claim is global FIFO. Make this pair the oldest due rows so
        # leftover fixtures from other tests cannot steal the claim.
        await conn.execute(
            """
            UPDATE ben.document_processing_jobs
               SET available_at = (
                   SELECT COALESCE(min(available_at), now()) - interval '2 hours'
                     FROM ben.document_processing_jobs
                    WHERE status = 'queued'
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
        assert stats["claim_policy"] == "allowlist"
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
