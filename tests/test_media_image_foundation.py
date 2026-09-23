"""Internal mock proof. No provider account, key, network, or production DB."""
from __future__ import annotations

import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import uuid

import httpx
from PIL import Image
import pytest

from services.media.contracts import GEMINI_IMAGE_MODEL, ImageRequest, MediaProviderError, normalize_usage
from services.media.gemini_image import ENDPOINT, GeminiImageAdapter
from services.media.image_storage import image_path, ingest_png
from services.media.metadata import evaluation_event, request_fingerprint, request_snapshot, result_observation
from services.workspace_files.storage import DurableStorageUnavailable

REQUEST = ImageRequest(GEMINI_IMAGE_MODEL, "Create a simple red chair on a white background.")
KEY = "test-only-not-a-real-credential"


def png(color="red"):
    stream = io.BytesIO()
    Image.new("RGB", (16, 16), color).save(stream, format="PNG")
    return stream.getvalue()


def response_data():
    return {"model": GEMINI_IMAGE_MODEL, "id": "internal-operation-id", "status": "completed",
            "steps": [{"type": "model_output", "content": [
                {"type": "image", "mime_type": "image/png", "data": base64.b64encode(png()).decode()}
            ]}], "usage": {"total_input_tokens": 12, "total_output_tokens": 1120,
                           "total_thought_tokens": 0,
                           "output_tokens_by_modality": [{"modality": "image", "tokens": 1120}]}}


def http_response(data, code=200, headers=None):
    return httpx.Response(code, headers=headers or {}, stream=httpx.ByteStream(json.dumps(data).encode()))


@pytest.mark.asyncio
async def test_jpeg_result_normalized_ingested_and_provenance_survives_journal(tmp_path, monkeypatch):
    import hashlib
    from services.media.journal import save_result, load_result
    monkeypatch.setenv("BEN_PROJECTS_DATA_DIR", str(tmp_path))
    raw = io.BytesIO()
    Image.new("RGB", (16, 16), "red").save(raw, format="JPEG")
    source = raw.getvalue()
    data = response_data()
    data.pop("id")  # Stateless completion must survive journal/storage without a provider ID.
    data["steps"][0]["content"][0].update(mime_type="image/jpeg", data=base64.b64encode(source).decode())
    result = await GeminiImageAdapter(KEY).generate(REQUEST, transport=httpx.MockTransport(lambda r: http_response(data)))
    assert result.mime_type == "image/png" and result.data.startswith(b"\x89PNG\r\n\x1a\n")
    with Image.open(io.BytesIO(result.data)) as decoded, Image.open(io.BytesIO(source)) as original:
        assert decoded.size == original.size and decoded.tobytes() == original.convert("RGB").tobytes()
    encoding = result.usage["image_encoding"]
    assert encoding["source_sha256"] == hashlib.sha256(source).hexdigest()
    assert encoding["normalization"] == "jpeg-to-rgb-png-v1"
    row = {"org_id": uuid.uuid4(), "resource_id": uuid.uuid4(), "execution_id": uuid.uuid4(), "model": GEMINI_IMAGE_MODEL}
    save_result(row, result)
    restored = load_result(row)
    assert restored == result
    assert restored.operation_ref is None
    assert result_observation(restored)["provider_operation_ref_missing_reason"] == "not_reported_stateless_response"
    stored = ingest_png(restored.data, org_id=row["org_id"], resource_id=row["resource_id"])
    assert stored.mime_type == "image/png" and stored.byte_size == len(result.data)
    assert result_observation(restored)["usage_dimensions"]["image_encoding"] == encoding


@pytest.mark.parametrize("raw", [b"invalid", png(), b"\xff\xd8\xfftruncated"])
def test_jpeg_normalization_rejects_corruption_and_mime_mismatch(raw):
    from services.media.gemini_image import jpeg_to_png
    with pytest.raises(MediaProviderError, match="media_invalid_image"):
        jpeg_to_png(raw)


def test_jpeg_normalization_enforces_output_bound(monkeypatch):
    from services.media.gemini_image import jpeg_to_png
    raw = io.BytesIO()
    Image.new("RGB", (16, 16), "red").save(raw, format="JPEG")
    monkeypatch.setattr("services.media.gemini_image.MAX_IMAGE_BYTES", 10)
    with pytest.raises(MediaProviderError, match="media_invalid_image"):
        jpeg_to_png(raw.getvalue())


