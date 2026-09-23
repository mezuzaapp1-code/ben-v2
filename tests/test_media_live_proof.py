"""Explicitly gated one-image proof; never collected as a paid default test.

Uses the unchanged disposable PostgreSQL/RLS fixture, actual beta authorization,
media HTTP routes, service, adapter and disk storage. Only evidence is exported.
"""
import hashlib
import json
import os
from pathlib import Path
import secrets
import time
import uuid

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text

from tests.test_media_repository import repository, THREAD

pytestmark = pytest.mark.skipif(
    os.getenv("BEN_SINGLE_IMAGE_PROOF") != "authorized-one-image",
    reason="single paid image requires explicit protected workflow")


@pytest.mark.asyncio
async def test_single_authorized_gemini_image(repository, monkeypatch, tmp_path):
    if os.getenv("BEN_SINGLE_IMAGE_PROOF") != "authorized-one-image":
        pytest.skip("single paid image requires explicit protected workflow")
    assert os.getenv("GITHUB_ACTIONS") == "true"
    assert os.getenv("GITHUB_REF") == "refs/heads/astra/ben-media-v1"
    assert os.getenv("GITHUB_RUN_ATTEMPT") == "1", "Paid proof reruns are forbidden"
    assert os.getenv("GOOGLE_API_KEY"), "Protected credential missing"

    from auth.beta_gate import derive_beta_org_id
    from routers.media import router, media_service
    from services.media.service import MediaService
    from services.media.contracts import GEMINI_IMAGE_MODEL

    repo, admin = repository
    alias, other_alias = "media-proof", "media-proof-other"
    org, other = derive_beta_org_id(alias), derive_beta_org_id(other_alias)
    await admin.execute("UPDATE ben.threads SET org_id=$1 WHERE id=$2", org, THREAD)
    monkeypatch.setenv("BEN_PROJECTS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("BEN_LOCAL_BETA_MODE", "1")
    passcode = secrets.token_urlsafe(32)
    monkeypatch.setenv("BEN_BETA_PASSCODE", passcode)
    monkeypatch.setenv("BEN_MEDIA_INTERNAL_ENABLED", "1")
    monkeypatch.setenv("BEN_MEDIA_INTERNAL_PRINCIPALS", json.dumps([
        {"org_id": str(org), "user_id": alias},
        {"org_id": str(other), "user_id": other_alias}]))
    service = MediaService(repo)
    generate = service.adapter.generate
    calls = 0

    async def one_submission(request):
        nonlocal calls
        calls += 1
        assert calls == 1 and request.model == GEMINI_IMAGE_MODEL
        return await generate(request)

    monkeypatch.setattr(service.adapter, "generate", one_submission)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[media_service] = lambda: service
    headers = {"X-Basalt-Beta-Passcode": passcode, "X-Basalt-Beta-Alias": alias}
    evidence_dir = Path("proof-evidence")
    evidence_dir.mkdir(exist_ok=True)
    evidence = {"status": "FAIL", "commit": os.getenv("GITHUB_SHA"),
                "provider": "google", "requested_model": GEMINI_IMAGE_MODEL,
                "training_status": "not_approved", "provider_submissions": 0}
    phase = "admission"
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://ben-proof") as client:
            body = {"conversation_id": str(THREAD), "idempotency_key": "single-proof",
                    "model": GEMINI_IMAGE_MODEL,
                    "prompt": "A single red wooden chair on a plain white background, simple studio image."}
            assert (await client.post("/api/media/executions", json=body)).status_code == 401
            accepted = await client.post("/api/media/executions", headers=headers, json=body)
            assert accepted.status_code == 202
            execution = accepted.json()["execution_id"]
            assert (await client.post("/api/media/executions", headers=headers, json=body)).json()["execution_id"] == execution
            assert (await client.post("/api/media/executions", headers=headers, json={**body, "prompt": "different"})).status_code == 409
            phase = "provider_and_ingestion"
            start = time.monotonic()
            assert await service.tick(org)
            evidence["lifecycle_duration_ms"] = round((time.monotonic() - start) * 1000, 2)
            row = await repo.read(org, alias, execution=uuid.UUID(execution))
            evidence.update(state=row["state"], error_code=row["error_code"], execution_id=execution)
            assert calls == row["submit_attempts"] == 1
            assert row["state"] == "succeeded"
            phase = "delivery_and_persistence"
            # Reconstruct service: delivery and idempotent replay use durable state.
            service = MediaService(repo)
            assert not await service.tick(org)
            public = (await client.get(f"/api/media/executions/{execution}", headers=headers)).json()
            resource = public["resource_id"]
            history = await client.get(f"/api/media/executions?conversation_id={THREAD}", headers=headers)
            assert history.status_code == 200
            assert history.json()["executions"] == [public]
            path = f"/api/media/resources/{resource}/content"
            image = await client.get(path, headers=headers)
            assert image.status_code == 200 and image.headers["content-type"] == "image/png"
            assert image.headers["cache-control"] == "private, no-store"
            assert len(image.content) == row["byte_size"]
            assert hashlib.sha256(image.content).hexdigest() == row["checksum"]
            assert (await client.get(path)).status_code == 401
            assert (await client.get(path, headers={**headers, "X-Basalt-Beta-Alias": other_alias})).status_code == 404
            async with repo.transaction(other) as session:
                assert await session.scalar(text("SELECT count(*) FROM ben.media_executions")) == 0
            replay = await client.post("/api/media/executions", headers=headers, json=body)
            assert replay.json()["execution_id"] == execution and replay.json()["status"] == "succeeded"
            assert await admin.fetchval("SELECT count(*) FROM ben.media_executions") == 1
            assert not await service.tick(org)
            observation = row["provider_output"]
            assert observation["upstream_model"] == GEMINI_IMAGE_MODEL
            assert row["provider_operation_ref"] and row["storage_key"]
            assert row["request_payload"]["rights"]["training_status"] == "not_approved"
            assert observation["training_status"] == "not_approved"
            assert row["usage_dimensions"]["image_count"] == 1 and row["pricing_version"]
            assert "storage_key" not in public and "provider_operation_ref" not in public
            evaluation = await client.post(f"/api/media/executions/{execution}/evaluation", headers=headers,
                json={"acceptance": "unrated", "rubric_version": "internal-live-proof-v1"})
            assert evaluation.status_code == 200
            persisted = await repo.read(org, alias, execution=uuid.UUID(execution))
            assert persisted["provider_output"]["evaluation"]["training_status"] == "not_approved"
            (evidence_dir / "image.png").write_bytes(image.content)
            evidence.update(status="PASS", resource_id=resource, byte_size=row["byte_size"],
                checksum=row["checksum"], returned_model=observation["upstream_model"],
                provider_duration_ms=observation["provider_duration_ms"], usage=row["usage_dimensions"],
                estimated_cost=public["estimated_cost"], actual_charge=None,
                checks=["authorization", "tenant_rls", "idempotency", "one_submission", "ingestion",
                        "protected_delivery", "conversation_history", "persisted_restart_read", "accounting", "provenance", "evaluation"])
    finally:
        evidence.update(phase=phase, provider_submissions=calls)
        # Allowlisted evidence only: never serialize responses, exceptions or keys.
        (evidence_dir / "proof.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
