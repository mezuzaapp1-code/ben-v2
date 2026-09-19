"""Disposable-Postgres verification of the repaired 001 migration chain.

Not imported by runtime. Does not start Measurement Gate 1.
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest

from tests.disposable_postgres import DisposablePostgres

ROOT = Path("/workspace")
HEAD = "031_workspace_file_evidence_ir"
APP_TABLES_001 = (
    "cognitive_events",
    "knowledge_objects",
    "messages",
    "relationships",
    "threads",
)
LATER_TABLES = (
    "ben_log_events",
    "document_processing_jobs",
    "financial_ledger",
    "inference_call_records",
    "ledger_actions",
    "ledger_approvals",
    "ledger_decisions",
    "news_articles",
    "news_claim_extractions",
    "news_claims",
    "news_event_packages",
    "news_events",
    "news_sources",
    "project_members",
    "project_tasks",
    "projects",
    "workspace_file_chunks",
    "workspace_file_evidence_ir",
    "workspace_file_pages",
    "workspace_files",
)

ORG = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
THREAD_ID = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
MSG_ID = uuid.UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
CE_ID = uuid.UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")
KO_A = uuid.UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeee1")
KO_B = uuid.UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeee2")
REL_ID = uuid.UUID("ffffffff-ffff-ffff-ffff-fffffffffff1")

STAGED_DB = "mbr_staged"
FRESH_DB = "mbr_fresh"


@pytest.fixture(scope="session")
def pg() -> Iterator[DisposablePostgres]:
    cluster = DisposablePostgres()
    cluster.start()
    try:
        cluster.create_database(STAGED_DB)
        cluster.create_database(FRESH_DB)
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


def test_001_source_has_no_live_metadata():
    src = (ROOT / "database" / "migrations" / "versions" / "001_initial_schema.py").read_text()
    assert "Base.metadata.create_all" not in src
    assert "Base.metadata.drop_all" not in src
    assert "from database.models import" not in src
    assert 'SCHEMA = "ben"' in src
    assert "DROP SCHEMA IF EXISTS" not in src
    assert "DROP SCHEMA" in src and "CASCADE" not in src.split("def downgrade", 1)[1]


async def _ben_tables(conn: asyncpg.Connection) -> list[str]:
    rows = await conn.fetch(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'ben' ORDER BY 1"
    )
    return [r["tablename"] for r in rows]


async def _column_map(conn: asyncpg.Connection, table: str) -> dict[str, asyncpg.Record]:
    rows = await conn.fetch(
        """
        SELECT column_name, data_type, udt_name, is_nullable, column_default,
               character_maximum_length, numeric_precision, numeric_scale
        FROM information_schema.columns
        WHERE table_schema = 'ben' AND table_name = $1
        """,
        table,
    )
    return {r["column_name"]: dict(r) for r in rows}


async def _assert_001_catalog(conn: asyncpg.Connection) -> None:
    assert await _ben_tables(conn) == list(APP_TABLES_001)
    for later in LATER_TABLES:
        assert later not in await _ben_tables(conn)

    threads = await _column_map(conn, "threads")
    assert set(threads) == {"id", "org_id", "title", "created_at", "updated_at"}
    assert "source_state" not in threads
    assert threads["title"]["data_type"] == "character varying"
    assert threads["title"]["character_maximum_length"] == 512
    assert threads["title"]["is_nullable"] == "NO"
    assert threads["id"]["udt_name"] == "uuid"
    assert threads["created_at"]["data_type"] == "timestamp with time zone"
    assert "gen_random_uuid" in (threads["id"]["column_default"] or "")
    assert "now()" in (threads["created_at"]["column_default"] or "")

    ko = await _column_map(conn, "knowledge_objects")
    assert ko["content"]["data_type"] == "text"
    assert ko["content"]["is_nullable"] == "NO"
    assert ko["confidence"]["data_type"] == "numeric"
    assert ko["confidence"]["numeric_precision"] == 5
    assert ko["confidence"]["numeric_scale"] == 4
    assert ko["confidence"]["is_nullable"] == "NO"

    checks = {
        r["conname"]: r["def"]
        for r in await conn.fetch(
            """
            SELECT c.conname,
                   pg_get_constraintdef(c.oid) AS def
            FROM pg_constraint c
            JOIN pg_class t ON t.oid = c.conrelid
            JOIN pg_namespace n ON n.oid = t.relnamespace
            WHERE n.nspname = 'ben' AND t.relname = 'knowledge_objects'
              AND c.contype = 'c'
            """
        )
    }
    type_sql = checks["ck_knowledge_objects_type"]
    assert "hypothesis" in type_sql and "contradiction" in type_sql and "problem" in type_sql
    assert "synthesis" not in type_sql
    assert "active" not in type_sql
    assert "active" in checks["ck_knowledge_objects_status"]

    fks = await conn.fetch(
        """
        SELECT conname, confdeltype
        FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE n.nspname = 'ben' AND c.contype = 'f'
        ORDER BY 1
        """
    )
    fk_names = {r["conname"] for r in fks}
    assert fk_names == {
        "fk_cognitive_events_thread_id",
        "fk_messages_thread_id",
        "fk_relationships_source_object_id",
        "fk_relationships_target_object_id",
    }
    assert {r["confdeltype"].decode() if isinstance(r["confdeltype"], (bytes, bytearray)) else r["confdeltype"] for r in fks} == {"c"}

    idx = {
        r["indexname"]
        for r in await conn.fetch(
            "SELECT indexname FROM pg_indexes WHERE schemaname = 'ben'"
        )
    }
    for name in (
        "ix_threads_org",
        "ix_threads_created",
        "ix_messages_org",
        "ix_messages_thread",
        "ix_messages_created",
        "ix_ce_org",
        "ix_ce_thread",
        "ix_ce_created",
        "ix_ko_org",
        "ix_ko_created",
        "ix_ko_updated",
        "ix_rel_org",
        "ix_rel_src",
        "ix_rel_tgt",
        "ix_rel_created",
        "threads_pkey",
        "messages_pkey",
        "cognitive_events_pkey",
        "knowledge_objects_pkey",
        "relationships_pkey",
    ):
        assert name in idx

    rls = await conn.fetch(
        """
        SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'ben' AND c.relkind = 'r'
        ORDER BY 1
        """
    )
    assert [r["relname"] for r in rls] == list(APP_TABLES_001)
    assert all(r["relrowsecurity"] is True for r in rls)
    assert all(r["relforcerowsecurity"] is False for r in rls)

    policies = await conn.fetch(
        """
        SELECT tablename, policyname, qual, with_check, cmd
        FROM pg_policies
        WHERE schemaname = 'ben'
        ORDER BY 1
        """
    )
    assert {r["tablename"] for r in policies} == set(APP_TABLES_001)
    assert all(r["policyname"] == "tenant_isolation" for r in policies)
    assert all(r["cmd"] == "ALL" for r in policies)
    for r in policies:
        for expr in (r["qual"], r["with_check"]):
            assert "current_setting" in expr
            assert "app.current_org_id" in expr
            assert "org_id" in expr
            assert "::uuid" in expr.replace(" ", "").lower() or "uuid" in expr.lower()
            assert "NULLIF" not in expr


async def _assert_001_constraints_and_rows(conn: asyncpg.Connection) -> None:
    await conn.execute(
        """
        INSERT INTO ben.threads (id, org_id, title)
        VALUES ($1, $2, 'legacy thread')
        """,
        THREAD_ID,
        ORG,
    )
    await conn.execute(
        """
        INSERT INTO ben.messages (id, org_id, thread_id, role, content)
        VALUES ($1, $2, $3, 'user', 'hello')
        """,
        MSG_ID,
        ORG,
        THREAD_ID,
    )
    await conn.execute(
        """
        INSERT INTO ben.cognitive_events (id, org_id, thread_id, type, description)
        VALUES ($1, $2, $3, 'challenge_raised', 'probe')
        """,
        CE_ID,
        ORG,
        THREAD_ID,
    )
    await conn.execute(
        """
        INSERT INTO ben.knowledge_objects
            (id, org_id, type, title, content, confidence, status)
        VALUES ($1, $2, 'problem', 'A', 'plain-text-body', 0.7500, 'active')
        """,
        KO_A,
        ORG,
    )
    await conn.execute(
        """
        INSERT INTO ben.knowledge_objects
            (id, org_id, type, title, content, confidence, status)
        VALUES ($1, $2, 'hypothesis', 'B', 'second-body', 0.1250, 'evolving')
        """,
        KO_B,
        ORG,
    )
    await conn.execute(
        """
        INSERT INTO ben.relationships
            (id, org_id, source_object_id, relation, target_object_id)
        VALUES ($1, $2, $3, 'supports', $4)
        """,
        REL_ID,
        ORG,
        KO_A,
        KO_B,
    )

    with pytest.raises(asyncpg.CheckViolationError):
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO ben.cognitive_events (org_id, thread_id, type, description)
                VALUES ($1, $2, 'not_a_type', 'x')
                """,
                ORG,
                THREAD_ID,
            )

    with pytest.raises(asyncpg.CheckViolationError):
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO ben.knowledge_objects
                    (org_id, type, title, content, confidence, status)
                VALUES ($1, 'synthesis', 'no', 'x', 0.1, 'active')
                """,
                ORG,
            )

    with pytest.raises(asyncpg.NotNullViolationError):
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO ben.knowledge_objects
                    (org_id, type, title, content, confidence, status)
                VALUES ($1, 'problem', 'no', 'x', NULL, 'active')
                """,
                ORG,
            )

    with pytest.raises(asyncpg.CheckViolationError):
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO ben.relationships
                    (org_id, source_object_id, relation, target_object_id)
                VALUES ($1, $2, 'unknown', $3)
                """,
                ORG,
                KO_A,
                KO_B,
            )

    with pytest.raises(asyncpg.ForeignKeyViolationError):
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO ben.messages (org_id, thread_id, role, content)
                VALUES ($1, $2, 'user', 'orphan')
                """,
                ORG,
                uuid.UUID("00000000-0000-0000-0000-000000000099"),
            )

    n = await conn.fetchval("SELECT COUNT(*) FROM ben.knowledge_objects")
    assert n == 2


