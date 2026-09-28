"""Real local bytes + disposable PostgreSQL. No provider calls or new schema.

Lost ACK is injected at the transaction response boundary after/before actual
commit, not represented as a real TCP packet-loss test. Disconnect uses actual
pg_terminate_backend while a separate storage thread remains active.
"""
import asyncio
from contextlib import asynccontextmanager, contextmanager
import hashlib
from pathlib import Path
import threading
from types import SimpleNamespace
import uuid
from unittest.mock import patch

from fastapi import HTTPException
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from services.media.attempt_storage import attempt_path, resolve_attempt_key
from services.media import image_storage
from services.media.metadata import request_snapshot, request_fingerprint
from services.media.service import MediaService
from services.media.video_storage import ingest_mp4
from tests.test_veo_media import mp4, REQUEST
from tests.test_media_repository import repository, ORG, OTHER, THREAD


async def ready(repo):
    snapshot = request_snapshot(REQUEST, conversation_id=str(THREAD), workspace_id=None)
    created = await repo.create(ORG, "tester", str(uuid.uuid4()), snapshot, request_fingerprint(snapshot))
    claimed = await repo.claim(ORG, "alpha")
    assert claimed["execution_id"] == created["execution_id"]
    return await repo.record_result(claimed, SimpleNamespace(operation_ref="synthetic-completed", usage={}), {})


def service(repo):
    # No live provider adapters needed, even if developer credentials exist.
    inert = object()
    return MediaService(repo, inert, inert, inert, inert)


async def expire(admin, row):
    await admin.execute("UPDATE ben.media_executions SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE execution_id=$1", row["execution_id"])


@pytest.mark.asyncio
async def test_interrupted_disk_write_preserves_authoritative_bytes(repository, tmp_path, monkeypatch):
    repo, _ = repository
    monkeypatch.setenv("BEN_PROJECTS_DATA_DIR", str(tmp_path))
    row = await ready(repo)
    svc = service(repo)
    data = mp4()
    completed = await svc.publish_video_attempt(row, data, attempt_id=uuid.uuid4())
    original_key = completed["storage_key"]
    failed_attempt = uuid.uuid4()
    temporary_names = []
    original_temp = image_storage.tempfile.NamedTemporaryFile

    @contextmanager
    def failing_temp(*args, **kwargs):
        with original_temp(*args, **kwargs) as handle:
            temporary_names.append(Path(handle.name))
            class PartialWriter:
                name = handle.name
                def write(self, payload):
                    handle.write(payload[:128])
                    handle.flush()
                    raise OSError("injected partial write")
            yield PartialWriter()

    with patch.object(image_storage.tempfile, "NamedTemporaryFile", failing_temp):
        with pytest.raises(OSError, match="injected partial write"):
            ingest_mp4(mp4(audio=False), org_id=ORG, resource_id=row["resource_id"],
                       execution_id=row["execution_id"], attempt_id=failed_attempt)
    assert temporary_names and all(not p.exists() for p in temporary_names)
    assert not attempt_path(ORG, row["resource_id"], row["execution_id"], failed_attempt)[1].exists()
    assert await svc.resource_bytes(ORG, "tester", row["resource_id"]) == data
    assert (await repo.read(ORG, "tester", execution=row["execution_id"]))["storage_key"] == original_key


@pytest.mark.asyncio
async def test_database_disconnect_while_write_continues_beta_wins(repository, tmp_path, monkeypatch):
    repo, admin = repository
    monkeypatch.setenv("BEN_PROJECTS_DATA_DIR", str(tmp_path))
    alpha = await ready(repo)
    svc = service(repo)
    attempt_alpha, attempt_beta = uuid.uuid4(), uuid.uuid4()
    alpha_data, beta_data = mp4(), mp4(audio=False)
    entered, release = threading.Event(), threading.Event()
    original_sync = image_storage._fsync_file_and_dir

    def pause_after_write(handle, parent):
        # Only Alpha's staging file; Beta can publish independently.
        if str(alpha["execution_id"]) in str(parent):
            # Filenames carry attempt IDs, while the staging parent is shared.
            # Gate only the first call, before Beta starts.
            if not entered.is_set():
                entered.set()
                assert release.wait(15), "test writer barrier timed out"
        return original_sync(handle, parent)

    monkeypatch.setattr(image_storage, "_fsync_file_and_dir", pause_after_write)
    writer = None
    try:
        with pytest.raises(DBAPIError):
            async with repo.transaction(ORG) as worker_session:
                backend = await worker_session.scalar(text("SELECT pg_backend_pid()"))
                connection = await worker_session.connection()
                raw = await connection.get_raw_connection()
                writer = asyncio.create_task(svc.publish_video_attempt(alpha, alpha_data, attempt_id=attempt_alpha))
                assert await asyncio.to_thread(entered.wait, 10)
                assert backend != await admin.fetchval("SELECT pg_backend_pid()")
                assert await admin.fetchval("SELECT pg_terminate_backend($1)", backend)
                for _ in range(200):
                    if raw.driver_connection.is_closed():
                        break
                    await asyncio.sleep(0.01)
                assert raw.driver_connection.is_closed()
                # Disconnect alone did not revoke the committed lease.
                assert await repo.claim(ORG, "beta") is None
                await expire(admin, alpha)
                beta = await repo.claim(ORG, "beta")
                assert beta["version"] > alpha["version"]
                won = await svc.publish_video_attempt(beta, beta_data, attempt_id=attempt_beta)
                assert won["state"] == "succeeded"
                release.set()
                assert await asyncio.wait_for(writer, 10) is None
                await worker_session.execute(text("SELECT 1"))
    finally:
        release.set()
        if writer is not None and not writer.done():
            await asyncio.wait_for(writer, 10)
    # The stale file may exist, but cannot block or replace the winning bytes.
    assert attempt_path(ORG, alpha["resource_id"], alpha["execution_id"], attempt_alpha)[1].read_bytes() == alpha_data
    row, delivered = await svc.read_video_attempt_outcome(ORG, "tester", alpha["execution_id"])
    assert delivered == beta_data
    assert row["storage_key"] == attempt_path(ORG, alpha["resource_id"], alpha["execution_id"], attempt_beta)[0]
    assert row["resource_id"] == alpha["resource_id"]
    assert row["request_payload"] == alpha["request_payload"]
    for org, user in ((OTHER, "tester"), (ORG, "other-user")):
        with pytest.raises(HTTPException) as exc:
            await svc.resource_bytes(org, user, row["resource_id"])
        assert exc.value.status_code == 404
    winning_path = resolve_attempt_key(ORG, row["resource_id"], row["execution_id"], row["storage_key"])
    # Same-size corruption must still fail the protected delivery checksum gate.
    winning_path.write_bytes(bytes([beta_data[0] ^ 1]) + beta_data[1:])
    with pytest.raises(HTTPException) as exc:
        await svc.resource_bytes(ORG, "tester", row["resource_id"])
    assert exc.value.status_code == 503