@pytest.mark.asyncio
async def test_exact_dispatch_header_stateless_and_no_ownership_sent():
    calls = []
    def handler(request):
        calls.append(request)
        assert str(request.url) == ENDPOINT
        assert request.method == "POST" and request.headers["x-goog-api-key"] == KEY
        assert KEY not in str(request.url)
        assert json.loads(request.content) == {
            "model": GEMINI_IMAGE_MODEL, "input": REQUEST.prompt,
            "store": False, "background": False, "stream": False,
            "response_format": {"type": "image", "mime_type": "image/jpeg",
                                "aspect_ratio": "1:1", "image_size": "1K"}}
        return http_response(response_data())
    result = await GeminiImageAdapter(KEY).generate(REQUEST, transport=httpx.MockTransport(handler))
    assert len(calls) == 1
    assert result.data == png() and result.returned_model == GEMINI_IMAGE_MODEL
    assert result.usage["provider_usage"]["total_thought_tokens"] == 0
    assert result.usage["image_count"] == 1
    assert result.duration_ms >= 0
    assert "internal-operation-id" not in repr(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("image_request", [replace(REQUEST, model="gemini-2.5-flash"),
    replace(REQUEST, model="gemini-3.1-flash-image-preview"), replace(REQUEST, prompt=" "),
    replace(REQUEST, prompt="x"*8001), replace(REQUEST, image_size="4K"),
    replace(REQUEST, aspect_ratio="bad")])
async def test_invalid_request_never_dispatches(image_request):
    def handler(_):
        pytest.fail("invalid request dispatched")
    with pytest.raises(MediaProviderError):
        await GeminiImageAdapter(KEY).generate(image_request, transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_missing_credential_never_dispatches():
    with pytest.raises(MediaProviderError, match="credentials_unavailable"):
        await GeminiImageAdapter(" ").generate(REQUEST)


@pytest.mark.asyncio
@pytest.mark.parametrize("status,unknown", [(400,False),(401,False),(403,False),(404,False),
    (429,False),(408,True),(500,True),(503,True),(302,True)])
async def test_http_failures_never_retry_or_follow_redirects(status, unknown):
    calls = []
    def handler(request):
        calls.append(request)
        return http_response({"error": {"message": KEY}}, status, {"location": "https://example.org"})
    with pytest.raises(MediaProviderError) as exc:
        await GeminiImageAdapter(KEY).generate(REQUEST, transport=httpx.MockTransport(handler))
    assert len(calls) == 1 and exc.value.submission_unknown is unknown
    assert KEY not in str(exc.value) and exc.value.__context__ is None


@pytest.mark.asyncio
async def test_timeout_is_unknown_and_exception_has_no_http_request():
    calls = []
    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout(KEY, request=request)
    with pytest.raises(MediaProviderError) as exc:
        await GeminiImageAdapter(KEY).generate(REQUEST, transport=httpx.MockTransport(handler))
    assert len(calls) == 1 and exc.value.submission_unknown
    assert exc.value.__context__ is None and exc.value.__cause__ is None
    assert KEY not in repr(exc.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation,code", [
    (lambda d: d.update(model="different-model"), "identity_mismatch"),
    (lambda d: d.update(status="in_progress"), "not_completed"),
    (lambda d: d.update(status="failed"), "not_completed"),
    (lambda d: d.update(id=123), "invalid_operation"),
    (lambda d: d.update(id="x" * 2049), "invalid_operation"),
    (lambda d: d.update(steps=[]), "output_count"),
    (lambda d: d["steps"].append(d["steps"][0]), "output_count"),
    (lambda d: d["steps"][0]["content"][0].update(mime_type="text/html"), "invalid_image"),
    (lambda d: d["steps"][0]["content"][0].update(data="%%%"), "invalid_image"),
    (lambda d: d["steps"][0]["content"][0].update(data=base64.b64encode(b"<html>").decode()), "invalid_image"),
    (lambda d: d["steps"][0]["content"][0].update(data=None, uri="http://127.0.0.1"), "invalid_image"),
    (lambda d: d["steps"][0].update(type="user_input"), "output_count"),
])
async def test_invalid_provider_output_fails_closed(mutation, code):
    data = response_data()
    mutation(data)
    with pytest.raises(MediaProviderError, match=code):
        await GeminiImageAdapter(KEY).generate(REQUEST, transport=httpx.MockTransport(lambda r: http_response(data)))


@pytest.mark.asyncio
async def test_bounded_response(monkeypatch):
    monkeypatch.setattr("services.media.gemini_image.MAX_RESPONSE_BYTES", 100)
    with pytest.raises(MediaProviderError, match="too_large") as exc:
        await GeminiImageAdapter(KEY).generate(REQUEST, transport=httpx.MockTransport(lambda r: http_response(response_data())))
    assert exc.value.submission_unknown


@pytest.mark.asyncio
async def test_malformed_json_redacted():
    def handler(_):
        return httpx.Response(200, stream=httpx.ByteStream(KEY.encode()))
    with pytest.raises(MediaProviderError, match="invalid_response") as exc:
        await GeminiImageAdapter(KEY).generate(REQUEST, transport=httpx.MockTransport(handler))
    assert exc.value.__context__ is None


def test_usage_unknown_zero_and_arbitrary_fields():
    assert normalize_usage(None)["provider_usage"] is None
    assert normalize_usage({})["provider_usage"] is None
    clean = normalize_usage({"total_tokens": 0, "total_input_tokens": True,
                             "total_output_tokens": -1, "secret": KEY})
    assert clean["provider_usage"] == {"unit": "tokens", "source": "provider_reported", "total_tokens": 0}
    assert KEY not in json.dumps(clean)


def test_snapshot_roundtrip_fingerprint_and_rights():
    snapshot = request_snapshot(REQUEST, conversation_id="conversation", workspace_id=None)
    assert json.loads(json.dumps(snapshot)) == snapshot
    assert request_fingerprint(snapshot) == request_fingerprint(dict(reversed(list(snapshot.items()))))
    different = request_snapshot(replace(REQUEST, prompt="Other prompt"), conversation_id="conversation", workspace_id=None)
    assert request_fingerprint(snapshot) != request_fingerprint(different)
    different = request_snapshot(REQUEST, conversation_id="other", workspace_id=None)
    assert request_fingerprint(snapshot) != request_fingerprint(different)
    for purpose in ("training", "fine_tuning", "distillation"):
        assert snapshot["rights"][purpose + "_status"] == "not_approved"
    assert snapshot["provenance"]["model_snapshot"] is None
    with pytest.raises(ValueError):
        request_snapshot(REQUEST, conversation_id=None, workspace_id=None)


def test_evaluation_uses_existing_contract_and_preserves_restricted_correction():
    correction = uuid.uuid4()
    args = dict(event_id=uuid.uuid4(), org_id=uuid.uuid4(), workspace_id=uuid.uuid4(),
                execution_id=uuid.uuid4(), evaluator_id="trusted-evaluator", observed_at=datetime.now(timezone.utc),
                acceptance="accepted", rubric_version="chair-v1", labels={"fidelity": "pass"},
                correction_resource_id=correction)
    event = evaluation_event(**args)
    event.validate()
    assert event.event_type == "observation" and event.origin == "operational"
    assert event.payload["training_status"] == "not_approved"
    assert event.payload["correction_resource_id"] == str(correction)
    cohort_event = evaluation_event(**args, experiment_id=uuid.uuid4())
    assert cohort_event.origin == "operational"
    cohort_event.validate()
    with pytest.raises(ValueError):
        evaluation_event(**{**args, "workspace_id": None})
    with pytest.raises(ValueError):
        evaluation_event(**{**args, "labels": {"api_key": KEY}})


@pytest.fixture
def storage_root(tmp_path, monkeypatch):
    monkeypatch.setattr("services.media.image_storage.files_root", lambda: tmp_path)
    return tmp_path


@pytest.mark.asyncio
async def test_mock_provider_to_owned_bytes_reload_and_provenance(storage_root):
    result = await GeminiImageAdapter(KEY).generate(REQUEST, transport=httpx.MockTransport(lambda r: http_response(response_data())))
    org, resource = uuid.uuid4(), uuid.uuid4()
    stored = ingest_png(result.data, org_id=org, resource_id=resource)
    assert (storage_root / stored.storage_key).read_bytes() == png()
    assert stored.width == 16 and stored.height == 16
    assert ingest_png(result.data, org_id=org, resource_id=resource) == stored
    observed = result_observation(result)
    assert observed["actual_charge"] is None and observed["training_status"] == "not_approved"
    assert "internal-operation-id" not in json.dumps(observed)
    assert not list(storage_root.rglob(".ingest-*"))


@pytest.mark.parametrize("attempt", range(10))
def test_concurrent_ingestion_cannot_overwrite(storage_root, attempt):
    org, resource = uuid.uuid4(), uuid.uuid4()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: ingest_png(png(), org_id=org, resource_id=resource), range(8)))
    assert len(set(results)) == 1
    with pytest.raises(DurableStorageUnavailable):
        ingest_png(png("blue"), org_id=org, resource_id=resource)
    assert image_path(org, resource)[1].read_bytes() == png()
    assert not list(storage_root.rglob(".ingest-*"))


@pytest.mark.parametrize("data", [b"", b"<html>", b"\x89PNG\r\n\x1a\n", png()[:40]])
def test_ingestion_rejects_corruption_before_writing(storage_root, data):
    with pytest.raises(ValueError):
        ingest_png(data, org_id=uuid.uuid4(), resource_id=uuid.uuid4())
    assert list(storage_root.iterdir()) == []


def test_storage_tenant_paths_and_uuid_validation(storage_root):
    resource = uuid.uuid4()
    assert image_path(uuid.uuid4(), resource)[1] != image_path(uuid.uuid4(), resource)[1]
    with pytest.raises(ValueError):
        image_path("../outside", resource)


def test_failed_disk_publication_cleans_temporary(storage_root, monkeypatch):
    def fail(*_):
        raise OSError("simulated disk error")
    monkeypatch.setattr("services.media.image_storage.os.link", fail)
    with pytest.raises(OSError):
        ingest_png(png(), org_id=uuid.uuid4(), resource_id=uuid.uuid4())
    assert not list(storage_root.rglob("*.png"))
    assert not list(storage_root.rglob(".ingest-*"))
