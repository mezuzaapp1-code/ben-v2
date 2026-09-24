"""Protected single Kling proof. Instrumentation only; product adapters unchanged."""
import asyncio
from datetime import datetime, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path
import secrets
import time
import uuid

import av
import httpx
from fastapi import FastAPI
from PIL import Image, ImageDraw
import pytest
from sqlalchemy import text

from services.media.fal_kling_video import FalKlingVideoAdapter, ENDPOINT, QUEUE_ROOT
from tests.test_media_repository import repository, THREAD

pytestmark = pytest.mark.skipif(os.getenv("BEN_SINGLE_KLING_PROOF") != "authorized-one-video",
                               reason="explicit protected one-video authorization required")


def safe_error(raw, status, metrics):
    """No arbitrary provider strings or request objects in evidence/logs."""
    safe = {"http_status": status}
    try:
        value = json.loads(raw)
        text_value = json.dumps(value).lower()
        categories = {"authentication": ("unauthorized", "invalid key", "api key", "authentication"),
            "credits": ("balance", "credit", "billing", "payment"),
            "quota": ("quota", "rate limit", "too many"),
            "model_access": ("not found", "model access", "permission", "forbidden"),
            "invalid_input": ("validation", "invalid input", "image_url", "duration"),
            "safety": ("moderation", "content policy", "nsfw")}
        safe["categories"] = [label for label, words in categories.items() if any(w in text_value for w in words)]
    except (ValueError, UnicodeError):
        safe["categories"] = ["unparsed_error_body"]
    metrics["provider_error"] = safe


class OneGenerationTransport(httpx.AsyncBaseTransport):
    def __init__(self, metrics, inner_factory):
        self.metrics, self.inner_factory = metrics, inner_factory
        self.opened = []

    async def handle_async_request(self, request):
        metrics = self.metrics
        if request.method == "POST":
            assert str(request.url) == ENDPOINT, "Unexpected generation endpoint"
            assert metrics.get("generation_requests", 0) == 0, "Second generation forbidden"
            assert request.headers["X-Fal-No-Retry"] == "1", "Provider retry must stay disabled"
            body = json.loads(request.content)
            assert set(body) == {"prompt", "image_url", "duration", "generate_audio"}
            assert body["duration"] == "3" and body["generate_audio"] is False
            import base64
            assert body["image_url"] == "data:image/png;base64," + base64.b64encode(synthetic_png()).decode()
            metrics["generation_requests"] = 1
            metrics["submission_started"] = time.monotonic()
        else:
            assert request.method == "GET", "Unexpected provider mutation"
            if request.url.host == "queue.fal.run":
                assert str(request.url).startswith((QUEUE_ROOT + "/requests/", ENDPOINT + "/requests/"))
                kind = "poll_requests" if request.url.path.endswith("/status") else "result_requests"
                if kind == "result_requests":
                    assert metrics.get(kind, 0) == 0, "Second result fetch forbidden in proof"
            else:
                assert request.url.host.endswith(".fal.media") or request.url.host == "fal.media"
                assert "authorization" not in request.headers
                kind = "download_requests"
                assert metrics.get(kind, 0) == 0, "Second download forbidden in proof"
            metrics[kind] = metrics.get(kind, 0) + 1
        inner = self.inner_factory()
        self.opened.append(inner)
        response = await inner.handle_async_request(request)
        if request.method == "POST":
            metrics["submission_http_status"] = response.status_code
            metrics["submission_duration_ms"] = round((time.monotonic()-metrics["submission_started"]) * 1000, 2)
        if response.status_code >= 400:
            raw = bytearray()
            async for part in response.aiter_bytes():
                raw.extend(part[:65537-len(raw)])
                if len(raw) > 65536:
                    break
            await response.aclose()
            safe_error(bytes(raw) if len(raw) <= 65536 else b"", response.status_code, metrics)
        return response

    async def aclose(self):
        for inner in self.opened:
            await inner.aclose()
        self.opened.clear()


