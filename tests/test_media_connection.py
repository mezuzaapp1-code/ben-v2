"""Production media must never inherit an administrator connection."""
from contextlib import asynccontextmanager
import os
import uuid

from fastapi import HTTPException
import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool

from services.media.connection import media_sessions, get_media_session
from services.media.repository import MediaRepository
from tests.test_media_repository import repository, ORG


@pytest.mark.parametrize("url", [None, "", "sqlite:///media.db"])
def test_media_has_no_global_database_fallback(monkeypatch, url):
    media_sessions.cache_clear()
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://admin:secret@localhost/main")
    if url is None:
        monkeypatch.delenv("BEN_MEDIA_DATABASE_URL", raising=False)
    else:
        monkeypatch.setenv("BEN_MEDIA_DATABASE_URL", url)
    with pytest.raises(HTTPException) as error:
        media_sessions()
    assert error.value.status_code == 503
    assert MediaRepository().sessions is get_media_session


@pytest.mark.asyncio
@pytest.mark.parametrize("role_kind", ["superuser", "bypassrls"])
async def test_unsafe_runtime_role_rejected_before_tenant_work(repository, role_kind):
    _, admin = repository
    role = "unsafe_media_" + uuid.uuid4().hex
    await admin.execute(f"CREATE ROLE {role} NOLOGIN " +
                        ("SUPERUSER" if role_kind == "superuser" else "NOSUPERUSER BYPASSRLS"))
    url = os.environ["MEDIA_TEST_DATABASE_URL"].replace("postgresql://", "postgresql+asyncpg://", 1)
    engine = create_async_engine(url, poolclass=NullPool,
                                 connect_args={"server_settings": {"role": role}})
    sessions = async_sessionmaker(engine)
    @asynccontextmanager
    async def session():
        async with sessions() as value:
            yield value
    try:
        with pytest.raises(HTTPException) as error:
            async with MediaRepository(session).transaction(ORG):
                pytest.fail("Unsafe role reached tenant work")
        assert error.value.status_code == 503
        assert await admin.fetchval("SELECT count(*) FROM ben.media_executions") == 0
    finally:
        await engine.dispose()
        await admin.execute(f"DROP ROLE {role}")
