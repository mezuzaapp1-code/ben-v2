"""fal Kling O3 Standard: one queue submission, bounded GET-only reconciliation.

Official endpoint/OpenAPI + fal client queue URL normalization checked 2026-09-24.
No SDK retries, webhooks, upload service, public provider references or fallback.
"""
import asyncio
import base64
import io
import json
import math
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx
from PIL import Image

from services.media.contracts import (KLING_VIDEO_MODEL, MAX_IMAGE_BYTES, MAX_VIDEO_BYTES,
    VideoRequest, VideoResult, MediaProviderError)

ENDPOINT = f"https://queue.fal.run/{KLING_VIDEO_MODEL}"
QUEUE_ROOT = "https://queue.fal.run/fal-ai/kling-video"


def operation_url(reference, polling_url=None):
    if not isinstance(reference, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", reference):
        raise MediaProviderError("media_invalid_operation")
    # SDK uses app root; endpoint OpenAPI also documents the full endpoint form.
    allowed = [f"{root}/requests/{reference}/status" for root in (QUEUE_ROOT, ENDPOINT)]
    if polling_url is not None and polling_url not in allowed:
        raise MediaProviderError("media_invalid_operation")
    return polling_url or allowed[0]


def download_url(value):
    try:
        u = urlsplit(value)
        valid = (isinstance(value, str) and len(value) <= 4096 and u.scheme == "https"
                 and u.port in (None, 443) and not u.username and not u.password and not u.fragment
                 and (u.hostname == "fal.media" or (u.hostname or "").endswith(".fal.media"))
                 and not any(ord(c) < 33 or c == chr(92) for c in value))
    except (TypeError, ValueError, AttributeError):
        valid = False
    if not valid:
        raise MediaProviderError("media_invalid_download")
    return value


def usage(payload=None):
    result = {"schema_version": "media-usage-v1", "provider_usage": None,
              "provider_usage_missing_reason": "not_reported", "actual_charge": None}
    metrics = payload.get("metrics") if isinstance(payload, dict) else None
    seconds = metrics.get("inference_time") if isinstance(metrics, dict) else None
    if type(seconds) in (int, float) and math.isfinite(seconds) and 0 <= seconds <= 86400:
        result["provider_timing"] = {"inference_seconds": seconds, "source": "provider_reported"}
    return result


def validate_source(image, aspect_ratio):
    valid = False
    try:
        if not image or len(image) > MAX_IMAGE_BYTES:
            raise ValueError()
        with Image.open(io.BytesIO(image)) as source:
            w, h = source.size
            a, b = map(int, aspect_ratio.split(":"))
            valid = (source.format == "PNG" and min(w, h) >= 300 and w*h <= 20_000_000
                     and w*b == h*a and getattr(source, "n_frames", 1) == 1)
            source.verify()
    except (ValueError, OSError, SyntaxError, Image.DecompressionBombError):
        valid = False
    if not valid:
        raise MediaProviderError("invalid_media_source")


@dataclass(frozen=True)
class FalSubmission:
    operation_ref: str = field(repr=False)
    polling_url: str = field(repr=False)
    usage: dict


class FalKlingVideoAdapter:
    def __init__(self, api_key, *, transport=None):
        self._key = api_key
        self._transport = transport

    async def _request(self, method, url, *, body=None, limit=65536, authenticated=True):
        if not self._key:
            raise MediaProviderError("media_credentials_missing")
        headers = {"Authorization": f"Key {self._key}"} if authenticated else {}
        if method == "POST":
            headers.update({"X-Fal-No-Retry": "1", "X-Fal-Request-Timeout": "1800",
                "X-Fal-Store-IO": "0",
                "X-Fal-Object-Lifecycle-Preference": '{"expiration_duration_seconds":3600}'})
        code, unknown = "media_provider_transport", method == "POST"
        try:
            # At most 2 small GETs + one file GET + 30s decode fit the existing 2m lease.
            async with asyncio.timeout(25):
                async with httpx.AsyncClient(transport=self._transport, trust_env=False,
                        follow_redirects=False, timeout=httpx.Timeout(20, connect=10)) as client:
                    async with client.stream(method, url, json=body, headers=headers) as response:
                        if response.status_code not in ((200, 202) if method == "POST" else (200,)):
                            code = "media_provider_rejected" if method == "POST" else "media_provider_fetch_failed"
                            unknown = method == "POST" and response.status_code not in (400, 401, 403, 404, 422, 429)
                            if method == "GET" and response.status_code in (404, 410):
                                code = "media_provider_expired"
                            if method == "GET" and authenticated and response.status_code == 422:
                                code = "media_provider_failed"
                        else:
                            parts, size = [], 0
                            async for chunk in response.aiter_bytes():
                                size += len(chunk)
                                if size > limit:
                                    code = "media_provider_response_too_large"
                                    break
                                parts.append(chunk)
                            else:
                                return b"".join(parts)
        except (httpx.HTTPError, TimeoutError, ValueError):
            pass
        # No raw exception/response retained: they can contain keys, URLs or prompts.
        raise MediaProviderError(code, submission_unknown=unknown)

    async def _json(self, method, url, body=None):
        raw = await self._request(method, url, body=body)
        value = None
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeError):
            pass
        if not isinstance(value, dict):
            raise MediaProviderError("media_provider_malformed", submission_unknown=method == "POST")
        return value

    async def submit(self, request: VideoRequest, image: bytes):
        request.validate()
        if request.model != KLING_VIDEO_MODEL:
            raise MediaProviderError("unsupported_media_model")
        validate_source(image, request.aspect_ratio)
        # I2V exposes neither aspect_ratio nor resolution. BEN validates the input
        # ratio and decoded output; never send invented provider parameters.
        result = await self._json("POST", ENDPOINT, {"prompt": request.prompt,
            "image_url": "data:image/png;base64," + base64.b64encode(image).decode(),
            "duration": str(request.duration_seconds), "generate_audio": False})
        reference = result.get("request_id")
        try:
            poll = operation_url(reference, result.get("status_url"))
            if result.get("status_url") is None or result.get("response_url") != poll.removesuffix("/status"):
                raise MediaProviderError("media_invalid_operation")
        except MediaProviderError:
            raise MediaProviderError("media_invalid_operation", submission_unknown=True) from None
        return FalSubmission(reference, poll, usage())

    async def poll(self, operation_ref, polling_url):
        if polling_url is None:
            raise MediaProviderError("media_invalid_operation")
        canonical = operation_url(operation_ref, polling_url)
        result = await self._json("GET", canonical)
        if result.get("request_id", operation_ref) != operation_ref:
            raise MediaProviderError("media_provider_malformed")
        state = result.get("status")
        if state in ("IN_QUEUE", "IN_PROGRESS"):
            return "Pending", None, usage(result)
        if state != "COMPLETED":
            raise MediaProviderError("media_provider_malformed")
        if result.get("error") is not None or result.get("error_type") is not None:
            return "Failed", None, usage(result)
        output = await self._json("GET", canonical.removesuffix("/status"))
        video = output.get("video")
        if not isinstance(video, dict) or video.get("content_type", "video/mp4") not in (None, "video/mp4"):
            raise MediaProviderError("media_provider_malformed")
        size = video.get("file_size")
        if size is not None and (type(size) is not int or not 0 < size <= MAX_VIDEO_BYTES):
            raise MediaProviderError("media_provider_response_too_large")
        return "Ready", download_url(video.get("url")), usage(result)

    async def download(self, operation_ref, sample, dimensions):
        operation_url(operation_ref)
        data = await self._request("GET", download_url(sample), limit=MAX_VIDEO_BYTES, authenticated=False)
        return VideoResult(data, "video/mp4", KLING_VIDEO_MODEL, operation_ref, dimensions, None)
