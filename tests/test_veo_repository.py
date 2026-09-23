"""Real PostgreSQL lifecycle and protected delivery with ONLY mocked Veo transport."""
import asyncio
from dataclasses import replace
import hashlib
import json
import uuid

import httpx
from fastapi import FastAPI, HTTPException
import pytest
import pytest_asyncio
from sqlalchemy import text

from auth.beta_gate import derive_beta_org_id
from routers.media import router, media_service
from services.media.contracts import VideoRequest, VEO_VIDEO_MODEL
from services.media.image_storage import ingest_png
from services.media.service import MediaService
from services.media.veo_video import VeoVideoAdapter
from services.media.metadata import request_snapshot, request_fingerprint
from tests.test_media_repository import repository, THREAD
from tests.test_media_image_foundation import REQUEST as IMAGE_REQUEST, png
from tests.test_veo_media import OP, POLL, FILE, KEY, REQUEST, response, completed, mp4

USER = "veo-internal-test"
ORG = derive_beta_org_id(USER)
HEADERS = {"X-Basalt-Beta-Alias": USER, "X-Basalt-Beta-Passcode": "test-only-passcode"}


@pytest_asyncio.fixture
async def context(repository, monkeypatch, tmp_path):
    repo, admin = repository
    await admin.execute("UPDATE ben.threads SET org_id=$1 WHERE id=$2", ORG, THREAD)
    monkeypatch.setenv("BEN_PROJECTS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("BEN_MEDIA_INTERNAL_ENABLED", "1")
    monkeypatch.setenv("BEN_MEDIA_VEO_ENABLED", "1")
    monkeypatch.setenv("BEN_MEDIA_INTERNAL_PRINCIPALS", json.dumps([{"org_id": str(ORG), "user_id": USER},
        {"org_id": str(derive_beta_org_id("other")), "user_id": "other"}]))
    monkeypatch.setenv("BEN_LOCAL_BETA_MODE", "1")
    monkeypatch.setenv("BEN_BETA_PASSCODE", "test-only-passcode")
    # Seed a synthetic first-frame BEN resource, without calling either image provider.
    snapshot = request_snapshot(IMAGE_REQUEST, conversation_id=str(THREAD), workspace_id=None)
    source = await repo.create(ORG, USER, "synthetic-source", snapshot, request_fingerprint(snapshot))
    stored = ingest_png(png(), org_id=ORG, resource_id=source["resource_id"])
    await admin.execute("""UPDATE ben.media_executions SET state='succeeded', storage_key=$2,
        mime_type='image/png',byte_size=$3,checksum=$4,published_at=now() WHERE execution_id=$1""",
        source["execution_id"], stored.storage_key, stored.byte_size, stored.checksum)
    yield repo, admin, replace(REQUEST, source_resource_id=str(source["resource_id"]))


async def due(admin):
    await admin.execute("UPDATE ben.media_executions SET next_reconcile_at=now()-interval '1 second'")


class NoImage:
    async def generate(self, *_):
        pytest.fail("cross-provider fallback")
    async def submit(self, *_):
        pytest.fail("cross-provider fallback")


def service_for(repo, handler):
    return MediaService(repo, NoImage(), NoImage(), VeoVideoAdapter(KEY, transport=httpx.MockTransport(handler)))


async def admit(service, request, key="veo-proof"):
    return await service.create(ORG, USER, key, THREAD, request)


@pytest.mark.asyncio
async def test_complete_video_path_restart_idempotency_rls_and_protected_delivery(context):
    repo, admin, request = context
    calls, polls = [], 0
    def handler(req):
        nonlocal polls
        calls.append(req.method)
        if req.method == "POST":
            return response({"name": OP})
        if str(req.url) == POLL:
            polls += 1
            return response({"name": OP} if polls == 1 else completed())
        return httpx.Response(200, content=mp4())
    service = service_for(repo, handler)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[media_service] = lambda: service
    body = {**request.__dict__, "conversation_id": str(THREAD), "idempotency_key": "veo-proof"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://ben") as client:
        assert (await client.post("/api/media/executions", json=body)).status_code == 401
        submitted = await client.post("/api/media/executions", json=body, headers=HEADERS)
        assert submitted.status_code == 202
        execution = uuid.UUID(submitted.json()["execution_id"])
        assert (await client.post("/api/media/executions", json=body, headers=HEADERS)).json() == submitted.json()
        row = await repo.read(ORG, USER, execution=execution)
        assert row["state"] == "pending" and row["operation"] == "image_to_video" and not calls
        assert row["request_payload"]["input_resource_refs"][0]["checksum"] == hashlib.sha256(png()).hexdigest()
        await asyncio.gather(service.tick(ORG), service.tick(ORG))
        row = await repo.read(ORG, USER, execution=execution)
        assert row["state"] == "submitted" and row["provider_operation_ref"] == OP
        service = service_for(repo, handler)  # Real restart, only DB survives.
        await due(admin)
        await service.tick(ORG)
        assert (await repo.read(ORG, USER, execution=execution))["state"] == "running"
        await due(admin)
        await service_for(repo, handler).tick(ORG)
        row = await repo.read(ORG, USER, execution=execution)
        assert row["state"] == "succeeded" and row["model"] == VEO_VIDEO_MODEL and row["provider"] == "google"
        assert row["submit_attempts"] == calls.count("POST") == 1 and row["poll_attempts"] == 2
        assert row["mime_type"] == "video/mp4" and row["storage_key"].endswith("output.mp4")
        assert str(row["estimated_cost"]) == "0.40000000" and row["actual_charge"] is None
        assert row["usage_dimensions"]["duration_seconds"] == 4 and row["usage_dimensions"]["provider_usage"] is None
        assert row["provider_output"]["model_identity_source"] == "exact_dispatch_endpoint"
        assert row["provider_output"]["training_status"] == "not_approved"
        listing = await client.get(f"/api/media/executions?conversation_id={THREAD}", headers=HEADERS)
        assert all(secret not in listing.text for secret in (KEY, OP, FILE, POLL, row["storage_key"]))
        public = next(r for r in listing.json()["executions"] if r["execution_id"] == str(execution))
        assert public["mime_type"] == "video/mp4" and public["resource_id"] == str(row["resource_id"])
        path = f"/api/media/resources/{row['resource_id']}/content"
        output = await client.get(path, headers=HEADERS)
        assert output.status_code == 200 and output.content == mp4()
        assert output.headers["content-type"] == "video/mp4" and output.headers["cache-control"] == "private, no-store"
        assert (await client.get(path)).status_code == 401
        assert (await client.get(path, headers={**HEADERS, "X-Basalt-Beta-Alias": "other"})).status_code == 404
        async with repo.transaction(derive_beta_org_id("other")) as session:
            assert await session.scalar(text("SELECT count(*) FROM ben.media_executions")) == 0
        assert (await admit(service, request))["execution_id"] == execution
        assert not await service.tick(ORG)
        assert calls.count("POST") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["rejected", "uncertain", "missing-reference", "crash-after-acceptance"])
async def test_no_second_post_after_submission_failure_or_crash(context, failure):
    repo, admin, request = context
    calls = []
    def handler(req):
        calls.append(req.method)
        if failure == "uncertain":
            raise httpx.ReadTimeout(KEY)
        if failure == "crash-after-acceptance":
            raise asyncio.CancelledError()
        return response({} if failure == "missing-reference" else {"error": "rejected"}, 400 if failure == "rejected" else 200)
    service = service_for(repo, handler)
    initial = await admit(service, request)
    if failure == "crash-after-acceptance":
        with pytest.raises(asyncio.CancelledError):
            await service.tick(ORG)
        await admin.execute("UPDATE ben.media_executions SET lease_expires_at=now()-interval '1 second' WHERE execution_id=$1", initial["execution_id"])
    else:
        await service.tick(ORG)
    await due(admin)
    await service_for(repo, handler).tick(ORG)
    row = await repo.read(ORG, USER, execution=initial["execution_id"])
    assert row["state"] == ("failed" if failure == "rejected" else "submission_unknown")
    assert row["submit_attempts"] == 1 and calls == ["POST"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["provider-error", "expired", "malformed", "poll-transport", "deadline"])
async def test_poll_failure_and_expiry_never_publish_or_resubmit(context, failure):
    repo, admin, request = context
    calls = []
    def handler(req):
        calls.append(req.method)
        if req.method == "POST":
            return response({"name": OP})
        if failure == "poll-transport":
            raise httpx.ReadTimeout(KEY)
        if failure == "expired":
            return response({}, 410)
        if failure == "malformed":
            return response({"name": OP, "done": True})
        return response({"name": OP, "done": True, "error": {"code": 13, "message": KEY}})
    initial = await admit(service_for(repo, handler), request)
    await service_for(repo, handler).tick(ORG)
    if failure == "deadline":
        await admin.execute("UPDATE ben.media_executions SET created_at=now()-interval '1 hour', deadline_at=now()-interval '1 second' WHERE execution_id=$1", initial["execution_id"])
    await due(admin)
    await service_for(repo, handler).tick(ORG)
    row = await repo.read(ORG, USER, execution=initial["execution_id"])
    expected = "expired" if failure in ("expired", "deadline") else "failed" if failure == "provider-error" else "submitted"
    assert row["state"] == expected and row["storage_key"] is None and calls.count("POST") == 1
    if failure in ("malformed", "poll-transport"):
        await admin.execute("UPDATE ben.media_executions SET poll_attempts=180 WHERE execution_id=$1", initial["execution_id"])
        await due(admin)
        await service_for(repo, handler).tick(ORG)
        assert (await repo.read(ORG, USER, execution=initial["execution_id"]))["state"] == "expired"
        assert calls.count("POST") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["download", "ingestion", "journal"])
