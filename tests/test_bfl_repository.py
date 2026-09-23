"""Mock BFL network through real PostgreSQL, authorization, routes and byte storage."""
import asyncio
import json
import uuid

import httpx
from fastapi import FastAPI, HTTPException
import pytest
import pytest_asyncio
from sqlalchemy import text

from auth.beta_gate import derive_beta_org_id
from routers.media import router, media_service
from services.media.bfl_image import BflImageAdapter
from services.media.service import MediaService
from services.media.metadata import request_snapshot, request_fingerprint
from tests.test_media_repository import repository, THREAD
from tests.test_bfl_media import REQUEST, KEY, OP, POLL, SAMPLE, response
from tests.test_media_image_foundation import png

USER = "bfl-internal-test"
ORG = derive_beta_org_id(USER)


@pytest_asyncio.fixture
async def context(repository, monkeypatch, tmp_path):
    repo, admin = repository
    await admin.execute("UPDATE ben.threads SET org_id=$1 WHERE id=$2", ORG, THREAD)
    monkeypatch.setenv("BEN_PROJECTS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("BEN_MEDIA_INTERNAL_ENABLED", "1")
    monkeypatch.setenv("BEN_MEDIA_BFL_ENABLED", "1")
    monkeypatch.setenv("BEN_MEDIA_INTERNAL_PRINCIPALS", json.dumps([{"org_id": str(ORG), "user_id": USER},
        {"org_id": str(derive_beta_org_id("other")), "user_id": "other"}]))
    monkeypatch.setenv("BEN_LOCAL_BETA_MODE", "1")
    monkeypatch.setenv("BEN_BETA_PASSCODE", "test-only-passcode")
    yield repo, admin


async def due(admin):
    await admin.execute("UPDATE ben.media_executions SET next_reconcile_at=now()-interval '1 second'")


def adapter(handler):
    return BflImageAdapter(KEY, transport=httpx.MockTransport(handler))


async def admit(service, key="bfl-proof"):
    return await service.create(ORG, USER, key, THREAD, REQUEST)


@pytest.mark.asyncio
async def test_bfl_full_path_restart_private_urls_and_protected_conversation_resource(context):
    repo, admin = context
    calls = []
    polls = 0
    def handler(req):
        nonlocal polls
        calls.append(req.method)
        if req.method == "POST":
            return response({"id": OP, "polling_url": POLL, "cost": 3})
        if str(req.url) == POLL:
            polls += 1
            return response({"id": OP, "status": "Generating" if polls == 1 else "Ready",
                             "cost": 3, "result": {"sample": SAMPLE}})
        assert str(req.url) == SAMPLE and "x-key" not in req.headers
        return httpx.Response(200, stream=httpx.ByteStream(png()))
    bfl = adapter(handler)
    class NoGemini:
        async def generate(self, _):
            pytest.fail("cross-provider fallback")
    service = MediaService(repo, NoGemini(), bfl)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[media_service] = lambda: service
    headers = {"X-Basalt-Beta-Alias": USER, "X-Basalt-Beta-Passcode": "test-only-passcode"}
    body = {"conversation_id": str(THREAD), "model": REQUEST.model, "prompt": REQUEST.prompt, "idempotency_key": "bfl-proof"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://ben") as client:
        assert (await client.post("/api/media/executions", json=body)).status_code == 401
        submitted = await client.post("/api/media/executions", headers=headers, json=body)
        assert submitted.status_code == 202
        execution = uuid.UUID(submitted.json()["execution_id"])
        assert (await client.post("/api/media/executions", headers=headers, json=body)).json() == submitted.json()
        await asyncio.gather(service.tick(ORG), service.tick(ORG))
        row = await repo.read(ORG, USER, execution=execution)
        assert row["state"] == "submitted" and row["submit_attempts"] == 1
        # Restart: only the existing operation is polled. No in-memory job state.
        service = MediaService(repo, NoGemini(), bfl)
        await due(admin)
        await service.tick(ORG)
        assert (await repo.read(ORG, USER, execution=execution))["state"] == "running"
        await due(admin)
        await service.tick(ORG)
        row = await repo.read(ORG, USER, execution=execution)
        assert row["state"] == "succeeded" and row["provider"] == "bfl" and row["model"] == REQUEST.model
        assert row["poll_attempts"] == 2 and row["submit_attempts"] == calls.count("POST") == 1
        assert row["storage_key"].endswith("output.png") and str(row["estimated_cost"]) == "0.03000000"
        assert row["provider_output"]["model_identity_source"] == "exact_dispatch_endpoint"
        assert row["provider_output"]["training_status"] == "not_approved"
        assert SAMPLE not in json.dumps(row["provider_output"])
        listing = await client.get(f"/api/media/executions?conversation_id={THREAD}", headers=headers)
        public = listing.json()["executions"][0]
        assert SAMPLE not in listing.text and POLL not in listing.text and KEY not in listing.text
        assert public["resource_id"] == str(row["resource_id"])
        path = f"/api/media/resources/{row['resource_id']}/content"
        delivered = await client.get(path, headers=headers)
        assert delivered.status_code == 200 and delivered.content == png()
        assert delivered.headers["cache-control"] == "private, no-store"
        assert (await client.get(path)).status_code == 401
        assert (await client.get(path, headers={**headers, "X-Basalt-Beta-Alias": "other"})).status_code == 404
        async with repo.transaction(derive_beta_org_id("other")) as session:
            assert await session.scalar(text("SELECT count(*) FROM ben.media_executions")) == 0
        assert (await admit(service))["execution_id"] == execution
        assert not await service.tick(ORG)
        assert calls.count("POST") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["Request Moderated", "Content Moderated", "Error", "Failed", "Task not found"])
async def test_terminal_bfl_failure_never_resubmits(context, status):
    repo, admin = context
    calls = []
    def handler(req):
        calls.append(req.method)
        return response({"id": OP, "polling_url": POLL} if req.method == "POST" else {"id": OP, "status": status})
    service = MediaService(repo, bfl_adapter=adapter(handler))
    initial = await admit(service)
    await service.tick(ORG)
    await due(admin)
    await service.tick(ORG)
    row = await repo.read(ORG, USER, execution=initial["execution_id"])
    assert row["state"] == "failed" and row["storage_key"] is None
    assert not await service.tick(ORG) and calls == ["POST", "GET"]


@pytest.mark.asyncio
async def test_uncertain_submission_and_lost_response_never_resubmit(context):
    repo, admin = context
    calls = []
    def handler(req):
        calls.append(req.method)
        raise httpx.ReadTimeout(KEY, request=req)
    service = MediaService(repo, bfl_adapter=adapter(handler))
    initial = await admit(service)
    await service.tick(ORG)
    await due(admin)
    await MediaService(repo, bfl_adapter=adapter(handler)).tick(ORG)
    row = await repo.read(ORG, USER, execution=initial["execution_id"])
    assert row["state"] == "submission_unknown" and row["submit_attempts"] == 1 and calls == ["POST"]


@pytest.mark.asyncio
async def test_temporary_download_failure_resumes_known_url_without_post(context):
    repo, admin = context
    calls, downloads = [], 0
    def handler(req):
        nonlocal downloads
        calls.append(req.method)
        if req.method == "POST":
            return response({"id": OP, "polling_url": POLL})
        if str(req.url) == POLL:
            return response({"id": OP, "status": "Ready", "result": {"sample": SAMPLE}, "cost": 3})
        downloads += 1
        return httpx.Response(503) if downloads == 1 else httpx.Response(200, stream=httpx.ByteStream(png()))
    service = MediaService(repo, bfl_adapter=adapter(handler))
    initial = await admit(service)
    await service.tick(ORG)
    await due(admin)
    await service.tick(ORG)
    row = await repo.read(ORG, USER, execution=initial["execution_id"])
    assert row["state"] == "running" and row["provider_state"] == "Ready"
    assert row["usage_dimensions"]["provider_usage"]["cost"] == 3
    await due(admin)
    await MediaService(repo, bfl_adapter=adapter(handler)).tick(ORG)
    row = await repo.read(ORG, USER, execution=initial["execution_id"])
    assert row["state"] == "succeeded" and calls.count("POST") == 1 and downloads == 2


@pytest.mark.asyncio
async def test_model_is_part_of_idempotency_and_bfl_disabled_gate(context, monkeypatch):
    repo, _ = context
    service = MediaService(repo)
    await admit(service)
    from tests.test_media_image_foundation import REQUEST as GEMINI
    with pytest.raises(HTTPException) as conflict:
        await service.create(ORG, USER, "bfl-proof", THREAD, GEMINI)
    assert conflict.value.status_code == 409
    monkeypatch.setenv("BEN_MEDIA_BFL_ENABLED", "0")
    with pytest.raises(HTTPException) as disabled:
        await admit(service, "new")
    assert disabled.value.status_code == 404


@pytest.mark.asyncio
async def test_expired_temporary_output_stops_after_three_gets_without_regeneration(context):
    repo, admin = context
    methods = []
    def handler(req):
        methods.append(req.method)
        if req.method == "POST":
            return response({"id": OP, "polling_url": POLL})
        if str(req.url) == POLL:
            return response({"id": OP, "status": "Ready", "cost": 3, "result": {"sample": SAMPLE}})
        return httpx.Response(404)
    service = MediaService(repo, bfl_adapter=adapter(handler))
    initial = await admit(service)
    await service.tick(ORG)
    for _ in range(3):
        await due(admin)
        await MediaService(repo, bfl_adapter=adapter(handler)).tick(ORG)
    row = await repo.read(ORG, USER, execution=initial["execution_id"])
    assert row["state"] == "failed" and row["ingest_attempts"] == 3
    assert row["usage_dimensions"]["provider_usage"]["cost"] == 3
    assert methods == ["POST", "GET", "GET", "GET", "GET"]
    assert not await service.tick(ORG)


@pytest.mark.asyncio
async def test_poll_transport_failure_preserves_reference_for_restart(context):
    repo, admin = context
    methods = []
    def handler(req):
        methods.append(req.method)
        if req.method == "POST":
            return response({"id": OP, "polling_url": POLL})
        raise httpx.ReadTimeout(KEY, request=req)
    service = MediaService(repo, bfl_adapter=adapter(handler))
    initial = await admit(service)
    await service.tick(ORG)
    for _ in range(2):
        await due(admin)
        await MediaService(repo, bfl_adapter=adapter(handler)).tick(ORG)
    row = await repo.read(ORG, USER, execution=initial["execution_id"])
    assert row["state"] == "submitted" and row["provider_operation_ref"] == OP
    assert row["poll_attempts"] == 2 and methods == ["POST", "GET", "GET"]


@pytest.mark.asyncio
async def test_poll_limit_deadline_and_stale_fencing(context):
    repo, admin = context
    service = MediaService(repo)
    row = await admit(service)
    claimed = await repo.claim(ORG, "first")
    submitting = await repo.mark_submitting(claimed)
    await repo.change(submitting, state="submitted", provider_operation_ref=OP,
        provider_output={"polling_url": POLL}, poll_attempts=180)
    await service.tick(ORG)
    assert (await repo.read(ORG, USER, execution=row["execution_id"]))["state"] == "expired"
    second = await admit(service, "second")
    first = await repo.claim(ORG, "old")
    from datetime import datetime, timezone
    assert (first["lease_expires_at"] - datetime.now(timezone.utc)).total_seconds() < 121
    await admin.execute("UPDATE ben.media_executions SET lease_expires_at=now()-interval '1 second' WHERE execution_id=$1", second["execution_id"])
    await repo.claim(ORG, "new")
    assert await repo.record_poll(first, status="Ready", output={}, usage={}) is None
