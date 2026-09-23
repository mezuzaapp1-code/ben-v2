"""Mock-only rehearsal of the exact paid-proof harness and its one-POST guard."""
import json

import httpx
import pytest

from tests.test_media_repository import repository
from tests.test_veo_live_proof import run_proof, OneGenerationTransport
from tests.test_veo_media import OP, POLL, FILE, response, completed, mp4
from services.media.veo_video import ENDPOINT


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["success", "rejection", "ingestion_failure"])
async def test_exact_proof_harness_without_network(repository, monkeypatch, tmp_path, outcome):
    monkeypatch.chdir(tmp_path)
    calls = []
    def handler(req):
        calls.append(req.method)
        if req.method == "POST":
            if outcome == "rejection":
                return response({"error": {"code": 403, "status": "PERMISSION_DENIED",
                    "message": "synthetic-secret service disabled", "details": [{"reason": "SERVICE_DISABLED"}]}}, 403)
            return response({"name": OP})
        if str(req.url) == POLL:
            return response(completed())
        assert str(req.url) == FILE
        return httpx.Response(200, content=mp4() if outcome == "success" else b"invalid video")
    kwargs = dict(inner_factory=lambda: httpx.MockTransport(handler), credential="synthetic-secret")
    if outcome == "success":
        await run_proof(repository, monkeypatch, tmp_path, **kwargs)
    else:
        with pytest.raises(AssertionError):
            await run_proof(repository, monkeypatch, tmp_path, **kwargs)
    text = (tmp_path / "proof-evidence/proof.json").read_text()
    evidence = json.loads(text)
    assert calls.count("POST") == evidence["metrics"]["generation_requests"] == 1
    assert "synthetic-secret" not in text and OP not in text and FILE not in text
    assert evidence["status"] == ("PASS" if outcome == "success" else "FAIL")
    if outcome == "rejection":
        assert calls == ["POST"] and evidence["state"] == "failed"
        assert evidence["metrics"]["provider_error"]["reasons"] == ["SERVICE_DISABLED"]
    if outcome == "ingestion_failure":
        assert evidence["state"] == "ingesting" and evidence["error_code"] == "media_ingestion_failed"
        assert evidence["metrics"]["provider_done"] is True and calls == ["POST", "GET", "GET"]


@pytest.mark.asyncio
async def test_transport_blocks_second_post_before_network():
    calls = []
    def handler(req):
        calls.append(req)
        return response({"name": OP})
    metrics = {}
    transport = OneGenerationTransport(metrics, lambda: httpx.MockTransport(handler))
    body = {"parameters": {"sampleCount": 1, "durationSeconds": 4, "aspectRatio": "16:9",
                           "resolution": "720p", "personGeneration": "allow_adult"},
            "instances": [{"image": {"mimeType": "image/png"}}]}
    async with httpx.AsyncClient(transport=transport) as client:
        assert (await client.post(ENDPOINT, json=body)).status_code == 200
        with pytest.raises(AssertionError, match="Second generation forbidden"):
            await client.post(ENDPOINT, json=body)
    assert len(calls) == metrics["generation_requests"] == 1