class ObservedAdapter(FalKlingVideoAdapter):
    """Inspect decoded results, never change normalization, dispatch or lifecycle."""
    def __init__(self, key, metrics, evidence_dir, **kwargs):
        super().__init__(key, **kwargs)
        self.metrics, self.evidence_dir = metrics, evidence_dir

    async def _json(self, method, url, body=None):
        result = await super()._json(method, url, body)
        if method == "POST":
            self.metrics["operation_reference_returned"] = isinstance(result.get("request_id"), str)
        if method == "GET" and url.endswith("/status"):
            status = result.get("status")
            if status in ("IN_QUEUE", "IN_PROGRESS", "COMPLETED"):
                self.metrics["last_provider_status"] = status
            if status == "COMPLETED":
                self.metrics["provider_done"] = True
                self.metrics.setdefault("submission_to_ready_ms", round((time.monotonic()-self.metrics["submission_started"])*1000, 2))
        for field in ("usage", "cost"):
            reported = result.get(field)
            if isinstance(reported, dict):
                clean = {k: reported[k] for k in ("video_seconds", "cost_usd", "credits")
                    if type(reported.get(k)) in (int, float) and math.isfinite(reported[k]) and 0 <= reported[k] <= 10**12}
                if clean:
                    self.metrics.setdefault("provider_reported_fields", {})[field] = clean
        return result

    async def download(self, operation_ref, sample, dimensions):
        started = time.monotonic()
        result = await super().download(operation_ref, sample, dimensions)
        self.metrics["download_duration_ms"] = round((time.monotonic()-started)*1000, 2)
        self.metrics["download_byte_size"] = len(result.data)
        self.metrics["download_checksum"] = hashlib.sha256(result.data).hexdigest()
        # Preserve actual provider output even if BEN rejects dimensions/format.
        (self.evidence_dir / "provider-output.mp4").write_bytes(result.data)
        try:
            with av.open(io.BytesIO(result.data), format="mp4", options={"protocol_whitelist": ""}) as container:
                stream = container.streams.video[0]
                from math import gcd
                factor = gcd(stream.width, stream.height)
                self.metrics["actual_output"] = {"width": stream.width, "height": stream.height,
                    "aspect_ratio": f"{stream.width//factor}:{stream.height//factor}",
                    "duration_seconds": float(stream.duration*stream.time_base) if stream.duration and stream.time_base else None,
                    "audio_present": bool(container.streams.audio), "byte_size": len(result.data)}
        except (av.FFmpegError, IndexError, ValueError, ZeroDivisionError):
            self.metrics["output_probe_failed"] = True
        return result


def synthetic_png():
    image = Image.new("RGB", (1280, 720), "#eeeeee")
    ImageDraw.Draw(image).rectangle((510, 230, 770, 490), fill="#2475df")
    output = io.BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


