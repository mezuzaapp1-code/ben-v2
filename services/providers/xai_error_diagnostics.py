"""Allowlisted xAI HTTP error metadata for operator logs (no request/payload dump)."""
from __future__ import annotations

import json
import re
from typing import Any

import httpx

from services.ops.structured_log import log_warning

ERROR_MESSAGE_MAX = 200
_SECRET_RE = re.compile(
    r"sk-[a-zA-Z0-9]{10,}|xai-[a-zA-Z0-9]{10,}|api[_-]?key[=:]\s*\S+|Bearer\s+\S+",
    re.I,
)
_REQUEST_ID_HEADERS = ("x-request-id", "openai-request-id", "x-grok-request-id")


def _bound_text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, (str, int, float)):
        return None
    text = str(value).strip()
    if not text:
        return None
    text = _SECRET_RE.sub("[redacted]", text)
    return text[:ERROR_MESSAGE_MAX]


def _header(response: httpx.Response, name: str) -> str | None:
    try:
        raw = response.headers.get(name)
    except Exception:
        return None
    return _bound_text(raw)


def _response_text_for_parse(response: httpx.Response) -> str:
    try:
        content = response.content
    except Exception:
        return ""
    if not content:
        return ""
    return content[:4096].decode("utf-8", "replace")


def _pick_nested_error(payload: dict[str, Any]) -> dict[str, Any]:
    nested = payload.get("error")
    if isinstance(nested, dict):
        return nested
    return {}


def extract_safe_xai_http_error(response: httpx.Response) -> dict[str, Any]:
    """Allowlisted fields only. Never returns request/response dumps."""
    status = int(getattr(response, "status_code", 0) or 0)
    out: dict[str, Any] = {
        "http_status": status or None,
        "error_code": None,
        "error_type": None,
        "error_message": None,
        "request_id": None,
        "cf_ray": _header(response, "cf-ray"),
    }
    for name in _REQUEST_ID_HEADERS:
        rid = _header(response, name)
        if rid:
            out["request_id"] = rid
            break

    raw = _response_text_for_parse(response)
    if not raw:
        return out
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return out
    if not isinstance(payload, dict):
        return out

    nested = _pick_nested_error(payload)
    code = payload.get("code")
    if code is None:
        code = nested.get("code")
    err_type = payload.get("type")
    if err_type is None:
        err_type = nested.get("type")
    message = payload.get("error")
    if isinstance(message, dict):
        message = message.get("message") or message.get("error")
    if not isinstance(message, str) or not message.strip():
        message = payload.get("message")
    if not isinstance(message, str) or not message.strip():
        message = nested.get("message") or nested.get("error")

    out["error_code"] = _bound_text(code)
    out["error_type"] = _bound_text(err_type)
    out["error_message"] = _bound_text(message)
    return {k: v for k, v in out.items() if k in {
        "http_status", "error_code", "error_type", "error_message", "request_id", "cf_ray",
    }}


def log_safe_xai_http_error(response: httpx.Response) -> dict[str, Any]:
    fields = extract_safe_xai_http_error(response)
    log_warning(
        "xAI provider HTTP error",
        subsystem="xai_provider",
        provider="xai",
        event="provider_http_error",
        http_status=fields.get("http_status"),
        error_code=fields.get("error_code"),
        error_type=fields.get("error_type"),
        error_message=fields.get("error_message"),
        request_id=fields.get("request_id"),
        cf_ray=fields.get("cf_ray"),
    )
    return fields


async def ensure_xai_response_content(response: httpx.Response) -> None:
    """Load streamed error bodies so allowlisted JSON can be parsed."""
    try:
        if response.content:
            return
    except Exception:
        pass
    try:
        await response.aread()
    except Exception:
        return


def raise_for_xai_status(response: httpx.Response) -> None:
    status = int(getattr(response, "status_code", 0) or 0)
    if status < 400:
        response.raise_for_status()
        return
    log_safe_xai_http_error(response)
    response.raise_for_status()
