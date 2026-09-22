"""033 DDL and real PostgreSQL tests; never accepts an application DATABASE_URL.

Integration requires MEDIA_TEST_DATABASE_URL for an empty loopback database
named media_v1_test_*. All DDL/data/role changes are rolled back after each test.
Run with a disposable cluster administrator, not production credentials.
"""
from __future__ import annotations

import importlib.util
import io
import os
from pathlib import Path
from urllib.parse import urlparse
import uuid

import asyncpg
from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
import pytest_asyncio

ORG_A = uuid.UUID('11111111-1111-4111-8111-111111111111')
ORG_B = uuid.UUID('22222222-2222-4222-8222-222222222222')


def migration_sql(direction='upgrade'):
    path = Path(__file__).resolve().parents[1] / 'database/migrations/versions/033_media_executions.py'
    spec = importlib.util.spec_from_file_location('media_migration', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    output = io.StringIO()
    ctx = MigrationContext.configure(dialect_name='postgresql', opts={'as_sql': True, 'output_buffer': output})
    with Operations.context(ctx):
        getattr(module, direction)()
    return output.getvalue()


def test_ddl_is_single_additive_table_and_forced_tenant_policy():
    sql = migration_sql()
    assert sql.count('CREATE TABLE ') == 1
    assert 'CREATE TABLE ben.media_executions' in sql
    assert 'FORCE ROW LEVEL SECURITY' in sql
    assert 'WITH CHECK' in sql and 'USING' in sql
    for protected in ('workspace_files', 'document_processing_jobs', 'inference_call_records', 'execution_events'):
        assert protected not in sql
    assert migration_sql('downgrade').strip() == 'DROP TABLE ben.media_executions;'


@pytest_asyncio.fixture
async def db():
    url = os.getenv('MEDIA_TEST_DATABASE_URL')
    if not url:
        pytest.skip('real PostgreSQL required: MEDIA_TEST_DATABASE_URL not configured')
    parsed = urlparse(url)
    if parsed.hostname not in ('127.0.0.1', 'localhost', '::1') or not parsed.path.startswith('/media_v1_test_'):
        pytest.fail('refusing non-disposable/non-loopback database', pytrace=False)
    conn = await asyncpg.connect(url, timeout=10)
    tx = conn.transaction()
    await tx.start()
    try:
        assert await conn.fetchval("SELECT to_regnamespace('ben')") is None, 'test database must not contain BEN schema'
        await conn.execute('CREATE SCHEMA ben')
        await conn.execute(migration_sql())
        # Random, transaction-local role identity; no existing roles are altered.
        role = 'media_test_' + uuid.uuid4().hex
        await conn.execute(f'CREATE ROLE {role} NOLOGIN NOSUPERUSER NOBYPASSRLS')
        await conn.execute(f'GRANT USAGE ON SCHEMA ben TO {role}')
        await conn.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON ben.media_executions TO {role}')
        yield conn, role
    finally:
        await tx.rollback()
        await conn.close()


async def insert(conn, org, key='request-1', resource=None):
    execution = uuid.uuid4()
    await conn.execute("""INSERT INTO ben.media_executions
        (execution_id,org_id,created_by,conversation_id,idempotency_key,request_fingerprint,
         request_payload,provider,model,operation,deadline_at,resource_id)
        VALUES ($1,$2,'test-user','conversation-1',$3,$4,'{}','test-provider','test-model',
                'image_generation',now()+interval '1 hour',$5)""",
        execution, org, key, 'a'*64, resource or uuid.uuid4())
    return execution


async def tenant(conn, role, org):
    await conn.execute(f'SET LOCAL ROLE {role}')
    await conn.execute("SELECT set_config('app.current_org_id',$1,true)", str(org) if org else '')


@pytest.mark.asyncio
async def test_rls_read_update_delete_and_missing_context(db):
    conn, role = db
    a = await insert(conn, ORG_A)
    await insert(conn, ORG_B)
    await tenant(conn, role, ORG_A)
    assert await conn.fetchval('SELECT count(*) FROM ben.media_executions') == 1
    assert await conn.fetchval('SELECT execution_id FROM ben.media_executions') == a
    assert await conn.execute('UPDATE ben.media_executions SET error_code=\'x\' WHERE org_id=$1', ORG_B) == 'UPDATE 0'
    assert await conn.execute('DELETE FROM ben.media_executions WHERE org_id=$1', ORG_B) == 'DELETE 0'
    await tenant(conn, role, None)
    assert await conn.fetchval('SELECT count(*) FROM ben.media_executions') == 0


@pytest.mark.asyncio
async def test_rls_insert_and_owner_transfer_denied(db):
    conn, role = db
    await tenant(conn, role, ORG_A)
    a = await insert(conn, ORG_A)
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with conn.transaction():
            await insert(conn, ORG_B, 'forbidden')
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with conn.transaction():
            await conn.execute('UPDATE ben.media_executions SET org_id=$1 WHERE execution_id=$2', ORG_B, a)
    await tenant(conn, role, None)
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with conn.transaction():
            await insert(conn, ORG_A, 'no-context')


@pytest.mark.asyncio
async def test_idempotency_and_single_resource_uniqueness(db):
    conn, _ = db
    resource = uuid.uuid4()
    await insert(conn, ORG_A, resource=resource)
    await insert(conn, ORG_B)  # Same key allowed in another organization.
    with pytest.raises(asyncpg.UniqueViolationError):
        async with conn.transaction():
            await insert(conn, ORG_A)
    with pytest.raises(asyncpg.UniqueViolationError):
        async with conn.transaction():
            await insert(conn, ORG_A, key='new-request', resource=resource)


@pytest.mark.asyncio
async def test_lifecycle_checks_and_projectless_destination(db):
    conn, _ = db
    a = await insert(conn, ORG_A)
    assert await conn.fetchval('SELECT workspace_id FROM ben.media_executions WHERE execution_id=$1', a) is None
    for change in ("state='bogus'", "state='succeeded'", "state='submitted'", "poll_attempts=-1",
                   "lease_owner='orphan'", "conversation_id=NULL", "byte_size=0", "estimated_cost=-1"):
        with pytest.raises(asyncpg.CheckViolationError):
            async with conn.transaction():
                await conn.execute(f'UPDATE ben.media_executions SET {change} WHERE execution_id=$1', a)
    await conn.execute("""UPDATE ben.media_executions SET state='succeeded',storage_key='private/test',
        mime_type='image/png',byte_size=10,checksum=$1,published_at=now() WHERE execution_id=$2""", 'b'*64, a)


@pytest.mark.asyncio
async def test_catalog_and_downgrade_reupgrade(db):
    conn, _ = db
    flags = await conn.fetchrow("SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid='ben.media_executions'::regclass")
    assert all(flags.values())
    indexes = {r['indexname']: r['indexdef'] for r in await conn.fetch("SELECT indexname,indexdef FROM pg_indexes WHERE schemaname='ben'")}
    assert 'WHERE' in indexes['ix_media_due'] and 'submission_unknown' in indexes['ix_media_due']
    assert 'UNIQUE' in indexes['uq_media_org_idempotency']
    assert 'UNIQUE' in indexes['uq_media_resource']
    await conn.execute(migration_sql('downgrade'))
    assert await conn.fetchval("SELECT to_regclass('ben.media_executions')") is None
    await conn.execute(migration_sql())
    assert await conn.fetchval("SELECT to_regclass('ben.media_executions')") is not None
