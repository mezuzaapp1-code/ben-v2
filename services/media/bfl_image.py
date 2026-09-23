"""BFL direct FLUX.2 Pro: submit once, resume polling, ingest bounded PNG bytes.

Official API/integration docs verified 2026-09-23. No loop, retry, fallback,
provider URL delivery, or ownership outside the existing media service.
"""
import asyncio
from dataclasses import dataclass, field
import hashlib
import json
import math
import re
import time
from urllib.parse import urlsplit, parse_qs

import httpx

from services.media.contracts import BFL_IMAGE_MODEL, ImageResult, MAX_IMAGE_BYTES, MediaProviderError

ENDPOINT = "https://api.bfl.ai/v1/flux-2-pro"
DIMENSIONS = {"1:1": (1024, 1024), "16:9": (1024, 576), "9:16": (576, 1024)}


def checked_url(value, *, polling=False, operation=None):
    valid = False
    try:
        if isinstance(value, str) and len(value) <= 8192 and not any(ord(c) <= 32 for c in value):
            p = urlsplit(value)
            host = p.hostname or ""
            host_ok = (re.fullmatch(r"api(?:\.[a-z0-9-]+)?\.bfl\.ai", host) if polling else
                       re.fullmatch(r"delivery\.[a-z0-9-]+\.bfl\.ai", host))
            valid = (p.scheme == "https" and host_ok and p.port in (None, 443)
                     and not p.username and not p.password and not p.fragment and "\\" not in value)
            if polling:
                valid = valid and p.path == "/v1/get_result" and parse_qs(p.query).get("id") == [operation]
    except ValueError:
        pass
    if not valid:
        raise MediaProviderError("media_invalid_provider_url", submission_unknown=True)
    return value


def reported_usage(data, *, settled=False):
    clean = {}
    for key in ("cost", "input_mp", "output_mp"):
        value = data.get(key)
        if type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1_000_000:
            clean[key] = value
    return {"schema_version": "media-usage-v1", "provider_usage": {
        "source": "provider_reported", "unit": "credits_and_megapixels",
        "cost_stage": "settled" if settled else "submission", **clean}}


@dataclass(frozen=True)
class BflSubmission:
    operation_ref: str = field(repr=False)
    polling_url: str = field(repr=False)
    usage: dict


class BflImageAdapter:
    def __init__(self, api_key, *, transport=None):
        self._api_key = api_key.strip()
        self._transport = transport

    async def _request(self, method, url, *, payload=None, download=False):
        failure, body = None, bytearray()
        if not download and not self._api_key:
            raise MediaProviderError("media_credentials_unavailable")
        headers = {"Accept-Encoding": "identity", "Accept": "image/png" if download else "application/json"}
        if not download:
            headers["x-key"] = self._api_key
        limit = MAX_IMAGE_BYTES if download else 64 * 1024
        try:
            async with asyncio.timeout(60):
                async with httpx.AsyncClient(transport=self._transport, trust_env=False, follow_redirects=False,
                                            timeout=httpx.Timeout(45, connect=10)) as client:
                    async with client.stream(method, url, headers=headers, json=payload) as response:
                        if response.status_code != 200:
                            code = response.status_code
                            failure = MediaProviderError({400: "media_request_rejected", 401: "media_auth_failed",
                                402: "media_credit_required", 403: "media_access_denied", 404: "media_provider_not_found",
                                422: "media_request_rejected", 429: "media_rate_limited"}.get(code, "media_provider_http_error"),
                                submission_unknown=method == "POST" and code not in (400, 401, 402, 403, 404, 422, 429))
                        elif response.headers.get("content-encoding", "identity").lower() != "identity":
                            failure = MediaProviderError("media_unexpected_encoding", submission_unknown=True)
                        else:
                            async for chunk in response.aiter_raw():
                                if len(body) + len(chunk) > limit:
                                    failure = MediaProviderError("media_response_too_large", submission_unknown=True)
                                    break
                                body.extend(chunk)
        except (httpx.HTTPError, TimeoutError):
            failure = MediaProviderError("media_transport_uncertain", submission_unknown=True)
        if failure:
            raise failure
        if download:
            if not body.startswith(b"\x89PNG\r\n\x1a\n"):
                raise MediaProviderError("media_invalid_image", submission_unknown=True)
            return bytes(body)
        data = None
        try:
            data = json.loads(body)
        except (ValueError, UnicodeError):
            pass
        if not isinstance(data, dict):
            raise MediaProviderError("media_invalid_response", submission_unknown=True)
        return data

    async def submit(self, request):
        request.validate()
        if request.model != BFL_IMAGE_MODEL:
            raise MediaProviderError("unsupported_media_model")
        width, height = DIMENSIONS[request.aspect_ratio]
        data = await self._request("POST", ENDPOINT, payload={"prompt": request.prompt,
            "width": width, "height": height, "output_format": "png", "disable_pup": True})
        operation = data.get("id")
        if not isinstance(operation, str) or not operation.strip() or len(operation) > 2048:
            raise MediaProviderError("media_invalid_operation_reference", submission_unknown=True)
        url = checked_url(data.get("polling_url"), polling=True, operation=operation)
        return BflSubmission(operation, url, reported_usage(data))

    async def poll(self, operation, polling_url):
        url = checked_url(polling_url, polling=True, operation=operation)
        data = await self._request("GET", url)
        if data.get("id") != operation:
            raise MediaProviderError("media_operation_identity_mismatch", submission_unknown=True)
        status = data.get("status")
        if status not in ("Pending", "Reasoning", "Generating", "Ready", "Error", "Failed",
                          "Request Moderated", "Content Moderated", "Task not found"):
            raise MediaProviderError("media_invalid_provider_state", submission_unknown=True)
        usage = reported_usage(data, settled=status in ("Ready", "Error", "Failed", "Request Moderated", "Content Moderated"))
        if status == "Ready":
            result = data.get("result")
            if not isinstance(result, dict):
                raise MediaProviderError("media_invalid_output", submission_unknown=True)
            # Validate before persisting the private temporary URL; never fetch arbitrary origins.
            sample = checked_url(result.get("sample"))
            return status, sample, usage
        return status, None, usage

    async def download(self, operation, sample, usage):
        started = time.monotonic()
        data = await self._request("GET", checked_url(sample), download=True)
        usage = {**usage, "image_count": 1, "image_count_source": "observed_output",
                 "image_encoding": {"schema_version": "media-image-encoding-v1", "source_mime_type": "image/png",
                    "stored_mime_type": "image/png", "source_sha256": hashlib.sha256(data).hexdigest(), "normalization": "identity"},
                 "download_duration_ms": (time.monotonic() - started) * 1000}
        # Poll responses do not attest a model snapshot: identity is the exact POST endpoint.
        return ImageResult(data, "image/png", BFL_IMAGE_MODEL, operation, usage, None)