async def test_ingestion_failure_recovery_never_generates_again(context, monkeypatch, failure):
    repo, admin, request = context
    calls, downloads = [], 0
    def handler(req):
        nonlocal downloads
        calls.append(req.method)
        if req.method == "POST":
            return response({"name": OP})
        if str(req.url) == POLL:
            return response(completed())
        downloads += 1
        return httpx.Response(503) if failure == "download" and downloads == 1 else httpx.Response(200, content=mp4())
    initial = await admit(service_for(repo, handler), request)
    await service_for(repo, handler).tick(ORG)
    import services.media.service as module
    if failure in ("ingestion", "journal"):
        name = "ingest_mp4" if failure == "ingestion" else "save_result"
        real = getattr(module, name)
        attempts = 0
        def once(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise OSError("synthetic storage failure")
            return real(*args, **kwargs)
        monkeypatch.setattr(module, name, once)
    await due(admin)
    await service_for(repo, handler).tick(ORG)
    assert (await repo.read(ORG, USER, execution=initial["execution_id"]))["state"] != "succeeded"
    await due(admin)
    await service_for(repo, handler).tick(ORG)
    row = await repo.read(ORG, USER, execution=initial["execution_id"])
    assert row["state"] == "succeeded" and calls.count("POST") == 1
    assert row["provider_operation_ref"] == OP and row["ingest_attempts"] == 2


@pytest.mark.asyncio
async def test_source_authorization_fingerprint_and_gate(context, monkeypatch):
    repo, admin, request = context
    service = service_for(repo, lambda _: pytest.fail("unexpected provider call"))
    await admit(service, request)
    with pytest.raises(HTTPException) as conflict:
        await admit(service, replace(request, prompt="Different intent"))
    assert conflict.value.status_code == 409
    with pytest.raises(HTTPException) as missing:
        await service.create(ORG, "someone-else", "other", THREAD, request)
    assert missing.value.status_code == 404
    monkeypatch.setenv("BEN_MEDIA_VEO_ENABLED", "0")
    with pytest.raises(HTTPException) as disabled:
        await admit(service, request, "disabled")
    assert disabled.value.status_code == 404
    monkeypatch.setenv("BEN_MEDIA_VEO_ENABLED", "1")
    await admin.execute("UPDATE ben.media_executions SET deleted_at=now() WHERE resource_id=$1", uuid.UUID(request.source_resource_id))
    await service.tick(ORG)
    assert await admin.fetchval("SELECT state FROM ben.media_executions WHERE idempotency_key='veo-proof'") == "failed"
