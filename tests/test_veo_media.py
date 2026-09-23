"""Deterministic Veo wire contract, secret boundaries, MP4 validation and accounting."""
import base64
import io
import json
import uuid
from functools import lru_cache

import av
import httpx
import pytest

from services.media.contracts import VEO_VIDEO_MODEL, VideoRequest, MediaProviderError
from services.media.veo_video import VeoVideoAdapter, ENDPOINT, BASE, operation_url, download_url, usage
from services.media.video_storage import inspect_mp4, ingest_mp4
from services.media.accounting import account
from tests.test_media_image_foundation import png

KEY = "synthetic-veo-test-key-not-a-credential"
OP = f"models/{VEO_VIDEO_MODEL}/operations/test-operation"
POLL = f"{BASE}/{OP}"
FILE = f"{BASE}/files/test-video:download?alt=media"
REQUEST = VideoRequest(VEO_VIDEO_MODEL, "Synthetic blue square gently moves. No people.", str(uuid.uuid4()))


def response(value, status=200):
    return httpx.Response(status, stream=httpx.ByteStream(json.dumps(value).encode()))


def completed():
    return {"name": OP, "done": True, "response": {"generateVideoResponse": {
        "generatedSamples": [{"video": {"uri": FILE, "mimeType": "video/mp4"}}]}}}


@lru_cache(maxsize=4)
def mp4(width=1280, height=720, frames=96, audio=True):
    # Synthetic pixels, never a provider call or a customer/provider output fixture.
    output = io.BytesIO()
    with av.open(output, "w", format="mp4") as container:
        stream = container.add_stream("libx264", rate=24)
        stream.width, stream.height, stream.pix_fmt = width, height, "yuv420p"
        stream.options = {"preset": "ultrafast", "crf": "40"}
        sound = container.add_stream("aac", rate=48000) if audio else None
        if sound:
            sound.layout = "stereo"
        for _ in range(frames):
            frame = av.VideoFrame(width, height, "yuv420p")
            for plane in frame.planes:
                plane.update(bytes([80]) * plane.buffer_size)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
        if sound:
            total = frames * 2000
            for start in range(0, total, 1024):
                frame = av.AudioFrame(format="fltp", layout="stereo", samples=min(1024, total-start))
                frame.sample_rate, frame.pts = 48000, start
                for plane in frame.planes:
                    plane.update(bytes(plane.buffer_size))
                for packet in sound.encode(frame):
                    container.mux(packet)
            for packet in sound.encode():
                container.mux(packet)
    return output.getvalue()


@pytest.mark.asyncio
async def test_exact_request_operation_poll_and_download():
    calls = []
    def handler(req):
        calls.append(req)
        assert req.headers["x-goog-api-key"] == KEY
        if req.method == "POST":
            assert str(req.url) == ENDPOINT
            body = json.loads(req.content)
            assert body == {"instances": [{"prompt": REQUEST.prompt, "image": {
                "bytesBase64Encoded": base64.b64encode(png()).decode(), "mimeType": "image/png"}}],
                "parameters": {"sampleCount": 1, "durationSeconds": 4, "aspectRatio": "16:9",
                               "resolution": "720p", "personGeneration": "allow_adult"}}
            return response({"name": OP})
        if str(req.url) == POLL:
            return response(completed())
        assert str(req.url) == FILE
        return httpx.Response(200, content=mp4())
    adapter = VeoVideoAdapter(KEY, transport=httpx.MockTransport(handler))
    submit = await adapter.submit(REQUEST, png())
    status, sample, dimensions = await adapter.poll(submit.operation_ref, submit.polling_url)
    assert status == "Ready"
    result = await adapter.download(OP, sample, dimensions)
    assert result.returned_model == VEO_VIDEO_MODEL and result.mime_type == "video/mp4"
    assert inspect_mp4(result.data) == (1280, 720, 4.0, True)
    assert result.usage["provider_usage"] is None and [c.method for c in calls] == ["POST", "GET", "GET"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status,unknown", [(400, False), (401, False), (403, False), (429, False), (500, True), (503, True)])
async def test_submit_rejection_no_retry_and_no_secret(status, unknown):
    calls = []
    def handler(req):
        calls.append(req)
        return response({"error": KEY}, status)
    with pytest.raises(MediaProviderError) as error:
        await VeoVideoAdapter(KEY, transport=httpx.MockTransport(handler)).submit(REQUEST, png())
    assert error.value.submission_unknown == unknown and len(calls) == 1
    assert KEY not in str(error.value) and error.value.__context__ is None


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{}, {"name": "operations/wrong"}, {"name": "models/other/operations/test"}])
async def test_accepted_malformed_reference_is_unknown(payload):
    with pytest.raises(MediaProviderError) as error:
        await VeoVideoAdapter(KEY, transport=httpx.MockTransport(lambda _: response(payload))).submit(REQUEST, png())
    assert error.value.submission_unknown


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {"name": OP, "done": "true"}, {"name": "wrong", "done": False},
    {"name": OP, "done": True}, {"name": OP, "done": False, "response": {}},
    {"name": OP, "done": True, "response": {"generateVideoResponse": {"generatedSamples": []}}},
])
async def test_malformed_poll_is_not_success(payload):
    with pytest.raises(MediaProviderError):
        await VeoVideoAdapter(KEY, transport=httpx.MockTransport(lambda _: response(payload))).poll(OP, POLL)