async def run_proof(repository, monkeypatch, tmp_path, *, inner_factory, credential):
    from auth.beta_gate import derive_beta_org_id
    from routers.media import router, media_service
    from services.media.contracts import KLING_VIDEO_MODEL, ImageRequest, GEMINI_IMAGE_MODEL
    from services.media.service import MediaService
    from services.media.repository import MediaRepository
    from services.media.image_storage import ingest_png
    from services.media.metadata import request_snapshot, request_fingerprint
    import services.media.service as service_module

    repo, admin = repository
    alias, other_alias = "kling-proof", "kling-proof-other"
    org, other = derive_beta_org_id(alias), derive_beta_org_id(other_alias)
    await admin.execute("UPDATE ben.threads SET org_id=$1 WHERE id=$2", org, THREAD)
    monkeypatch.setenv("BEN_PROJECTS_DATA_DIR", str(tmp_path / "media-store"))
    monkeypatch.setenv("BEN_LOCAL_BETA_MODE", "1")
    passcode = secrets.token_urlsafe(32)
    monkeypatch.setenv("BEN_BETA_PASSCODE", passcode)
    monkeypatch.setenv("BEN_MEDIA_INTERNAL_ENABLED", "1")
    monkeypatch.setenv("BEN_MEDIA_KLING_ENABLED", "1")
    monkeypatch.setenv("BEN_MEDIA_INTERNAL_PRINCIPALS", json.dumps([
        {"org_id": str(org), "user_id": alias}, {"org_id": str(other), "user_id": other_alias}]))
    # Disposable fixture setup: truthful local origin, no image provider call.
    snapshot = request_snapshot(ImageRequest(GEMINI_IMAGE_MODEL, "Locally drawn blue square"),
                                conversation_id=str(THREAD), workspace_id=None)
    snapshot.update(provider="ben-test-fixture", model="synthetic-first-frame-v1")
    snapshot["parameters"] = {"width": 1280, "height": 720, "mime_type": "image/png",
                              "creation_method": "local_rectangle_drawing"}
    snapshot["provenance"] = {"output_origin": "locally_drawn_synthetic_fixture", "external_provider_called": False}
    source = await repo.create(org, alias, "synthetic-first-frame", snapshot, request_fingerprint(snapshot))
    source_bytes = synthetic_png()
    stored = ingest_png(source_bytes, org_id=org, resource_id=source["resource_id"])
    await admin.execute("""UPDATE ben.media_executions SET state='succeeded',storage_key=$2,
        mime_type='image/png',byte_size=$3,checksum=$4,published_at=now() WHERE execution_id=$1""",
        source["execution_id"], stored.storage_key, stored.byte_size, stored.checksum)

    metrics = {"generation_requests": 0, "poll_requests": 0, "download_requests": 0, "result_requests": 0}
    class NoImage:
        async def generate(self, *_):
            raise AssertionError("Image generation forbidden")
        async def submit(self, *_):
            raise AssertionError("Image generation forbidden")
    def fresh_service():
        # Only counters are shared. No provider operation/job state survives.
        return MediaService(MediaRepository(repo.sessions), NoImage(), NoImage(), NoImage(),
            ObservedAdapter(credential, metrics, evidence_dir,
                transport=OneGenerationTransport(metrics, inner_factory)))
    original_ingest = service_module.ingest_mp4
    def measured_ingest(*args, **kwargs):
        start = time.monotonic()
        try:
            result = original_ingest(*args, **kwargs)
            metrics.update(width=result.width, height=result.height, duration_seconds=result.duration_seconds,
                           audio_present=result.audio_present)
            return result
        finally:
            metrics["ingestion_duration_ms"] = round((time.monotonic()-start)*1000, 2)
    monkeypatch.setattr(service_module, "ingest_mp4", measured_ingest)
    evidence_dir = Path("proof-evidence")
    evidence_dir.mkdir(exist_ok=True)
    service = fresh_service()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[media_service] = lambda: service
    headers = {"X-Basalt-Beta-Passcode": passcode, "X-Basalt-Beta-Alias": alias}
    evidence = {"status": "FAIL", "commit": os.getenv("GITHUB_SHA"), "provider": "fal",
        "api": "fal queue API", "model": KLING_VIDEO_MODEL, "training_status": "not_approved",
        "source_origin": "locally_drawn_synthetic_fixture", "source_checksum": stored.checksum,
        "parameters": {"operation": "image_to_video", "duration_seconds": 3, "resolution": "720p",
                       "aspect_ratio": "16:9", "audio": "off"}}
    phase, execution = "admission", None
    started = time.monotonic()
    checks = []
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://ben-proof") as client:
            body = {"conversation_id": str(THREAD), "idempotency_key": "single-kling-proof", "model": KLING_VIDEO_MODEL,
                "source_resource_id": str(source["resource_id"]), "duration_seconds": 3, "resolution": "720p",
                "aspect_ratio": "16:9", "prompt": "A blue square gently moves to the right across a plain light gray background. Fixed camera, simple flat graphic. No audio. No people, text, logos or speech."}
            assert (await client.post("/api/media/executions", json=body)).status_code == 401
            source_response = await client.get(f"/api/media/resources/{source['resource_id']}/content", headers=headers)
            assert source_response.status_code == 200 and source_response.content == source_bytes
            accepted = await client.post("/api/media/executions", headers=headers, json=body)
            assert accepted.status_code == 202
            execution = uuid.UUID(accepted.json()["execution_id"])
            evidence["execution_id"] = str(execution)
            assert (await client.post("/api/media/executions", headers=headers, json=body)).json()["execution_id"] == str(execution)
            assert (await client.post("/api/media/executions", headers=headers, json={**body, "prompt": "changed"})).status_code == 409
            row = await repo.read(org, alias, execution=execution)
            assert row["state"] == "pending" and metrics["generation_requests"] == 0
            checks.extend(["authorization", "source_checksum_delivery", "persisted_before_dispatch", "idempotency_admission"])
            phase = "submission"
            assert await service.tick(org)
            row = await repo.read(org, alias, execution=execution)
            evidence.update(state=row["state"], error_code=row["error_code"])
            assert row["state"] == "submitted", "Stop on submission failure or uncertain acceptance"
            assert row["provider_operation_ref"] and row["submit_attempts"] == 1
            metrics["acceptance_persisted_ms"] = round((time.monotonic() - metrics["submission_started"]) * 1000, 2)
            checks.append("durable_operation_reference")
            phase = "polling_and_ingestion"
            states = ["pending", "submitted"]
            while row["state"] not in ("succeeded", "failed", "expired", "submission_unknown"):
                assert row["error_code"] is None, "Stop on first BEN/provider reconciliation error"
                assert time.monotonic() - started < 480, "Observation limit reached; never resubmit"
                delay = max(0.1, (row["next_reconcile_at"] - datetime.now(timezone.utc)).total_seconds())
                await asyncio.sleep(min(delay, 10))
                service = fresh_service()
                await service.tick(org)
                row = await repo.read(org, alias, execution=execution)
                if states[-1] != row["state"]:
                    states.append(row["state"])
                evidence.update(state=row["state"], error_code=row["error_code"], observed_states=states)
            evidence["lifecycle_duration_ms"] = round((time.monotonic()-started)*1000, 2)
            assert row["state"] == "succeeded", "Kling proof did not succeed; no second generation"
            assert row["submit_attempts"] == metrics["generation_requests"] == 1
            assert metrics["download_requests"] == metrics["result_requests"] == 1
            assert row["provider"] == "fal" and row["model"] == KLING_VIDEO_MODEL
            checks.extend(["restart_safe_polling", "mp4_validation", "immutable_ingestion"])
            phase = "delivery_and_provenance"
            service = fresh_service()
            assert not await service.tick(org)
            public = (await client.get(f"/api/media/executions/{execution}", headers=headers)).json()
            resource = public["resource_id"]
            path = f"/api/media/resources/{resource}/content"
            video = await client.get(path, headers=headers)
            assert video.status_code == 200 and video.headers["content-type"] == "video/mp4"
            assert video.headers["cache-control"] == "private, no-store"
            assert hashlib.sha256(video.content).hexdigest() == row["checksum"] and len(video.content) == row["byte_size"]
            assert (await client.get(path)).status_code == 401
            assert (await client.get(path, headers={**headers, "X-Basalt-Beta-Alias": other_alias})).status_code == 404
            async with repo.transaction(other) as session:
                assert await session.scalar(text("SELECT count(*) FROM ben.media_executions")) == 0
            replay = await client.post("/api/media/executions", headers=headers, json=body)
            assert replay.json()["execution_id"] == str(execution) and replay.json()["status"] == "succeeded"
            assert await admin.fetchval("SELECT count(*) FROM ben.media_executions") == 2
            history = await client.get(f"/api/media/executions?conversation_id={THREAD}", headers=headers)
            assert history.status_code == 200 and public in history.json()["executions"]
            assert row["provider_operation_ref"] not in history.text and row["storage_key"] not in history.text
            observation = row["provider_output"]
            assert observation["upstream_model"] == KLING_VIDEO_MODEL and observation["model_identity_source"] == "exact_dispatch_endpoint"
            assert observation["gateway"] == "fal" and observation["upstream_provider"] == "kling"
            assert row["request_payload"]["rights"]["training_status"] == observation["training_status"] == "not_approved"
            assert row["usage_dimensions"]["video_count"] == 1 and row["pricing_version"] and row["actual_charge"] is None
            assert metrics["audio_present"] is False
            evaluation = await client.post(f"/api/media/executions/{execution}/evaluation", headers=headers,
                json={"acceptance": "unrated", "rubric_version": "internal-kling-proof-v1"})
            assert evaluation.status_code == 200
            evaluated = await repo.read(org, alias, execution=execution)
            assert evaluated["provider_output"]["evaluation"]["training_status"] == "not_approved"
            assert not await service.tick(org) and metrics["generation_requests"] == 1
            (evidence_dir / "video.mp4").write_bytes(video.content)
            (evidence_dir / "source.png").write_bytes(source_bytes)
            checks.extend(["checksum_reload", "protected_delivery", "tenant_rls", "idempotency_replay",
                           "conversation_history", "accounting", "provenance", "evaluation", "audio_off"])
            evidence.update(status="PASS", resource_id=resource, byte_size=row["byte_size"], checksum=row["checksum"],
                usage=row["usage_dimensions"], estimated_cost=public["estimated_cost"], actual_charge=None,
                pricing_version=row["pricing_version"], created_at=row["created_at"].isoformat(),
                published_at=row["published_at"].isoformat(), last_polled_at=row["last_polled_at"].isoformat())
    except Exception as exc:
        evidence["failure_type"] = type(exc).__name__
        raise
    finally:
        if execution:
            final = await repo.read(org, alias, execution=execution)
            evidence.update(state=final["state"], error_code=final["error_code"],
                reserved_resource_id=str(final["resource_id"]), resource_published=final["state"] == "succeeded",
                provider_operation_persisted=bool(final["provider_operation_ref"]),
                submit_attempts=final["submit_attempts"], poll_attempts=final["poll_attempts"],
                ingest_attempts=final["ingest_attempts"])
        evidence.setdefault("lifecycle_duration_ms", round((time.monotonic()-started)*1000, 2))
        metrics.pop("submission_started", None)
        evidence.update(phase=phase, checks=checks, metrics=metrics,
            total_proof_duration_ms=round((time.monotonic()-started)*1000, 2))
        serialized = json.dumps(evidence, indent=2, allow_nan=False)
        assert credential not in serialized and passcode not in serialized
        (evidence_dir / "proof.json").write_text(serialized, encoding="utf-8")


@pytest.mark.asyncio
async def test_single_authorized_kling_video(repository, monkeypatch, tmp_path):
    assert os.getenv("BEN_SINGLE_KLING_PROOF") == "authorized-one-video"
    assert os.getenv("GITHUB_ACTIONS") == "true"
    assert os.getenv("GITHUB_REF") == "refs/heads/astra/ben-media-v1"
    assert os.getenv("GITHUB_RUN_ATTEMPT") == "1", "Paid reruns forbidden"
    credential = os.getenv("FAL_KEY")
    assert credential, "Protected credential missing"
    await run_proof(repository, monkeypatch, tmp_path,
                    inner_factory=lambda: httpx.AsyncHTTPTransport(retries=0), credential=credential)
