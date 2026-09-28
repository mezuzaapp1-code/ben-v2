"""API boundary: fail-fast gate, real authorized assets and closed status schema."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import uuid

from fastapi import FastAPI
import httpx
import pytest

from routers import media
from services.media import access, narration
from services.workspace_files.storage import files_root
from tests.test_media_repository import repository, ORG, OTHER, THREAD
from tests.test_narration_replacement import setup, wav

DENIED = {"detail": {"code": "MEDIA_UNAVAILABLE", "message": "Media unavailable"}}


def app_for(service=None):
    app = FastAPI()
    app.include_router(media.router)
    if service:
        app.dependency_overrides[media.narration_service] = lambda: service
        app.dependency_overrides[media.media_service] = lambda: service
    return app


def identity(monkeypatch, org=ORG, user="tester"):
    monkeypatch.setenv("BEN_MEDIA_LOCAL_NARRATION_ENABLED", "1")
    monkeypatch.setenv("BEN_MEDIA_INTERNAL_ENABLED", "1")
    monkeypatch.setenv("BEN_MEDIA_INTERNAL_PRINCIPALS", json.dumps([
        {"org_id": str(ORG), "user_id": "tester"}, {"org_id": str(OTHER), "user_id": "tester"}]))
    auth = AsyncMock(return_value=SimpleNamespace(tenant_id=str(org), user_id=user))
    monkeypatch.setattr(access, "build_project_tenant_context_from_request", auth)
    return auth


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["off", "missing", "outside_org", "outside_user"])
async def test_gate_has_zero_asset_and_creation_side_effects(monkeypatch, mode):
    auth = identity(monkeypatch)
    if mode == "off":
        monkeypatch.setenv("BEN_MEDIA_LOCAL_NARRATION_ENABLED", "0")
    elif mode == "missing":
        monkeypatch.delenv("BEN_MEDIA_LOCAL_NARRATION_ENABLED")
    else:
        auth.return_value = SimpleNamespace(tenant_id=str(uuid.uuid4()) if mode == "outside_org" else str(ORG),
                                           user_id="outsider" if mode == "outside_user" else "tester")
    factory = Mock(side_effect=AssertionError("service must not be constructed"))
    query = AsyncMock(side_effect=AssertionError("no asset query"))
    files = AsyncMock(side_effect=AssertionError("no file read"))
    create = AsyncMock(side_effect=AssertionError("no execution creation"))
    monkeypatch.setattr(media, "MediaService", factory)
    monkeypatch.setattr("services.media.repository.MediaRepository.read", query)
    monkeypatch.setattr("services.media.repository.MediaRepository.create", create)
    monkeypatch.setattr(narration, "audio_bytes", files)
    body = {k: str(uuid.uuid4()) for k in ("conversation_id", "video_resource_id", "workspace_id",
                                        "music_file_id", "narration_file_id")}
    body["idempotency_key"] = "gate-test"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app_for()), base_url="http://test") as client:
        response = await client.post("/api/media/narration-replacements", json=body)
    assert response.status_code == 404 and response.json() == DENIED
    factory.assert_not_called()
    query.assert_not_awaited()
    files.assert_not_awaited()
    create.assert_not_awaited()
    if mode in ("off", "missing"):
        auth.assert_not_awaited()


async def payload(setup):
    svc, admin, _, _, source, _, sound = setup
    workspace = await admin.fetchval("SELECT workspace_id FROM ben.workspace_files WHERE id=$1", sound)
    return {"conversation_id": str(THREAD), "idempotency_key": "api-test",
            "video_resource_id": str(source["resource_id"]), "workspace_id": str(workspace),
            "music_file_id": str(sound), "narration_file_id": str(sound)}


@pytest.mark.asyncio
async def test_api_admission_polling_and_closed_schema(setup, monkeypatch, caplog):
    svc, admin, *_ = setup
    identity(monkeypatch)
    app = app_for(svc)
    body = await payload(setup)
    with caplog.at_level("INFO", logger="ben.ops"):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            created = await client.post("/api/media/narration-replacements", json=body)
            assert created.status_code == 202, created.text
            pending = created.json()
            assert pending["resource_id"] is None and pending["status"] == "pending"
            assert (await client.post("/api/media/narration-replacements", json=body)).json() == pending
            url = "/api/media/executions/" + pending["execution_id"]
            assert (await client.get(url)).json() == pending
            assert await svc.tick(ORG)
            ready = (await client.get(url)).json()
            assert ready["status"] == "succeeded" and ready["resource_id"]
            assert set(ready) == set(media.ExecutionResponse.model_fields)
            assert "storage_key" not in json.dumps(ready)
            assert "request_payload" not in ready and "lease_owner" not in ready
            assert (await client.get(url)).json() == ready
            identity(monkeypatch, org=OTHER)
            assert (await client.get(url)).status_code == 404
    schema = app.openapi()["components"]["schemas"]["ExecutionResponse"]
    assert schema["additionalProperties"] is False
    assert "storage_key" not in schema["properties"]
    events = [r for r in caplog.records if r.getMessage() == "Narration admission"]
    assert events and all(r.outcome == "accepted" and r.duration_ms >= 0 for r in events)
    assert all(not hasattr(r, key) for r in events for key in ("storage_key", "payload", "data", "file_id"))


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["audio_org", "audio_user", "video_user", "checksum", "long_audio", "free_path"])
async def test_api_rejects_sources_before_creation(setup, monkeypatch, mode):
    svc, admin, _, asset, source, _, sound = setup
    identity(monkeypatch)
    body = await payload(setup)
    if mode in ("audio_org", "audio_user"):
        body["narration_file_id"] = str(await asset(wav(), org=OTHER if mode == "audio_org" else ORG,
                                                  user="different" if mode == "audio_user" else "tester"))
    elif mode == "video_user":
        await admin.execute("UPDATE ben.media_executions SET created_by='different' WHERE execution_id=$1", source["execution_id"])
    elif mode == "checksum":
        key = await admin.fetchval("SELECT storage_key FROM ben.workspace_files WHERE id=$1", sound)
        path = files_root() / key
        data = path.read_bytes()
        path.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
    elif mode == "long_audio":
        body["narration_file_id"] = str(await asset(wav(4.01)))
    else:
        body["storage_key"] = "private/file.wav"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app_for(svc)), base_url="http://test") as client:
        response = await client.post("/api/media/narration-replacements", json=body)
    assert response.status_code == (503 if mode == "checksum" else 422 if mode in ("long_audio", "free_path") else 404)
    assert await admin.fetchval("SELECT count(*) FROM ben.media_executions WHERE operation='narration_replacement'") == 0
