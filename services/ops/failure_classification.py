"""Provider failure categories for structured logging (not exposed in council shape)."""
from __future__ import annotations

import asyncio

import httpx

FAILURE_TIMEOUT = "timeout"
FAILURE_AUTH_ERROR = "auth_error"
FAILURE_CONFIG_ERROR = "config_error"
FAILURE_PROVIDER_UNAVAILABLE = "provider_unavailable"
FAILURE_UNKNOWN_ERROR = "unknown_error"


def classify_failure(exc: BaseException) -> str:
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError, httpx.TimeoutException)):
        return FAILURE_TIMEOUT

    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code in (401, 403):
            return FAILURE_AUTH_ERROR
        if code == 404:
            return FAILURE_CONFIG_ERROR
        if code == 408:
            return FAILURE_TIMEOUT
        if code == 429:
            return FAILURE_PROVIDER_UNAVAILABLE
        if code >= 500:
            return FAILURE_PROVIDER_UNAVAILABLE
        return FAILURE_UNKNOWN_ERROR

    if isinstance(exc, httpx.RequestError):
        return FAILURE_PROVIDER_UNAVAILABLE

    msg = str(exc).lower()
    if "missing" in msg and "api_key" in msg:
        return FAILURE_CONFIG_ERROR
    if "timeout" in msg or "timed out" in msg:
        return FAILURE_TIMEOUT
    if "401" in msg or "403" in msg or "unauthorized" in msg:
        return FAILURE_AUTH_ERROR
    if "404" in msg or "not_found" in msg:
        return FAILURE_CONFIG_ERROR

    return FAILURE_UNKNOWN_ERROR
