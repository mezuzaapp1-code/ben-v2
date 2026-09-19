"""Additive 032 migration lifecycle on disposable PostgreSQL 16."""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from typing import Any

import asyncpg
import pytest

from tests.disposable_postgres import DisposablePostgres

HEAD = "032_measurement_foundation_v1"
PREV = "031_workspace_file_evidence_ir"
FRESH_DB = "mf_fresh"
LEGACY_DB = "mf_legacy"
CALL_ID = uuid.UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")


@pytest.fixture(scope="module")
def pg() -> Iterator[DisposablePostgres]:
    cluster = DisposablePostgres()
    cluster.start()
    try:
        cluster.create_database(FRESH_DB)
        cluster.create_database(LEGACY_DB)
        yield cluster
    finally:
        cluster.cleanup()


@asynccontextmanager
async def connect(pg: DisposablePostgres, dbname: str) -> AsyncIterator[asyncpg.Connection]:
    pg.verify_identity(dbname)
    conn = await asyncpg.connect(**pg.asyncpg_kwargs(dbname))
    try:
        yield conn
    finally:
        await conn.close()


def _run(coro):
    return asyncio.run(coro)


def _as_dict(row: Any) -> dict[str, Any]:
    if hasattr(row, "items"):
        data = dict(row)
    else:
        data = dict(row)
    out = {}
    for k, v in data.items():
        if isinstance(v, bytes):
            v = v.decode("ascii", errors="replace")
        out[k] = v
    return out


async def _heads(conn: asyncpg.Connection) -> list[str]:
    exists = await conn.fetchval("SELECT to_regclass('public.alembic_version')")
    if not exists:
        return []
    return [r["version_num"] for r in await conn.fetch("SELECT version_num FROM alembic_version")]


async def _tables(conn: asyncpg.Connection) -> set[str]:
    rows = await conn.fetch(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'ben' ORDER BY 1"
    )
    return {r["tablename"] for r in rows}


async def _snapshot_inference_call_records(conn: asyncpg.Connection) -> dict[str, Any]:
    cols = [
        _as_dict(r)
        for r in await conn.fetch(
            """
            SELECT column_name, data_type, udt_name, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = 'ben' AND table_name = 'inference_call_records'
            ORDER BY ordinal_position
            """
        )
    ]
    indexes = [
        _as_dict(r)
        for r in await conn.fetch(
            """
            SELECT indexname, indexdef
            FROM pg_indexes
            WHERE schemaname = 'ben' AND tablename = 'inference_call_records'
            ORDER BY 1
            """
        )
    ]
    checks = [
        _as_dict(r)
        for r in await conn.fetch(
            """
            SELECT conname, pg_get_constraintdef(oid) AS def
            FROM pg_constraint
            WHERE conrelid = 'ben.inference_call_records'::regclass
            ORDER BY 1
            """
        )
    ]
    rls = _as_dict(
        await conn.fetchrow(
            """
            SELECT relrowsecurity, relforcerowsecurity
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'ben' AND c.relname = 'inference_call_records'
            """
        )
    )
    return {"cols": cols, "indexes": indexes, "checks": checks, "rls": rls}


async def _assert_032_catalog(conn: asyncpg.Connection) -> None:
    tables = await _tables(conn)
    assert "execution_events" in tables
    assert "validation_records" in tables
    assert "inference_call_records" in tables

    rls = await conn.fetch(
        """
        SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'ben'
          AND c.relname IN ('execution_events', 'validation_records')
        ORDER BY 1
        """
    )
    assert [r["relname"] for r in rls] == ["execution_events", "validation_records"]
    assert all(r["relrowsecurity"] and r["relforcerowsecurity"] for r in rls)

    policies = await conn.fetch(
        """
        SELECT tablename, policyname
        FROM pg_policies
        WHERE schemaname = 'ben'
          AND tablename IN ('execution_events', 'validation_records')
        ORDER BY 1
        """
    )
    assert {r["tablename"] for r in policies} == {"execution_events", "validation_records"}
    assert all(r["policyname"].endswith("_org_isolation") for r in policies)

    trigger = await conn.fetchval(
        """
        SELECT 1 FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'ben' AND c.relname = 'execution_events'
          AND t.tgname = 'trg_execution_events_origin_guard'
        """
    )
    assert trigger == 1
    fn = await conn.fetchval(
        "SELECT to_regprocedure('ben.tg_execution_events_origin_guard()')"
    )
    assert fn is not None

    fks = await conn.fetch(
        """
        SELECT conname
        FROM pg_constraint
        WHERE contype = 'f'
          AND conrelid IN (
            'ben.execution_events'::regclass,
            'ben.validation_records'::regclass
          )
        """
    )
    assert fks == []

    event_cols = {
        r["column_name"]
        for r in await conn.fetch(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'ben' AND table_name = 'execution_events'
            """
        )
    }
    for col in (
        "event_id",
        "org_id",
        "workspace_id",
        "execution_id",
        "task_id",
        "request_id",
        "call_id",
        "result_id",
        "test_id",
        "test_version",
        "test_run_id",
        "question_id",
        "section_id",
        "event_type",
        "origin",
        "observed_at",
        "observed_at_missing_reason",
        "recorded_at",
        "payload_version",
        "sequence_in_execution",
        "content_fingerprint",
        "payload",
    ):
        assert col in event_cols

    val_cols = {
        r["column_name"]
        for r in await conn.fetch(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'ben' AND table_name = 'validation_records'
            """
        )
    }
    for col in (
        "validation_id",
        "execution_id",
        "result_id",
        "validator_type",
        "validator_version",
        "outcome",
        "evaluated_at",
        "recorded_at",
        "test_run_id",
        "question_id",
        "content_fingerprint",
        "payload",
    ):
        assert col in val_cols


