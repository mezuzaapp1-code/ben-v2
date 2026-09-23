"""BFL wire contract and hostile-response tests. MockTransport only, no paid calls."""
import json
from dataclasses import replace

import httpx
import pytest

from services.media.bfl_image import BflImageAdapter, ENDPOINT, checked_url, reported_usage
from services.media.contracts import ImageRequest, BFL_IMAGE_MODEL, GEMINI_IMAGE_MODEL, MediaProviderError
from services.media.accounting import account
from services.media.metadata import request_snapshot, result_observation
from tests.test_media_image_foundation import png

KEY = "fake-bfl-test-credential"
OP = "task-proof"
POLL = "https://api.eu2.bfl.ai/v1/get_result?id=task-proof"
SAMPLE = "https://delivery.eu2.bfl.ai/image.png?token=private-test-only"
REQUEST = ImageRequest(BFL_IMAGE_MODEL, "A red chair")


def response(data, status=200):
    return httpx.Response(status, stream=httpx.ByteStream(json.dumps(data).encode()))


@pytest.mark.asyncio
@pytest.mark.parametrize("ratio,size", [("1:1", (1024,1024)), ("16:9", (1024,576)), ("9:16", (576,1024))])
async def test_exact_dispatch_poll_download_and_credit_accounting(ratio, size):
    calls = []
    def handler(req):
        calls.append(req)
        if req.method == "POST":
            assert str(req.url) == ENDPOINT and req.headers["x-key"] == KEY
            assert json.loads(req.content) == {"prompt": REQUEST.prompt, "width": size[0], "height": size[1],
                "output_format": "png", "disable_pup": True}
            return response({"id": OP, "polling_url": POLL, "cost": 3, "output_mp": 1.0, "secret": KEY})
        if str(req.url) == POLL:
            assert req.headers["x-key"] == KEY
            return response({"id": OP, "status": "Ready", "cost": 3, "result": {"sample": SAMPLE}})
        assert str(req.url) == SAMPLE and "x-key" not in req.headers and "authorization" not in req.headers
        return httpx.Response(200, stream=httpx.ByteStream(png()))
    adapter = BflImageAdapter(KEY, transport=httpx.MockTransport(handler))
    submitted = await adapter.submit(replace(REQUEST, aspect_ratio=ratio))
    status, sample, usage = await adapter.poll(submitted.operation_ref, submitted.polling_url)
    result = await adapter.download(submitted.operation_ref, sample, usage)
    assert status == "Ready" and len(calls) == 3 and result.data == png()
    assert result.returned_model == BFL_IMAGE_MODEL and result.operation_ref == OP
    observation = result_observation(result)
    assert observation["provider"] == "bfl" and observation["model_identity_source"] == "exact_dispatch_endpoint"
    assert SAMPLE not in json.dumps(observation) and KEY not in json.dumps(observation)
    assert observation["training_status"] == "not_approved"
    priced = account(result.usage, width=size[0], height=size[1], model=BFL_IMAGE_MODEL)
    assert str(priced["estimated_cost"]) == "0.03" and priced["actual_charge"] is None
    assert result.usage["provider_usage"]["cost_stage"] == "settled"


@pytest.mark.parametrize("url", ["http://api.bfl.ai/v1/get_result?id=task-proof", "https://127.0.0.1/",
    "https://api.bfl.ai.evil.test/v1/get_result?id=task-proof", "https://evil@api.bfl.ai/v1/get_result?id=task-proof",
    "https://api.bfl.ai:444/v1/get_result?id=task-proof", "https://api.bfl.ai/v1/get_result?id=other",
    "https://api.bfl.ai/v1/get_result?id=task-proof&id=other", "https://api.bfl.ai/v1/get_result?id=task-proof#fragment",
    "https://api.bfl.ai/v1/flux-2-pro", None])
def test_poll_url_restrictions(url):
    with pytest.raises(MediaProviderError):
        checked_url(url, polling=True, operation=OP)


@pytest.mark.parametrize("url", ["http://delivery.eu2.bfl.ai/a", "https://delivery.eu2.bfl.ai.evil.test/a",
    "https://api.bfl.ai/a", "https://delivery.eu2.bfl.ai:8080/a", "https://localhost/a", "file:///secret"])