@pytest.mark.asyncio
@pytest.mark.parametrize("committed", [True, False])
async def test_lost_commit_response_read_resolves_without_rewrite(repository, tmp_path, monkeypatch, committed):
    repo, _ = repository
    monkeypatch.setenv("BEN_PROJECTS_DATA_DIR", str(tmp_path))
    row = await ready(repo)
    svc = service(repo)
    attempt = uuid.uuid4()
    original_complete, original_transaction = repo.complete_attempt, repo.transaction

    @asynccontextmanager
    async def uncertain_transaction(org):
        async with original_transaction(org) as session:
            yield session
            if not committed:
                raise ConnectionError("injected before commit")
        raise ConnectionError("injected lost commit response")

    async def uncertain_complete(*args):
        with patch.object(repo, "transaction", uncertain_transaction):
            return await original_complete(*args)

    with patch.object(repo, "complete_attempt", uncertain_complete):
        with pytest.raises(ConnectionError):
            await svc.publish_video_attempt(row, mp4(), attempt_id=attempt)
    path = attempt_path(ORG, row["resource_id"], row["execution_id"], attempt)[1]
    before = (path.stat().st_mtime_ns, path.read_bytes())
    with patch.object(repo, "claim", side_effect=AssertionError("must not claim")), \
         patch.object(image_storage, "publish_bytes", side_effect=AssertionError("must not write")), \
         patch("services.media.service.ingest_mp4", side_effect=AssertionError("must not ingest")):
        outcome = await svc.read_video_attempt_outcome(ORG, "tester", row["execution_id"])
    if committed:
        assert outcome[0]["state"] == "succeeded" and outcome[1] == mp4()
        assert outcome[0]["checksum"] == hashlib.sha256(mp4()).hexdigest()
    else:
        assert outcome is None
        current = await repo.read(ORG, "tester", execution=row["execution_id"])
        assert current["state"] == "ingesting" and current["storage_key"] is None
        assert current["version"] == row["version"]
    assert (path.stat().st_mtime_ns, path.read_bytes()) == before


@pytest.mark.asyncio
async def test_expired_owner_and_deleted_stored_destination_cannot_publish(repository, tmp_path, monkeypatch):
    repo, admin = repository
    monkeypatch.setenv("BEN_PROJECTS_DATA_DIR", str(tmp_path))
    row = await ready(repo)
    stored = ingest_mp4(mp4(), org_id=ORG, resource_id=row["resource_id"],
                        execution_id=row["execution_id"], attempt_id=uuid.uuid4())
    await expire(admin, row)
    assert await repo.complete_attempt(row, stored) is None
    beta = await repo.claim(ORG, "beta")
    other_thread = uuid.uuid4()
    await admin.execute("INSERT INTO ben.threads VALUES ($1,$2)", other_thread, ORG)
    await admin.execute("DELETE FROM ben.threads WHERE id=$1", THREAD)
    beta["conversation_id"] = str(other_thread)  # Forged caller snapshot must not authorize.
    with pytest.raises(HTTPException) as exc:
        await repo.complete_attempt(beta, stored)
    assert exc.value.status_code == 404


@pytest.mark.parametrize("tamper", ["other_org", "other_resource", "other_execution", "traversal", "absolute", "backslash"])
def test_attempt_key_is_bound_to_org_resource_execution(tmp_path, monkeypatch, tamper):
    monkeypatch.setenv("BEN_PROJECTS_DATA_DIR", str(tmp_path))
    org, resource, execution, attempt = (uuid.uuid4() for _ in range(4))
    key, _ = attempt_path(org, resource, execution, attempt)
    if tamper == "other_org": key = key.replace(str(org), str(uuid.uuid4()))
    elif tamper == "other_resource": key = key.replace(str(resource), str(uuid.uuid4()))
    elif tamper == "other_execution": key = key.replace(str(execution), str(uuid.uuid4()))
    elif tamper == "traversal": key = "../" + key
    elif tamper == "absolute": key = "/" + key
    else: key = key.replace("/", "\\")
    with pytest.raises(ValueError):
        resolve_attempt_key(org, resource, execution, key)
