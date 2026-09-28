"""Kling wire contract and security boundaries; all transport is synthetic."""
import base64
from dataclasses import replace
from functools import lru_cache
import io
import json
import uuid

import httpx
from PIL import Image
import pytest

from services.media.contracts import KLING_VIDEO_MODEL, VEO_VIDEO_MODEL, VideoRequest, MediaProviderError
from services.media.fal_kling_video import FalKlingVideoAdapter, ENDPOINT, QUEUE_ROOT, operation_url, download_url, usage
from services.media.video_storage import inspect_mp4, ingest_mp4
from services.media.accounting import account
from services.media.metadata import request_snapshot, result_observation
from tests.test_veo_media import mp4

KEY = "synthetic-fal-test-key-not-a-credential"
OP = "synthetic-kling-request"
POLL = operation_url(OP)
RESULT = POLL.removesuffix("/status")
FILE = "https://v3b.fal.media/files/test/synthetic.mp4"
REQUEST = VideoRequest(KLING_VIDEO_MODEL, "A blue square moves gently. Synthetic scene.", str(uuid.uuid4()), duration_seconds=3)


@lru_cache(maxsize=4)
def png(width=1280, height=720):
    out = io.BytesIO()
    Image.new("RGB", (width, height), (20, 50, 100)).save(out, format="PNG")
    return out.getvalue()


def video():
    return mp4(frames=72, audio=False)


def response(value, status=200):
    return httpx.Response(status, content=json.dumps(value).encode())


def accepted(poll=POLL):
    return {"request_id": OP, "status_url": poll, "response_url": poll.removesuffix("/status"), "status": "IN_QUEUE"}


def completed():
    return {"request_id": OP, "status": "COMPLETED", "metrics": {"inference_time": 12.5}}


def output():
    return {"video": {"url": FILE, "content_type": "video/mp4", "file_size": len(video())}}


@pytest.mark.asyncio
@pytest.mark.parametrize("poll", [POLL, f"{ENDPOINT}/requests/{OP}/status"])
async def test_exact_wire_no_retry_secret_boundary_and_normalization(poll):
    calls = []
    def handler(req):
        calls.append(req)
        if req.method == "POST":
            assert str(req.url) == ENDPOINT
            assert req.headers["Authorization"] == f"Key {KEY}"
            assert req.headers["X-Fal-No-Retry"] == "1"
            assert req.headers["X-Fal-Store-IO"] == "0"
            assert json.loads(req.headers["X-Fal-Object-Lifecycle-Preference"]) == {"expiration_duration_seconds": 3600}
            assert json.loads(req.content) == {"prompt": REQUEST.prompt, "image_url": "data:image/png;base64," + base64.b64encode(png()).decode(), "duration": "3", "generate_audio": False}
            return response(accepted(poll))
        if str(req.url) == FILE:
            assert "authorization" not in req.headers
            return httpx.Response(200, content=video())
        assert req.headers["Authorization"] == f"Key {KEY}"
        assert str(req.url) in (poll, poll.removesuffix("/status"))
        return response(completed() if str(req.url) == poll else output())
    adapter = FalKlingVideoAdapter(KEY, transport=httpx.MockTransport(handler))
    submission = await adapter.submit(REQUEST, png())
    state, url, dimensions = await adapter.poll(submission.operation_ref, submission.polling_url)
    assert state == "Ready"
    result = await adapter.download(OP, url, dimensions)
    assert result.returned_model == KLING_VIDEO_MODEL and result.operation_ref == OP
    assert inspect_mp4(result.data, duration_seconds=3) == (1280, 720, 3.0, False)
    assert dimensions["provider_usage"] is None and dimensions["provider_timing"]["inference_seconds"] == 12.5
    assert [r.method for r in calls] == ["POST", "GET", "GET", "GET"]
    observation = result_observation(result)
    assert observation["gateway"] == "fal" and observation["upstream_provider"] == "kling"
    assert observation["model_identity_source"] == "exact_dispatch_endpoint"
    assert all(value not in json.dumps(observation) for value in (KEY, OP, FILE, POLL))


@pytest.mark.asyncio
@pytest.mark.parametrize("status,unknown", [(400, False), (401, False), (403, False), (422, False), (429, False), (500, True), (503, True)])
async def test_rejection_no_retry_and_no_error_secrets(status, unknown):
    calls = []
    def handler(req):
        calls.append(req)
        return response({"error": KEY}, status)
    with pytest.raises(MediaProviderError) as error:
        await FalKlingVideoAdapter(KEY, transport=httpx.MockTransport(handler)).submit(REQUEST, png())
    assert error.value.submission_unknown == unknown and len(calls) == 1
    assert KEY not in str(error.value) and error.value.__context__ is None


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{}, {"request_id": OP}, {**accepted(), "status_url": "https://evil.example"}, {**accepted(), "response_url": RESULT + "?key=secret"}])
async def test_malformed_acceptance_is_unknown(payload):
    with pytest.raises(MediaProviderError) as error:
        await FalKlingVideoAdapter(KEY, transport=httpx.MockTransport(lambda _: response(payload))).submit(REQUEST, png())
    assert error.value.submission_unknown


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{}, {"status": "OTHER"}, {"status": "COMPLETED", "request_id": "wrong"}])
async def test_malformed_status_never_success(payload):
    with pytest.raises(MediaProviderError):
        await FalKlingVideoAdapter(KEY, transport=httpx.MockTransport(lambda _: response(payload))).poll(OP, POLL)


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["IN_QUEUE", "IN_PROGRESS"])
async def test_pending_does_not_fetch_output(state):
    calls = []
    def handler(req):
        calls.append(req)
        return response({"status": state})
    result = await FalKlingVideoAdapter(KEY, transport=httpx.MockTransport(handler)).poll(OP, POLL)
    assert result[0] == "Pending" and len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{}, {"video": {}}, {"video": {"url": FILE, "content_type": "text/html"}}, {"video": {"url": FILE, "file_size": 100_000_000}}])
