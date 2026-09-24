"""No-network rehearsal of the exact protected Kling proof and spend guards."""
import base64
import gzip
import json

import httpx
import pytest

from tests.test_media_repository import repository
from tests.test_kling_live_proof import run_proof, OneGenerationTransport, synthetic_png
from tests.test_kling_media import OP, POLL, RESULT, FILE, response, accepted, completed, output, video
from tests.test_veo_media import mp4
from services.media.fal_kling_video import ENDPOINT


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["success", "rejection", "ingestion_failure", "wrong_resolution", "poll_failure"])
async def test_exact_proof_harness_without_network(repository, monkeypatch, tmp_path, outcome):
    monkeypatch.chdir(tmp_path)
    calls = []
    def handler(req):
        calls.append((req.method, str(req.url)))
        if req.method == "POST":
            if outcome == "rejection":
                return response({"detail": "synthetic-secret Insufficient credits"}, 403)
            return response(accepted())
        if str(req.url) == POLL:
            if outcome == "poll_failure":
                return response({"detail": "synthetic-secret"}, 503)
            # Instrumentation must inspect decoded JSON even with compressed wire data.
            return httpx.Response(200, content=gzip.compress(json.dumps(completed()).encode()),
                                  headers={"content-encoding": "gzip"})
        if str(req.url) == RESULT:
            return response(output())
        assert str(req.url) == FILE
        data = mp4(width=640, height=360, frames=72, audio=False) if outcome == "wrong_resolution" else video()
        return httpx.Response(200, content=b"invalid video" if outcome == "ingestion_failure" else data)
    kwargs = dict(inner_factory=lambda: httpx.MockTransport(handler), credential="synthetic-secret")
    if outcome == "success":
        await run_proof(repository, monkeypatch, tmp_path, **kwargs)
    else:
        with pytest.raises(AssertionError):
            await run_proof(repository, monkeypatch, tmp_path, **kwargs)
    text = (tmp_path / "proof-evidence/proof.json").read_text()
    evidence = json.loads(text)
    assert sum(method == "POST" for method, _ in calls) == evidence["metrics"]["generation_requests"] == 1
    assert "synthetic-secret" not in text and OP not in text and FILE not in text
    assert evidence["status"] == ("PASS" if outcome == "success" else "FAIL")
    if outcome == "rejection":
        assert calls == [("POST", ENDPOINT)] and evidence["state"] == "failed"
        assert evidence["metrics"]["provider_error"]["categories"] == ["credits"]
    if outcome in ("ingestion_failure", "wrong_resolution"):
        assert evidence["state"] == "ingesting" and evidence["error_code"] == "media_ingestion_failed"
        assert evidence["metrics"]["provider_done"] is True
    if outcome in ("success", "ingestion_failure", "wrong_resolution"):
        assert evidence["metrics"]["download_requests"] == evidence["metrics"]["result_requests"] == 1
        assert evidence["metrics"]["submission_to_ready_ms"] >= 0
    if outcome == "wrong_resolution":
        assert evidence["metrics"]["actual_output"]["width"] == 640
        assert evidence["metrics"]["actual_output"]["height"] == 360
    if outcome == "success":
        assert evidence["metrics"]["actual_output"] == {"width": 1280, "height": 720,
            "duration_seconds": 3.0, "aspect_ratio": "16:9", "audio_present": False, "byte_size": len(video())}
        assert evidence["estimated_cost"] == "0.25200000"
    if outcome == "poll_failure":
        assert evidence["metrics"]["download_requests"] == 0 and len(calls) == 2


@pytest.mark.asyncio
async def test_transport_blocks_second_generation_and_download_before_network():
    calls = []
    def handler(req):
        calls.append(req)
        return response(accepted()) if req.method == "POST" else httpx.Response(200, content=b"test")
    metrics = {}
    transport = OneGenerationTransport(metrics, lambda: httpx.MockTransport(handler))
    body = {"prompt": "synthetic", "image_url": "data:image/png;base64," + base64.b64encode(synthetic_png()).decode(),
            "duration": "3", "generate_audio": False}
    async with httpx.AsyncClient(transport=transport) as client:
        assert (await client.post(ENDPOINT, json=body, headers={"X-Fal-No-Retry": "1"})).status_code == 200
        with pytest.raises(AssertionError, match="Second generation forbidden"):
            await client.post(ENDPOINT, json=body)
        assert (await client.get(FILE)).status_code == 200
        with pytest.raises(AssertionError, match="Second download forbidden"):
            await client.get(FILE)
    assert len(calls) == 2 and metrics["generation_requests"] == metrics["download_requests"] == 1