async def _alembic_heads(conn: asyncpg.Connection) -> list[str]:
    exists = await conn.fetchval(
        "SELECT to_regclass('public.alembic_version') IS NOT NULL"
    )
    if not exists:
        return []
    return [r["version_num"] for r in await conn.fetch("SELECT version_num FROM alembic_version")]


def test_staged_001_lifecycle_then_data_bearing_to_031(pg: DisposablePostgres) -> None:
    # Inherited ambient URLs must not be used.
    os.environ.pop("PGHOST", None)
    os.environ.pop("PGPORT", None)
    os.environ.pop("PGUSER", None)
    os.environ.pop("PGPASSWORD", None)
    os.environ.pop("PGDATABASE", None)

    pg.alembic(STAGED_DB, "upgrade", "001_initial_schema")

    async def phase_a_first_upgrade():
        async with connect(pg, STAGED_DB) as conn:
            assert await _alembic_heads(conn) == ["001_initial_schema"]
            await _assert_001_catalog(conn)

    _run(phase_a_first_upgrade())

    async def phase_a_constraints():
        async with connect(pg, STAGED_DB) as conn:
            await _assert_001_constraints_and_rows(conn)
            # Remove lifecycle probe rows so downgrade/re-upgrade is empty.
            await conn.execute("DELETE FROM ben.relationships")
            await conn.execute("DELETE FROM ben.messages")
            await conn.execute("DELETE FROM ben.cognitive_events")
            await conn.execute("DELETE FROM ben.knowledge_objects")
            await conn.execute("DELETE FROM ben.threads")

    _run(phase_a_constraints())

    pg.alembic(STAGED_DB, "downgrade", "base")

    async def phase_a_after_downgrade():
        async with connect(pg, STAGED_DB) as conn:
            assert await _alembic_heads(conn) == []
            assert await conn.fetchval(
                "SELECT to_regnamespace('ben') IS NULL"
            )
            tables = await conn.fetch(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'ben'"
            )
            assert tables == []

    _run(phase_a_after_downgrade())

    pg.alembic(STAGED_DB, "upgrade", "001_initial_schema")

    async def phase_a_second_upgrade_and_fixture():
        async with connect(pg, STAGED_DB) as conn:
            assert await _alembic_heads(conn) == ["001_initial_schema"]
            await _assert_001_catalog(conn)
            await _assert_001_constraints_and_rows(conn)
            ko = await conn.fetchrow(
                """
                SELECT id, type, title, content, confidence, status
                FROM ben.knowledge_objects WHERE id = $1
                """,
                KO_A,
            )
            assert ko["content"] == "plain-text-body"
            assert ko["type"] == "problem"
            assert ko["confidence"] == Decimal("0.7500")
            rel = await conn.fetchrow(
                "SELECT source_object_id, relation, target_object_id FROM ben.relationships WHERE id = $1",
                REL_ID,
            )
            assert rel["source_object_id"] == KO_A
            assert rel["target_object_id"] == KO_B
            assert rel["relation"] == "supports"

    _run(phase_a_second_upgrade_and_fixture())

    # Fixture must exist before 002.
    pg.alembic(STAGED_DB, "upgrade", "002_ko_synthesis_jsonb")

    async def phase_b_after_002():
        async with connect(pg, STAGED_DB) as conn:
            assert await _alembic_heads(conn) == ["002_ko_synthesis_jsonb"]
            ko = await _column_map(conn, "knowledge_objects")
            assert ko["content"]["udt_name"] == "jsonb"
            assert ko["confidence"]["is_nullable"] == "YES"
            row = await conn.fetchrow(
                """
                SELECT id, type, title, content, pg_typeof(content)::text AS ctype,
                       confidence, status,
                       (content = to_jsonb('plain-text-body'::text)) AS content_converted
                FROM ben.knowledge_objects WHERE id = $1
                """,
                KO_A,
            )
            assert row["id"] == KO_A
            assert row["type"] == "problem"
            assert row["title"] == "A"
            assert row["ctype"] == "jsonb"
            assert row["content_converted"] is True
            assert row["confidence"] == Decimal("0.7500")
            assert row["status"] == "active"
            checks = [
                r["def"]
                for r in await conn.fetch(
                    """
                    SELECT pg_get_constraintdef(c.oid) AS def
                    FROM pg_constraint c
                    JOIN pg_class t ON t.oid = c.conrelid
                    JOIN pg_namespace n ON n.oid = t.relnamespace
                    WHERE n.nspname = 'ben' AND t.relname = 'knowledge_objects'
                      AND c.contype = 'c'
                    """
                )
            ]
            assert any("synthesis" in d and "hypothesis" in d for d in checks)
            await conn.execute(
                """
                INSERT INTO ben.knowledge_objects
                    (org_id, type, title, content, confidence, status)
                VALUES ($1, 'synthesis', 'post-002', to_jsonb('x'::text), NULL, 'active')
                """,
                ORG,
            )
            rel = await conn.fetchrow(
                "SELECT source_object_id, target_object_id FROM ben.relationships WHERE id = $1",
                REL_ID,
            )
            assert rel["source_object_id"] == KO_A
            assert rel["target_object_id"] == KO_B
            thread = await conn.fetchval("SELECT title FROM ben.threads WHERE id = $1", THREAD_ID)
            assert thread == "legacy thread"

    _run(phase_b_after_002())

    pg.alembic(STAGED_DB, "upgrade", HEAD)

    async def phase_b_after_031():
        async with connect(pg, STAGED_DB) as conn:
            assert await _alembic_heads(conn) == [HEAD]
            row = await conn.fetchrow(
                """
                SELECT id, type, content,
                       (content = to_jsonb('plain-text-body'::text)) AS content_ok,
                       confidence
                FROM ben.knowledge_objects WHERE id = $1
                """,
                KO_A,
            )
            assert row["id"] == KO_A
            assert row["type"] == "problem"
            assert row["content_ok"] is True
            assert row["confidence"] == Decimal("0.7500")
            assert await conn.fetchval(
                "SELECT relation FROM ben.relationships WHERE id = $1", REL_ID
            ) == "supports"
            assert await conn.fetchval(
                "SELECT title FROM ben.threads WHERE id = $1", THREAD_ID
            ) == "legacy thread"
            # 029 added source_state; fixture row must still exist.
            src = await conn.fetchval(
                "SELECT source_state FROM ben.threads WHERE id = $1", THREAD_ID
            )
            if isinstance(src, str):
                src = json.loads(src)
            assert src == {}

    _run(phase_b_after_031())


