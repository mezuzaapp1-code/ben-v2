"""Deterministic orchestration/HTTP tests. PostgreSQL assertions live separately."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import uuid

from fastapi import FastAPI, HTTPException
import httpx
import pytest

from routers.media import router, media_service
from services.media.access import pilot_principals, require_pilot
from services.media.accounting import account
from services.media.contracts import ImageResult, MediaProviderError, normalize_usage
from services.media.image_storage import image_path
from services.media.journal import save_result
from services.media.service import MediaService, public_execution, media_worker
from tests.test_media_image_foundation import REQUEST, png, response_data

ORG, OTHER, THREAD = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
USER = "media-tester"


def execution():
    from services.media.metadata import request_snapshot
    now = datetime.now(timezone.utc)
    return dict(execution_id=uuid.uuid4(), org_id=ORG, created_by=USER, conversation_id=str(THREAD),
                resource_id=uuid.uuid4(), state="pending", created_at=now, deadline_at=now+timedelta(minutes=30),
                next_reconcile_at=now, lease_owner=None, lease_expires_at=None, version=0,
                submit_attempts=0, ingest_attempts=0, error_code=None, provider="google", model=REQUEST.model,
                request_payload=request_snapshot(REQUEST, conversation_id=str(THREAD), workspace_id=None),
                storage_key=None, usage_dimensions={}, estimated_cost=None, pricing_version=None)


def result():
    return ImageResult(png(), "image/png", REQUEST.model, "private-operation",
                       normalize_usage(response_data()["usage"]), 10.0)


class RepositoryDouble:
    """State double only; does NOT claim to verify SQL, locks or RLS."""
    def __init__(self):
        self.row = execution()
        self.destination_exists = True

    async def claim(self, org, owner):
        row = self.row
        if org != row["org_id"] or row["lease_owner"] or row["state"] not in (
                "pending", "submitting", "ingesting", "submission_unknown"):
            return None
        if row["next_reconcile_at"] > datetime.now(timezone.utc):
            return None
        row.update(lease_owner=owner, version=row["version"]+1)
        return deepcopy(row)

    async def mark_submitting(self, row):
        if not self.destination_exists:
            raise HTTPException(404)
        assert row["state"] == "pending" and row["submit_attempts"] == 0
        self.row.update(state="submitting", submit_attempts=1, version=self.row["version"]+1)
        return deepcopy(self.row)

    async def record_result(self, row, generated, observation):
        self.row.update(state="ingesting", provider_operation_ref=generated.operation_ref,
                        provider_output=observation, usage_dimensions=generated.usage, version=row["version"]+1)
        return deepcopy(self.row)

    async def change(self, row, **values):
        assert row["version"] == self.row["version"] and row["lease_owner"] == self.row["lease_owner"]
        if values.get("state") == "succeeded" and not self.destination_exists:
            raise HTTPException(404)
        self.row.update(**values, lease_owner=None, version=row["version"]+1)
        return deepcopy(self.row)

    async def read(self, org, user, **selectors):
        if org != ORG or user != USER or not self.destination_exists:
            raise HTTPException(404)
        if selectors.get("resource") not in (None, self.row["resource_id"]):
            raise HTTPException(404)
        return deepcopy(self.row)


class AdapterDouble:
    def __init__(self, failure=None, callback=None):
        self.calls = 0
        self.failure = failure
        self.callback = callback

    async def generate(self, request):
        assert request.model == REQUEST.model
        self.calls += 1
        if self.callback:
            self.callback()
        await asyncio.sleep(0)
        if self.failure:
            raise self.failure
        return result()


@pytest.fixture
def context(monkeypatch, tmp_path):
    monkeypatch.setenv("BEN_PROJECTS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("BEN_MEDIA_INTERNAL_ENABLED", "1")
    monkeypatch.setenv("BEN_MEDIA_INTERNAL_PRINCIPALS", json.dumps([{"org_id": str(ORG), "user_id": USER}]))
    repo, adapter = RepositoryDouble(), AdapterDouble()
    return repo, adapter, MediaService(repo, adapter)


@pytest.mark.asyncio
async def test_success_is_owned_reloadable_and_accounted(context):
    repo, adapter, service = context
    await service.tick(ORG)
    assert repo.row["state"] == "succeeded" and adapter.calls == 1
    assert repo.row["submit_attempts"] == 1 and repo.row["ingest_attempts"] == 1
    assert await MediaService(repo, adapter).resource_bytes(ORG, USER, repo.row["resource_id"]) == png()
    assert repo.row["usage_dimensions"]["image_count"] == 1
    assert str(repo.row["estimated_cost"]) == "0.06720600"
    assert repo.row["actual_charge"] is None
    assert repo.row["provider_output"]["training_status"] == "not_approved"
    public = public_execution(repo.row)
    encoded = json.dumps(public)
    for forbidden in ("private-operation", "storage_key", "request_payload", REQUEST.prompt):
        assert forbidden not in encoded
    await service.tick(ORG)
    assert adapter.calls == 1


@pytest.mark.asyncio
async def test_concurrent_ticks_generate_once(context):
    repo, adapter, service = context
    await asyncio.gather(*(service.tick(ORG) for _ in range(8)))
    assert adapter.calls == 1 and repo.row["state"] == "succeeded"


@pytest.mark.asyncio
@pytest.mark.parametrize("unknown", [True, False])
async def test_provider_failure_no_fallback_or_resubmission(context, unknown):
    repo, _, _ = context
    adapter = AdapterDouble(MediaProviderError("media_transport_uncertain", submission_unknown=unknown))
    service = MediaService(repo, adapter)
    await service.tick(ORG)
    assert repo.row["state"] == ("submission_unknown" if unknown else "failed")
    await service.tick(ORG)
    assert adapter.calls == 1


@pytest.mark.asyncio
async def test_restart_after_unknown_submission_never_regenerates(context):
    repo, adapter, service = context
    repo.row.update(state="submitting", submit_attempts=1)
    await service.tick(ORG)
    assert repo.row["state"] == "submission_unknown" and adapter.calls == 0
    repo.row.update(deadline_at=datetime.now(timezone.utc)-timedelta(seconds=1),
                    next_reconcile_at=datetime.now(timezone.utc)-timedelta(seconds=1))
    await MediaService(repo, adapter).tick(ORG)
    assert repo.row["state"] == "expired" and adapter.calls == 0


@pytest.mark.asyncio
async def test_restart_recovers_journal_without_provider(context):
    repo, adapter, service = context
    repo.row.update(state="submitting", submit_attempts=1)
    save_result(repo.row, result())
    await service.tick(ORG)
    assert repo.row["state"] == "succeeded" and adapter.calls == 0


@pytest.mark.asyncio
async def test_ingestion_failure_retries_only_storage_and_preserves_usage(context, monkeypatch):
    repo, adapter, service = context
    import services.media.service as module
    original = module.ingest_png
    def fail(*args, **kwargs):
        raise OSError("do-not-expose-path-or-provider-secret")
    monkeypatch.setattr(module, "ingest_png", fail)
    await service.tick(ORG)
    assert repo.row["state"] == "ingesting"
    assert repo.row["usage_dimensions"]["provider_usage"]["total_input_tokens"] == 12
    monkeypatch.setattr(module, "ingest_png", original)
    repo.row["next_reconcile_at"] = datetime.now(timezone.utc)
    await MediaService(repo, adapter).tick(ORG)
    assert repo.row["state"] == "succeeded" and adapter.calls == 1


@pytest.mark.asyncio
async def test_missing_destination_before_submit_and_before_publish(context):
    repo, adapter, service = context
    repo.destination_exists = False
    await service.tick(ORG)
    assert repo.row["state"] == "failed" and adapter.calls == 0
    repo.row = execution()
    repo.destination_exists = True
    adapter.callback = lambda: setattr(repo, "destination_exists", False)
    await service.tick(ORG)
    assert repo.row["state"] == "failed" and adapter.calls == 1
    assert repo.row["storage_key"] is None


@pytest.mark.asyncio
async def test_delivery_denies_cross_tenant_principal_deleted_destination_and_corruption(context):
    repo, _, service = context
    await service.tick(ORG)
    resource = repo.row["resource_id"]
    for org, user in ((OTHER, USER), (ORG, "other-user")):
        with pytest.raises(HTTPException) as failure:
            await service.resource_bytes(org, user, resource)
        assert failure.value.status_code == 404
    image_path(ORG, resource)[1].write_bytes(b"corrupt")
    with pytest.raises(HTTPException) as failure:
        await service.resource_bytes(ORG, USER, resource)
    assert failure.value.status_code == 503
    repo.destination_exists = False
    with pytest.raises(HTTPException) as failure:
        await service.resource_bytes(ORG, USER, resource)
    assert failure.value.status_code == 404


@pytest.mark.asyncio
async def test_disabled_worker_and_revoked_principal(context, monkeypatch):
    repo, adapter, service = context
    monkeypatch.delenv("BEN_MEDIA_INTERNAL_ENABLED")
    assert pilot_principals() == set()
    async with media_worker():
        pass
    await service.tick(ORG)
    assert repo.row["state"] == "failed" and adapter.calls == 0


@pytest.mark.parametrize("raw", ["null", "{}", "[{}]", "not-json", '[{"org_id":"bogus","user_id":"x"}]'])
def test_pilot_invalid_configuration_fails_closed(context, monkeypatch, raw):
    monkeypatch.setenv("BEN_MEDIA_INTERNAL_PRINCIPALS", raw)
    assert pilot_principals() == set()


def test_partial_usage_is_not_zero_or_fake_tokens():
    accounted = account(normalize_usage(None), width=1024, height=1024)
    assert accounted["estimated_cost"] is None and accounted["actual_charge"] is None
    assert accounted["usage_dimensions"]["provider_usage"] is None
    assert accounted["usage_dimensions"]["cost_estimate"]["components"] == {"image_output_usd": "0.06720000"}


@pytest.mark.asyncio
async def test_http_polling_does_not_submit_and_resource_is_authenticated(context):
    repo, adapter, service = context
    await service.tick(ORG)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[media_service] = lambda: service
    app.dependency_overrides[require_pilot] = lambda: (ORG, USER)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        for _ in range(3):
            response = await client.get(f'/api/media/executions/{repo.row["execution_id"]}')
            assert response.status_code == 200
        response = await client.get(f'/api/media/resources/{repo.row["resource_id"]}/content')
        assert response.content == png() and response.headers["cache-control"] == "private, no-store"
        assert adapter.calls == 1
        app.dependency_overrides[require_pilot] = lambda: (OTHER, USER)
        assert (await client.get(f'/api/media/resources/{repo.row["resource_id"]}/content')).status_code == 404
        app.dependency_overrides.pop(require_pilot)
        assert (await client.get('/api/media/capabilities')).status_code in (401, 404)


@pytest.mark.asyncio
async def test_http_rejects_client_ownership_and_other_model(context):
    _, adapter, service = context
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[media_service] = lambda: service
    app.dependency_overrides[require_pilot] = lambda: (ORG, USER)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        body = {"conversation_id": str(THREAD), "idempotency_key": "test", "prompt": "chair"}
        for extra in ({"org_id": str(OTHER)}, {"storage_key": "private"}, {"model": "other-model"},
                      {"rights": {"training_status": "approved"}}, {"provider_operation_ref": "private"}):
            assert (await client.post('/api/media/executions', json={**body, **extra})).status_code == 422
    assert adapter.calls == 0