def test_delivery_url_restrictions(url):
    with pytest.raises(MediaProviderError):
        checked_url(url)


@pytest.mark.asyncio
@pytest.mark.parametrize("code,uncertain", [(400,False),(401,False),(402,False),(403,False),(404,False),
    (422,False),(429,False),(302,True),(500,True)])
async def test_submit_never_retries_and_redacts_provider_errors(code, uncertain):
    calls = []
    def handler(req):
        calls.append(req)
        return response({"secret": KEY}, code)
    with pytest.raises(MediaProviderError) as error:
        await BflImageAdapter(KEY, transport=httpx.MockTransport(handler)).submit(REQUEST)
    assert len(calls) == 1 and error.value.submission_unknown == uncertain
    assert KEY not in str(error.value) and error.value.__context__ is None


@pytest.mark.asyncio
async def test_invalid_identity_and_missing_key_never_dispatch():
    def handler(_):
        pytest.fail("unexpected network call")
    adapter = BflImageAdapter(KEY, transport=httpx.MockTransport(handler))
    for request in (replace(REQUEST, model=GEMINI_IMAGE_MODEL), replace(REQUEST, model="flux-2-pro-preview")):
        with pytest.raises(MediaProviderError):
            await adapter.submit(request)
    with pytest.raises(MediaProviderError):
        await BflImageAdapter("", transport=httpx.MockTransport(handler)).submit(REQUEST)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["Pending", "Reasoning", "Generating", "Error", "Failed", "Request Moderated", "Content Moderated", "Task not found"])
async def test_status_mapping_does_not_download(status):
    adapter = BflImageAdapter(KEY, transport=httpx.MockTransport(lambda r: response({"id": OP, "status": status})))
    actual, sample, _ = await adapter.poll(OP, POLL)
    assert actual == status and sample is None


@pytest.mark.asyncio
@pytest.mark.parametrize("data", [{"id":"other","status":"Ready"}, {"id":OP,"status":"unknown"},
    {"id":OP,"status":"Ready","result":{"sample":"http://localhost/secret"}}, []])
async def test_untrusted_poll_responses_fail_closed(data):
    with pytest.raises(MediaProviderError):
        await BflImageAdapter(KEY, transport=httpx.MockTransport(lambda r: response(data))).poll(OP, POLL)


@pytest.mark.asyncio
async def test_download_bounded_no_redirect_no_secret(monkeypatch):
    calls = []
    def handler(req):
        calls.append(req)
        assert "x-key" not in req.headers
        return httpx.Response(302, headers={"location":"http://127.0.0.1/private"})
    with pytest.raises(MediaProviderError):
        await BflImageAdapter(KEY, transport=httpx.MockTransport(handler)).download(OP, SAMPLE, {})
    assert len(calls) == 1
    monkeypatch.setattr("services.media.bfl_image.MAX_IMAGE_BYTES", 10)
    with pytest.raises(MediaProviderError, match="too_large"):
        await BflImageAdapter(KEY, transport=httpx.MockTransport(lambda r: httpx.Response(200,
            stream=httpx.ByteStream(png())))).download(OP, SAMPLE, {})


@pytest.mark.asyncio
async def test_timeout_does_not_retain_secret_request():
    def handler(req):
        raise httpx.ReadTimeout(KEY, request=req)
    with pytest.raises(MediaProviderError) as error:
        await BflImageAdapter(KEY, transport=httpx.MockTransport(handler)).submit(REQUEST)
    assert error.value.submission_unknown and error.value.__context__ is None
    assert KEY not in str(error.value)


def test_unknown_usage_stays_unknown_and_rights_not_approved():
    usage = reported_usage({"cost": float("nan"), "input_mp": True, "output_mp": -1, "url": SAMPLE})
    assert "cost" not in usage["provider_usage"]
    assert account(usage, width=1024, height=1024, model=BFL_IMAGE_MODEL)["estimated_cost"] is None
    snapshot = request_snapshot(REQUEST, conversation_id="thread", workspace_id=None)
    assert snapshot["provider"] == "bfl" and snapshot["rights"]["training_status"] == "not_approved"
