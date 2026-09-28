"""Existing infrastructure only; NOT proof of narration or attempt-path fencing.

The PostgreSQL case uses the native disposable-database fixture. It terminates
only the backend opened by this test, never an arbitrary database connection.
"""
import hashlib
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.test_media_repository import repository, create, ORG


def test_real_mp4_persisted_bytes_and_conflicting_publication(tmp_path, monkeypatch):
    pytest.importorskip("av", reason="PyAV required for actual MP4 validation")
    from services.media.video_storage import ingest_mp4, video_path
    from tests.test_veo_media import mp4
    monkeypatch.setenv("BEN_PROJECTS_DATA_DIR", str(tmp_path))
    org, resource = uuid.uuid4(), uuid.uuid4()
    data = mp4()
    stored = ingest_mp4(data, org_id=org, resource_id=resource)
    persisted = video_path(org, resource)[1].read_bytes()
    assert persisted == data
    assert stored.byte_size == len(persisted)
    assert stored.checksum == hashlib.sha256(persisted).hexdigest()
    assert ingest_mp4(data, org_id=org, resource_id=resource) == stored
    with pytest.raises(ValueError, match="media video publication failed"):
        ingest_mp4(mp4(audio=False), org_id=org, resource_id=resource)
    assert video_path(org, resource)[1].read_bytes() == data


@pytest.mark.asyncio
async def test_real_disconnect_then_takeover_rejects_stale_owner(repository):
    """Baseline DB fencing with an existing operation, not the future local worker."""
    repo, admin = repository
    execution = await create(repo)
    alpha = await repo.claim(ORG, "local-proof-alpha")
    assert alpha["execution_id"] == execution["execution_id"]
    with pytest.raises(DBAPIError):
        async with repo.transaction(ORG) as session:
            backend = await session.scalar(text("SELECT pg_backend_pid()"))
            assert backend != await admin.fetchval("SELECT pg_backend_pid()")
            assert await admin.fetchval("SELECT pg_terminate_backend($1)", backend)
            await session.execute(text("SELECT 1"))
    # Disconnect does not undo the lease committed by claim().
    assert await repo.claim(ORG, "local-proof-beta") is None
    # Deterministic expiry injection, separate from the real disconnect above.
    await admin.execute(
        "UPDATE ben.media_executions SET lease_expires_at=clock_timestamp()-interval '1 second' "
        "WHERE execution_id=$1", execution["execution_id"])
    beta = await repo.claim(ORG, "local-proof-beta")
    assert beta["version"] > alpha["version"]
    assert await repo.change(alpha, state="failed") is None
    assert (await repo.read(ORG, "tester", execution=execution["execution_id"]))["lease_owner"] == "local-proof-beta"
