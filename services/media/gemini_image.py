"""Exact Gemini image dispatch via the documented Interactions REST API.

    https://ai.google.dev/gemini-api/docs/image-generation
    https://ai.google.dev/api/interactions-api
Verified 2026-09-22. No tools, chat context, automatic retries, or fallback.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import json
import time

import httpx

from services.media.contracts import (
    ImageRequest, ImageResult, MAX_IMAGE_BYTES, MAX_RESPONSE_BYTES,
    MediaProviderError, normalize_usage,
)

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/interactions"


class GeminiImageAdapter:
    def __init__(self, api_key: str):
        # Credential resolution belongs to the trusted composition root.
        self._api_key = api_key.strip()

    async def generate(self, request: ImageRequest, *, transport=None) -> ImageResult:
        request.validate()
        if not self._api_key:
            raise MediaProviderError("media_credentials_unavailable")
        payload = {
            "model": request.model, "input": request.prompt,
            "store": False, "background": False, "stream": False,
            "response_format": {"type": "image", "mime_type": "image/png",
                                "aspect_ratio": request.aspect_ratio,
                                "image_size": request.image_size},
        }
        started = time.monotonic()
        # Do not propagate HTTP exceptions: they retain credential-bearing requests.
        failure = None
        body = bytearray()
        try:
            async with asyncio.timeout(180):
                async with httpx.AsyncClient(
                    transport=transport, follow_redirects=False, trust_env=False,
                    timeout=httpx.Timeout(150, connect=10),
                ) as client:
                    async with client.stream(
                        "POST", ENDPOINT, json=payload,
                        headers={"x-goog-api-key": self._api_key,
                                 "Accept": "application/json", "Accept-Encoding": "identity"},
                    ) as response:
                        if response.status_code != 200:
                            code = response.status_code
                            failure = MediaProviderError(
                                {400: "media_request_rejected", 401: "media_auth_failed",
                                 403: "media_access_denied", 404: "media_model_unavailable",
                                 429: "media_rate_limited"}.get(code, "media_provider_http_error"),
                                submission_unknown=code not in (400, 401, 403, 404, 429),
                            )
                        elif response.headers.get("content-encoding", "identity").lower() != "identity":
                            failure = MediaProviderError("media_unexpected_encoding", submission_unknown=True)
                        else:
                            async for chunk in response.aiter_raw():
                                if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                                    failure = MediaProviderError("media_response_too_large", submission_unknown=True)
                                    break
                                body.extend(chunk)
        except (httpx.HTTPError, TimeoutError):
            failure = MediaProviderError("media_transport_uncertain", submission_unknown=True)
        if failure:
            raise failure
        return parse_result(bytes(body), request, (time.monotonic() - started) * 1000)


def parse_result(body: bytes, request: ImageRequest, duration_ms: float) -> ImageResult:
    failure = None
    try:
        data = json.loads(body)
    except (ValueError, UnicodeError):
        failure = MediaProviderError("media_invalid_response", submission_unknown=True)
    if failure:
        raise failure
    if not isinstance(data, dict):
        raise MediaProviderError("media_invalid_response", submission_unknown=True)
    if data.get("status") != "completed":
        raise MediaProviderError("media_not_completed", submission_unknown=data.get("status") != "failed")
    if data.get("model") != request.model:
        raise MediaProviderError("media_model_identity_mismatch")
    operation = data.get("id")
    if not isinstance(operation, str) or not operation or len(operation) > 2048:
        raise MediaProviderError("media_missing_operation_reference", submission_unknown=True)
    steps = data.get("steps")
    if not isinstance(steps, list):
        raise MediaProviderError("media_invalid_output")
    images = []
    for step in steps:
        if not isinstance(step, dict) or step.get("type") != "model_output":
            continue
        content = step.get("content")
        if not isinstance(content, list):
            raise MediaProviderError("media_invalid_output")
        images.extend(part for part in content if isinstance(part, dict) and part.get("type") == "image")
    if len(images) != 1:
        raise MediaProviderError("media_output_count_invalid")
    output = images[0]
    encoded = output.get("data")
    if (output.get("mime_type") != "image/png" or not isinstance(encoded, str) or
            len(encoded) > ((MAX_IMAGE_BYTES + 2) // 3) * 4):
        raise MediaProviderError("media_invalid_image")
    image = None
    try:
        image = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        pass
    if not image or len(image) > MAX_IMAGE_BYTES or not image.startswith(b"\x89PNG\r\n\x1a\n"):
        raise MediaProviderError("media_invalid_image")
    # Full decoding/MIME integrity checks are required again at ingestion.
    return ImageResult(image, "image/png", data["model"], operation,
                       normalize_usage(data.get("usage")), duration_ms)