async def test_malformed_output_is_not_ready(payload):
    with pytest.raises(MediaProviderError):
        await FalKlingVideoAdapter(KEY, transport=httpx.MockTransport(lambda r: response(completed() if str(r.url) == POLL else payload))).poll(OP, POLL)


@pytest.mark.parametrize("url", [None, "http://fal.media/a", "https://fal.media.evil/a", "https://127.0.0.1/a", "https://fal.media@evil/a", "https://fal.media/a#x", "https://fal.media:444/a", "https://fal.media/\nsecret"])
def test_download_ssrf_boundary(url):
    with pytest.raises(MediaProviderError):
        download_url(url)


@pytest.mark.asyncio
async def test_no_redirect_no_credential_forwarding():
    calls = []
    def handler(req):
        calls.append(req)
        assert "authorization" not in req.headers
        return httpx.Response(302, headers={"location": "https://evil.example"})
    with pytest.raises(MediaProviderError):
        await FalKlingVideoAdapter(KEY, transport=httpx.MockTransport(handler)).download(OP, FILE, usage())
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("image,intent", [(b"bad", REQUEST), (png(100, 100), REQUEST), (png(720, 1280), REQUEST), (png(), replace(REQUEST, model=VEO_VIDEO_MODEL, duration_seconds=4))])
async def test_invalid_source_or_other_model_never_submitted(image, intent):
    with pytest.raises(MediaProviderError):
        await FalKlingVideoAdapter(KEY, transport=httpx.MockTransport(lambda _: pytest.fail("unexpected dispatch"))).submit(intent, image)


def test_accounting_rights_and_unknown_charge():
    result = account({**usage(), "duration_seconds": 3, "requested_audio": "off"}, width=1280, height=720, model=KLING_VIDEO_MODEL)
    assert str(result["estimated_cost"]) == "0.252" and result["actual_charge"] is None
    assert account(usage(), width=1280, height=720, model=KLING_VIDEO_MODEL)["estimated_cost"] is None
    snapshot = request_snapshot(REQUEST, conversation_id=str(uuid.uuid4()), workspace_id=None)
    assert snapshot["provider"] == "fal" and snapshot["parameters"]["audio"] == "off"
    assert all(snapshot["rights"][key] == "not_approved" for key in ("training_status", "fine_tuning_status", "distillation_status"))


@pytest.mark.parametrize("changes", [{"model": "fal-ai/kling-video/o3/pro/image-to-video"},
    {"model": "fal-ai/kling-video/o3/standard/text-to-video"}, {"duration_seconds": 4},
    {"duration_seconds": True}, {"resolution": "1080p"}, {"aspect_ratio": "1:1"}])
def test_exact_internal_contract(changes):
    with pytest.raises(MediaProviderError):
        replace(REQUEST, **changes).validate()


@pytest.mark.asyncio
async def test_missing_credentials_and_submit_timeout_never_retry():
    calls = []
    def handler(req):
        calls.append(req.method)
        raise httpx.ReadTimeout(KEY)
    with pytest.raises(MediaProviderError) as missing:
        await FalKlingVideoAdapter("", transport=httpx.MockTransport(handler)).submit(REQUEST, png())
    assert missing.value.code == "media_credentials_missing" and not calls
    with pytest.raises(MediaProviderError) as error:
        await FalKlingVideoAdapter(KEY, transport=httpx.MockTransport(handler)).submit(REQUEST, png())
    assert error.value.submission_unknown and calls == ["POST"]
    assert error.value.__context__ is None and KEY not in str(error.value)


@pytest.mark.asyncio
async def test_bounded_response_and_invalid_persisted_poll_never_followed():
    calls = []
    def handler(req):
        calls.append(req.method)
        return httpx.Response(200, content=b"x" * 65537)
    adapter = FalKlingVideoAdapter(KEY, transport=httpx.MockTransport(handler))
    with pytest.raises(MediaProviderError):
        await adapter.poll(OP, "https://evil.example/status")
    assert not calls
    with pytest.raises(MediaProviderError) as error:
        await adapter.submit(REQUEST, png())
    assert error.value.code == "media_provider_response_too_large" and error.value.submission_unknown
    assert calls == ["POST"]


def test_immutable_video_checksum_reload(tmp_path, monkeypatch):
    from services.media.video_storage import video_path
    monkeypatch.setenv("BEN_PROJECTS_DATA_DIR", str(tmp_path))
    org, resource = uuid.uuid4(), uuid.uuid4()
    stored = ingest_mp4(video(), org_id=org, resource_id=resource, duration_seconds=3)
    assert video_path(org, resource)[1].read_bytes() == video()
    assert ingest_mp4(video(), org_id=org, resource_id=resource, duration_seconds=3) == stored
    with pytest.raises(ValueError):
        ingest_mp4(mp4(frames=72, audio=True), org_id=org, resource_id=resource, duration_seconds=3)
    assert video_path(org, resource)[1].read_bytes() == video()