@pytest.mark.parametrize("value", ["https://evil.example/file", "http://generativelanguage.googleapis.com/v1beta/files/a",
    f"{BASE}/files/a?key=secret", f"{BASE}/files/../a", f"{BASE}/files/a#x", f"{BASE}/files/a%2fb"])
def test_untrusted_download_rejected(value):
    with pytest.raises(MediaProviderError):
        download_url(value)


@pytest.mark.asyncio
async def test_download_redirect_strips_credential_and_rejects_external_host():
    calls = []
    def handler(req):
        calls.append(req)
        if len(calls) == 1:
            return httpx.Response(302, headers={"location": "https://storage.googleapis.com/test/video.mp4"})
        assert "x-goog-api-key" not in req.headers
        return httpx.Response(200, content=mp4())
    await VeoVideoAdapter(KEY, transport=httpx.MockTransport(handler)).download(OP, FILE, usage())
    bad = VeoVideoAdapter(KEY, transport=httpx.MockTransport(lambda _: httpx.Response(302, headers={"location": "https://evil.example"})))
    with pytest.raises(MediaProviderError, match="media_invalid_download_redirect"):
        await bad.download(OP, FILE, usage())


@pytest.mark.parametrize("data", [b"", b"1234ftypnot-a-video", b"<html>not video</html>"])
def test_invalid_mp4(data):
    with pytest.raises(ValueError):
        inspect_mp4(data)


def test_decode_detects_wrong_dimensions_duration_and_truncation():
    for data in (mp4(640, 360), mp4(frames=24), mp4()[:100]):
        with pytest.raises(ValueError):
            inspect_mp4(data)
    assert inspect_mp4(mp4(720, 1280), aspect_ratio="9:16")[:2] == (720, 1280)


def test_immutable_publication_and_video_seconds_accounting(tmp_path, monkeypatch):
    monkeypatch.setenv("BEN_PROJECTS_DATA_DIR", str(tmp_path))
    org, resource = uuid.uuid4(), uuid.uuid4()
    result = ingest_mp4(mp4(), org_id=org, resource_id=resource)
    assert result == ingest_mp4(mp4(), org_id=org, resource_id=resource)
    assert result.mime_type == "video/mp4" and result.storage_key.endswith("output.mp4")
    costs = account({**usage(), "duration_seconds": result.duration_seconds}, width=1280, height=720, model=VEO_VIDEO_MODEL)
    assert str(costs["estimated_cost"]) == "0.400" and costs["actual_charge"] is None
    assert costs["usage_dimensions"]["provider_usage"] is None
    assert account(usage(), width=1280, height=720, model=VEO_VIDEO_MODEL)["estimated_cost"] is None


@pytest.mark.parametrize("changes", [{"duration_seconds": 8}, {"duration_seconds": True}, {"resolution": "1080p"},
                                      {"model": "veo-3.1-generate-preview"}, {"aspect_ratio": "1:1"}])
def test_narrow_admission(changes):
    with pytest.raises(MediaProviderError):
        VideoRequest(**{**REQUEST.__dict__, **changes}).validate()