def test_independent_base_to_031(pg: DisposablePostgres) -> None:
    pg.alembic(FRESH_DB, "upgrade", HEAD)

    async def verify():
        async with connect(pg, FRESH_DB) as conn:
            heads = await _alembic_heads(conn)
            assert heads == [HEAD]
            tables = set(await _ben_tables(conn))
            for name in APP_TABLES_001 + (
                "ledger_decisions",
                "ledger_approvals",
                "ledger_actions",
                "ben_log_events",
                "projects",
                "workspace_files",
                "document_processing_jobs",
                "workspace_file_evidence_ir",
                "inference_call_records",
            ):
                assert name in tables

            # 001 must not have pre-created later objects via create_all:
            # 003 named CHECKs + partial/DESC indexes.
            ledger_ck = await conn.fetchval(
                """
                SELECT pg_get_constraintdef(c.oid)
                FROM pg_constraint c
                JOIN pg_class t ON t.oid = c.conrelid
                JOIN pg_namespace n ON n.oid = t.relnamespace
                WHERE n.nspname = 'ben' AND t.relname = 'ledger_decisions'
                  AND c.conname = 'ck_ledger_decisions_status'
                """
            )
            assert ledger_ck is not None
            assert "draft" in ledger_ck and "superseded" in ledger_ck

            idxdef = await conn.fetchval(
                """
                SELECT pg_get_indexdef(i.oid)
                FROM pg_class i
                JOIN pg_namespace n ON n.oid = i.relnamespace
                WHERE n.nspname = 'ben' AND i.relname = 'ix_ledger_decisions_supersedes'
                """
            )
            assert idxdef is not None
            assert "WHERE" in idxdef.upper()
            assert "supersedes_decision_id" in idxdef

            subj = await conn.fetchval(
                """
                SELECT pg_get_indexdef(i.oid)
                FROM pg_class i
                JOIN pg_namespace n ON n.oid = i.relnamespace
                WHERE n.nspname = 'ben' AND i.relname = 'ix_ledger_decisions_org_subject_created'
                """
            )
            assert subj is not None
            assert "DESC" in subj.upper()

            # 002 transformation present on empty 031 db.
            ko = await _column_map(conn, "knowledge_objects")
            assert ko["content"]["udt_name"] == "jsonb"
            assert ko["confidence"]["is_nullable"] == "YES"

            # 029 column exists; created by 029, not 001.
            threads = await _column_map(conn, "threads")
            assert threads["source_state"]["udt_name"] == "jsonb"

            wf = await _column_map(conn, "workspace_files")
            assert wf["extraction_status"]["is_nullable"] == "NO"
            assert wf["initial_read_status"]["is_nullable"] == "NO"

            jobs = await _column_map(conn, "document_processing_jobs")
            assert "runner_eligible" in jobs
            assert jobs["runner_eligible"]["data_type"] == "boolean"

            proj_idx = await conn.fetchval(
                """
                SELECT pg_get_indexdef(i.oid)
                FROM pg_class i
                JOIN pg_namespace n ON n.oid = i.relnamespace
                WHERE n.nspname = 'ben' AND i.relname = 'ix_projects_org_updated_id'
                """
            )
            assert proj_idx is not None
            assert "DESC" in proj_idx.upper()

            role = await conn.fetchrow(
                "SELECT rolname, rolcanlogin FROM pg_roles WHERE rolname = 'ben_doc_processor'"
            )
            assert role is not None
            assert role["rolcanlogin"] is False

            fns = {
                r["proname"]
                for r in await conn.fetch(
                    """
                    SELECT p.proname
                    FROM pg_proc p
                    JOIN pg_namespace n ON n.oid = p.pronamespace
                    WHERE n.nspname = 'ben'
                      AND p.proname IN (
                        'claim_document_processing_jobs',
                        'reap_expired_document_processing_jobs',
                        'claim_file_initial_read_jobs'
                      )
                    """
                )
            }
            assert "claim_document_processing_jobs" in fns
            assert "reap_expired_document_processing_jobs" in fns
            assert "claim_file_initial_read_jobs" in fns

            owner = await conn.fetchval(
                """
                SELECT pg_get_userbyid(p.proowner)
                FROM pg_proc p
                JOIN pg_namespace n ON n.oid = p.pronamespace
                WHERE n.nspname = 'ben' AND p.proname = 'claim_document_processing_jobs'
                LIMIT 1
                """
            )
            assert owner == "ben_doc_processor"

            force = await conn.fetchrow(
                """
                SELECT relrowsecurity, relforcerowsecurity
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'ben' AND c.relname = 'workspace_files'
                """
            )
            assert force["relrowsecurity"] is True
            assert force["relforcerowsecurity"] is True

    _run(verify())
