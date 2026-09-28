"""Exact Gemini Developer API Veo boundary. One POST, resumable GETs, no fallback.

REST encoding follows Google's python-genai models/files converters (2026-09-23).
No SDK automatic retries, public operation names or provider URL delivery.
"""
import asyncio
import base64
import json
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urljoin

import httpx

from services.media.contracts import (VEO_VIDEO_MODEL, MAX_IMAGE_BYTES, MAX_VIDEO_BYTES,
    VideoRequest, VideoResult, MediaProviderError)

BASE = "https://generativelanguage.googleapis.com/v1beta"
ENDPOINT = f"{BASE}/models/{VEO_VIDEO_MODEL}:predictLongRunning"
OPERATION = re.compile(r"models/" + re.escape(VEO_VIDEO_MODEL) + r"/operations/[A-Za-z0-9_-]{1,256}")


def operation_url(name):
    if not isinstance(name, str) or not OPERATION.fullmatch(name):
        raise MediaProviderError("media_invalid_operation")
    return f"{BASE}/{name}"


def download_url(value):
    # Reconstruct the documented files download route rather than forwarding
    # arbitrary provider query parameters or a provider-controlled host.
    if not isinstance(value, str) or len(value) > 2048:
        raise MediaProviderError("media_invalid_download")
    match = re.fullmatch(re.escape(BASE) + r"/files/([A-Za-z0-9_-]{1,256})(?::download)?(?:\?alt=media)?", value)
    if not match:
        raise MediaProviderError("media_invalid_download")
    return f"{BASE}/files/{match[1]}:download?alt=media"


def redirect_url(value):
    try:
        u = urlsplit(value)
        valid = (u.scheme == "https" and u.port in (None, 443) and not u.username and not u.password
                 and not u.fragment and (u.hostname == "storage.googleapis.com" or
                 (u.hostname or "").endswith(".googleusercontent.com"))
                 and not any(ord(c) < 33 or c == chr(92) for c in value))
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise MediaProviderError("media_invalid_download_redirect")
    return value


def usage():
    # This API does not document invoice/credit/token usage on its LRO response.
    return {"schema_version": "media-usage-v1", "provider_usage": None,
            "provider_usage_missing_reason": "not_reported", "actual_charge": None}


@dataclass(frozen=True)
class VeoSubmission:
    operation_ref: str = field(repr=False)
    polling_url: str = field(repr=False)
    usage: dict


class VeoVideoAdapter:
    def __init__(self, api_key, *, transport=None):
        self._key = api_key
        self._transport = transport

    async def _request(self, method, url, *, body=None, limit=65536, authenticated=True):
        if not self._key:
            raise MediaProviderError("media_credentials_missing")
        code, unknown = "media_provider_transport", method == "POST"
        try:
            async with asyncio.timeout(70):
                async with httpx.AsyncClient(transport=self._transport, trust_env=False,
                        follow_redirects=False, timeout=httpx.Timeout(60, connect=10)) as client:
                    for hop in range(4):
                        headers = {"x-goog-api-key": self._key} if authenticated else {}
                        async with client.stream(method, url, json=body, headers=headers) as response:
                            if method == "GET" and limit == MAX_VIDEO_BYTES and response.status_code in (301, 302, 303, 307, 308):
                                url = redirect_url(urljoin(url, response.headers.get("location", "")))
                                # Never send the API key to a redirected host, including Google storage.
                                authenticated = False
                                continue
                            if response.status_code != 200:
                                code = "media_provider_rejected" if method == "POST" else "media_provider_fetch_failed"
                                unknown = method == "POST" and response.status_code not in (400, 401, 403, 404, 422, 429)
                                if method == "GET" and response.status_code in (404, 410):
                                    code = "media_provider_expired"
                                break
                            parts, size = [], 0
                            async for chunk in response.aiter_bytes():
                                size += len(chunk)
                                if size > limit:
                                    code = "media_provider_response_too_large"
                                    break
                                parts.append(chunk)
                            else:
                                return b"".join(parts)
                            break
        except MediaProviderError:
            raise
        except (httpx.HTTPError, TimeoutError, ValueError):
            pass
        # Drop transport exception context, which can retain headers and content.
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
        if not image or len(image) > MAX_IMAGE_BYTES or not image.startswith(b"\x89PNG\r\n\x1a\n"):
            raise MediaProviderError("invalid_media_source")
        body = {"instances": [{"prompt": request.prompt, "image": {
            "bytesBase64Encoded": base64.b64encode(image).decode(), "mimeType": "image/png"}}],
            "parameters": {"sampleCount": 1, "durationSeconds": request.duration_seconds,
                "aspectRatio": request.aspect_ratio, "resolution": request.resolution,
                "personGeneration": "allow_adult"}}
        response = await self._json("POST", ENDPOINT, body)
        name = response.get("name")
        try:
            poll = operation_url(name)
        except MediaProviderError:
            raise MediaProviderError("media_invalid_operation", submission_unknown=True) from None
        return VeoSubmission(name, poll, usage())

    async def poll(self, operation_ref, polling_url):
        canonical = operation_url(operation_ref)
        if polling_url != canonical:
            raise MediaProviderError("media_invalid_operation")
        result = await self._json("GET", canonical)
        if result.get("name") != operation_ref or type(result.get("done", False)) is not bool:
            raise MediaProviderError("media_provider_malformed")
        if not result.get("done", False):
            if "error" in result or "response" in result:
                raise MediaProviderError("media_provider_malformed")
            return "Pending", None, usage()
        if "error" in result:
            return "Failed", None, usage()
        response = result.get("response")
        video = response.get("generateVideoResponse") if isinstance(response, dict) else None
        if not isinstance(video, dict):
            raise MediaProviderError("media_provider_malformed")
        samples = video.get("generatedSamples")
        if video.get("raiMediaFilteredCount"):
            return "Content Moderated", None, usage()
        if not isinstance(samples, list) or len(samples) != 1 or not isinstance(samples[0], dict):
            raise MediaProviderError("media_provider_malformed")
        media = samples[0].get("video")
        if not isinstance(media, dict) or media.get("mimeType", "video/mp4") != "video/mp4":
            raise MediaProviderError("media_provider_malformed")
        return "Ready", download_url(media.get("uri")), usage()

    async def download(self, operation_ref, sample, dimensions):
        operation_url(operation_ref)
        data = await self._request("GET", download_url(sample), limit=MAX_VIDEO_BYTES)
        return VideoResult(data, "video/mp4", VEO_VIDEO_MODEL, operation_ref, dimensions, None)
