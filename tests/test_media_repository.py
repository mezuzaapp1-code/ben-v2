"""Real PostgreSQL gate: locks, fencing, idempotency, tenant RLS and publication.

Only a fresh empty loopback MEDIA_TEST_DATABASE_URL named media_v1_test_* is
accepted. Never reads ambient DATABASE_URL. Runtime sessions SET ROLE to a
non-superuser without BYPASSRLS. Missing environment is a skip, not a pass.
"""
import asyncio
from contextlib import asynccontextmanager
import json
import os
from urllib.parse import urlparse
import uuid

import asyncpg
from fastapi import HTTPException
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool

from services.media.metadata import request_snapshot, request_fingerprint
from services.media.repository import MediaRepository
from tests.test_media_image_foundation import REQUEST
from tests.test_media_migration import migration_sql

ORG, OTHER, THREAD = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


@pytest_asyncio.fixture
async def repository():
    url = os.getenv("MEDIA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("disposable PostgreSQL required for media runtime gate")
    parsed = urlparse(url)
    if parsed.hostname not in ("localhost", "127.0.0.1", "::1") or not parsed.path.startswith("/media_v1_test_"):
        pytest.fail("refusing non-disposable database", pytrace=False)
    admin = await asyncpg.connect(url, timeout=10)
    assert await admin.fetchval("SELECT to_regnamespace('ben')") is None
    role = "media_runtime_" + uuid.uuid4().hex
    engine = None
    try:
        await admin.execute("CREATE SCHEMA ben")
        await admin.execute("CREATE TABLE ben.threads (id uuid PRIMARY KEY, org_id uuid NOT NULL)")
        await admin.execute("INSERT INTO ben.threads VALUES ($1,$2)", THREAD, ORG)
        await admin.execute(migration_sql())
        await admin.execute(f"CREATE ROLE {role} NOLOGIN NOSUPERUSER NOBYPASSRLS")
        await admin.execute(f"GRANT USAGE ON SCHEMA ben TO {role}")
        await admin.execute(f"GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA ben TO {role}")
        engine = create_async_engine(url.replace("postgresql://", "postgresql+asyncpg://", 1),
                                     poolclass=NullPool, echo=False,
                                     connect_args={"server_settings": {"role": role}})
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        @asynccontextmanager
        async def session():
            async with sessions() as s:
                yield s
        repo = MediaRepository(session)
        async with repo.transaction(ORG) as s:
            assert await s.scalar(text("SELECT current_user")) == role
            assert not await s.scalar(text("SELECT rolbypassrls OR rolsuper FROM pg_roles WHERE rolname=current_user"))
        yield repo, admin
    finally:
        if engine:
            await engine.dispose()
        # This fixture proved this dedicated database had no BEN schema before setup.
        await admin.execute("DROP SCHEMA ben CASCADE")
        await admin.execute(f"DROP ROLE IF EXISTS {role}")
        await admin.close()


async def create(repo, key="same", org=ORG, user="tester", conversation=THREAD, prompt=None):
    from dataclasses import replace
    snapshot = request_snapshot(replace(REQUEST, prompt=prompt) if prompt else REQUEST,
                                conversation_id=str(conversation), workspace_id=None)
    return await repo.create(org, user, key, snapshot, request_fingerprint(snapshot))


@pytest.mark.asyncio
async def test_concurrent_admission_one_execution_and_fingerprint_conflict(repository):
    repo, admin = repository
    rows = await asyncio.gather(*(create(repo) for _ in range(8)))
    assert len({r["execution_id"] for r in rows}) == 1
    assert await admin.fetchval("SELECT count(*) FROM ben.media_executions") == 1
    for kwargs in ({"prompt": "different"}, {"user": "other-user"}):
        with pytest.raises(HTTPException) as error:
            await create(repo, **kwargs)
        assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_tenant_principal_and_destination_isolation(repository):
    repo, _ = repository
    row = await create(repo)
    for org, user in ((OTHER, "tester"), (ORG, "someone-else")):
        with pytest.raises(HTTPException) as error:
            await repo.read(org, user, execution=row["execution_id"])
        assert error.value.status_code == 404
    with pytest.raises(HTTPException):
        await create(repo, org=OTHER)
    with pytest.raises(HTTPException):
        await create(repo, conversation=uuid.uuid4())
    async with repo.transaction(OTHER) as s:
        assert await s.scalar(text("SELECT count(*) FROM ben.media_executions")) == 0


@pytest.mark.asyncio
async def test_claim_concurrency_and_stale_fence(repository):
    repo, admin = repository
    await create(repo)
    claims = await asyncio.gather(*(repo.claim(ORG, str(i)) for i in range(8)))
    claimed = [r for r in claims if r]
    assert len(claimed) == 1
    first = await repo.mark_submitting(claimed[0])
    assert first["submit_attempts"] == 1
    await admin.execute("UPDATE ben.media_executions SET lease_expires_at=now()-interval '1 second'")
    second = await repo.claim(ORG, "restart")
    assert second["state"] == "submitting"
    assert await repo.change(first, state="failed") is None
    assert await repo.mark_submitting(second) is None
    await repo.change(second, state="submission_unknown")


@pytest.mark.asyncio
async def test_org_scoped_key_and_limit(repository):
    repo, admin = repository
    other_thread = uuid.uuid4()
    await admin.execute("INSERT INTO ben.threads VALUES ($1,$2)", other_thread, OTHER)
    first, second = await create(repo), await create(repo, org=OTHER, conversation=other_thread)
    assert first["execution_id"] != second["execution_id"]
    for i in range(19):
        await create(repo, key=f"extra-{i}")
    with pytest.raises(HTTPException) as error:
        await create(repo, key="over-limit")
    assert error.value.status_code == 429
    assert (await create(repo))["execution_id"] == first["execution_id"]


@pytest.mark.asyncio
async def test_late_publication_destination_deletion_and_evaluation(repository):
    repo, admin = repository
    await create(repo)
    row = await repo.claim(ORG, "worker")
    from datetime import datetime, timezone
    values = dict(state="succeeded", storage_key="private/output.png", byte_size=12,
                  mime_type="image/png", checksum="a"*64, published_at=datetime.now(timezone.utc))
    completed = await repo.change(row, **values)
    await repo.evaluate(ORG, "tester", row["execution_id"], {"training_status": "not_approved", "acceptance": "accepted"})
    read = await repo.read(ORG, "tester", execution=row["execution_id"])
    assert read["provider_output"]["evaluation"]["training_status"] == "not_approved"
    await admin.execute("DELETE FROM ben.threads WHERE id=$1", THREAD)
    with pytest.raises(HTTPException):
        await repo.read(ORG, "tester", resource=completed["resource_id"])
    with pytest.raises(HTTPException):
        await repo.change(row, **values)
