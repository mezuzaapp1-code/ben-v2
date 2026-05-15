"""Per-request tracing ID (contextvar)."""
from __future__ import annotations

import uuid
from contextvars import ContextVar

_request_id: ContextVar[str | None] = ContextVar("ben_request_id", default=None)


def new_request_id() -> str:
    rid = str(uuid.uuid4())
    _request_id.set(rid)
    return rid


def set_request_id(request_id: str) -> None:
    _request_id.set(request_id)


def get_request_id() -> str | None:
    return _request_id.get()


def attach_request_id(payload: dict) -> dict:
    rid = get_request_id()
    if rid:
        return {**payload, "request_id": rid}
    return payload