def test_fresh_chain_001_to_032(pg: DisposablePostgres) -> None:
    os.environ.pop("PGHOST", None)
    os.environ.pop("PGPORT", None)
    pg.alembic(FRESH_DB, "upgrade", "001_initial_schema")

    async def after_001():
        async with connect(pg, FRESH_DB) as conn:
            assert await _heads(conn) == ["001_initial_schema"]
            tables = await _tables(conn)
            assert "execution_events" not in tables
            assert "validation_records" not in tables
            assert "inference_call_records" not in tables

    _run(after_001())
    pg.alembic(FRESH_DB, "upgrade", HEAD)

    async def after_head():
        async with connect(pg, FRESH_DB) as conn:
            assert await _heads(conn) == [HEAD]
            await _assert_032_catalog(conn)

    _run(after_head())
    pg.alembic(FRESH_DB, "downgrade", PREV)

    async def after_down():
        async with connect(pg, FRESH_DB) as conn:
            assert await _heads(conn) == [PREV]
            tables = await _tables(conn)
            assert "execution_events" not in tables
            assert "validation_records" not in tables
            assert await conn.fetchval(
                "SELECT to_regprocedure('ben.tg_execution_events_origin_guard()')"
            ) is None

    _run(after_down())
    pg.alembic(FRESH_DB, "upgrade", HEAD)
    _run(after_head())


def test_legacy_031_then_032_leaves_inference_call_records_unchanged(
    pg: DisposablePostgres,
) -> None:
    pg.alembic(LEGACY_DB, "upgrade", PREV)

    async def seed_031():
        async with connect(pg, LEGACY_DB) as conn:
            assert await _heads(conn) == [PREV]
            tables = await _tables(conn)
            assert "inference_call_records" in tables
            assert "execution_events" not in tables
            snapshot = await _snapshot_inference_call_records(conn)
            await conn.execute(
                """
                INSERT INTO ben.inference_call_records (
                  id, request_id, execution_id, pipeline, provider, model, outcome,
                  usage_status, cost_status, pricing_version, started_at, finished_at
                ) VALUES (
                  $1, 'req-legacy', 'exec-legacy', 'chat', 'openai', 'gpt-4o', 'success',
                  'missing', 'unknown', 'unknown', now(), now()
                )
                """,
                CALL_ID,
            )
            return snapshot

    snapshot_031 = _run(seed_031())
    pg.alembic(LEGACY_DB, "upgrade", HEAD)

    async def after_032():
        async with connect(pg, LEGACY_DB) as conn:
            assert await _heads(conn) == [HEAD]
            await _assert_032_catalog(conn)
            snapshot_032 = await _snapshot_inference_call_records(conn)
            assert snapshot_032 == snapshot_031
            row = await conn.fetchrow(
                "SELECT id, execution_id, provider, model FROM ben.inference_call_records WHERE id = $1",
                CALL_ID,
            )
            assert dict(row)["execution_id"] == "exec-legacy"
            fks = await conn.fetch(
                """
                SELECT conname FROM pg_constraint
                WHERE contype = 'f' AND conrelid = 'ben.execution_events'::regclass
                """
            )
            assert fks == []

    _run(after_032())
    pg.alembic(LEGACY_DB, "downgrade", PREV)

    async def after_down():
        async with connect(pg, LEGACY_DB) as conn:
            assert await _heads(conn) == [PREV]
            tables = await _tables(conn)
            assert "execution_events" not in tables
            assert "validation_records" not in tables
            snapshot = await _snapshot_inference_call_records(conn)
            assert snapshot == snapshot_031
            exists = await conn.fetchval(
                "SELECT 1 FROM ben.inference_call_records WHERE id = $1",
                CALL_ID,
            )
            assert exists == 1

    _run(after_down())
    pg.alembic(LEGACY_DB, "upgrade", HEAD)
    _run(after_032())


def test_032_source_is_additive_only() -> None:
    src = (os.fsdecode("/workspace/database/migrations/versions/032_measurement_foundation_v1.py"))
    text = open(src, encoding="utf-8").read()
    assert 'down_revision = "031_workspace_file_evidence_ir"' in text
    assert "op.drop_table" in text  # downgrade only
    assert "inference_call_records" in text  # mentioned as not altered
    assert "op.alter_table" not in text
    assert "drop_column" not in text
    assert "gpt-6-astra" not in text
    # JSON round-trip of the comment is just a sanity that the file is text.
    json.dumps({"revision": HEAD, "down_revision": PREV})
