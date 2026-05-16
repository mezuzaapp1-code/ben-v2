"""In-process request idempotency and persistence dedupe (v1; not distributed)."""
from __future__ import annotations

import asyncio
import copy
import os
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Literal

from fastapi import HTTPException

from services.ops.runtime_diagnostics import anonymize_tenant_id, emit_runtime_event
from services.ops.runtime_events import (
    IDEMPOTENCY_REJECTED,
    PERSISTENCE_RECOVERY,
    REPLAY_DETECTED,
    STALE_RUNTIME_STATE_RECOVERED,
)
from services.ops.runtime_events import COUNCIL_PENDING
from services.ops.runtime_state import COUNCIL_RUNNING

IdempotencyState = Literal["pending", "completed", "failed"]

CLIENT_REQUEST_ID_HEADER = "X-BEN-Client-Request-Id"
IDEMPOTENCY_REJECTED_CODE = "idempotency_rejected"

_REPLAY_STRIP_KEYS = frozenset({"question", "message"})


def _sanitize_replay_snapshot(response: dict[str, Any]) -> dict[str, Any]:
    """Drop prompt fields from stored replay payloads (no full prompt retention)."""
    out = copy.deepcopy(response)
    for key in _REPLAY_STRIP_KEYS:
        out.pop(key, None)
    return out


@dataclass
class IdempotencyEntry:
    route: str
    tenant_hash: str
    state: IdempotencyState
    created_at: float
    updated_at: float
    response: dict[str, Any] | None = None
    persist_markers: set[str] = field(default_factory=set)


@dataclass
class IdempotencyBeginResult:
    active: bool
    store_key: str | None = None
    replay_response: dict[str, Any] | None = None
    runtime_state: str = COUNCIL_RUNNING


_idempotency_key_ctx: ContextVar[str | None] = ContextVar("ben_idempotency_key", default=None)
_client_request_id_ctx: ContextVar[str | None] = ContextVar("ben_client_request_id", default=None)


def get_idempotency_store_key() -> str | None:
    return _idempotency_key_ctx.get()


def get_client_request_id() -> str | None:
    return _client_request_id_ctx.get()


def resolve_client_request_id(*, body_value: str | None, header_value: str | None) -> str | None:
    raw = (body_value or "").strip() or (header_value or "").strip()
    if not raw:
        return None
    if len(raw) > 128:
        return None
    return raw


def _ttl_completed_s() -> float:
    raw = os.getenv("BEN_IDEMPOTENCY_COMPLETED_TTL_S", "").strip()
    try:
        return float(raw) if raw else 300.0
    except ValueError:
        return 300.0


def _ttl_pending_s() -> float:
    raw = os.getenv("BEN_IDEMPOTENCY_PENDING_TTL_S", "").strip()
    try:
        return float(raw) if raw else 120.0
    except ValueError:
        return 120.0


@dataclass
class IdempotencyRegistry:
    _entries: dict[str, IdempotencyEntry] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    def _make_key(self, route: str, tenant_id: str, client_request_id: str) -> str:
        return f"{route}:{anonymize_tenant_id(tenant_id)}:{client_request_id}"

    async def _purge_expired(self, now: float) -> None:
        completed_ttl = _ttl_completed_s()
        pending_ttl = _ttl_pending_s()
        stale: list[str] = []
        for key, ent in self._entries.items():
            age = now - ent.updated_at
            if ent.state == "completed" and age > completed_ttl:
                stale.append(key)
            elif ent.state == "pending" and age > pending_ttl:
                stale.append(key)
                emit_runtime_event(
                    STALE_RUNTIME_STATE_RECOVERED,
                    level="warning",
                    route=ent.route,
                    tenant_hash=ent.tenant_hash,
                    prior_state=ent.state,
                    outcome="recovered",
                )
            elif ent.state == "failed" and age > pending_ttl:
                stale.append(key)
        for key in stale:
            self._entries.pop(key, None)

    async def begin(
        self,
        *,
        route: str,
        tenant_id: str,
        client_request_id: str | None,
    ) -> IdempotencyBeginResult:
        if not client_request_id:
            _idempotency_key_ctx.set(None)
            _client_request_id_ctx.set(None)
            return IdempotencyBeginResult(active=True)

        key = self._make_key(route, tenant_id, client_request_id)
        tenant_hash = anonymize_tenant_id(tenant_id)
        now = time.monotonic()
        async with self._lock:
            await self._purge_expired(now)
            ent = self._entries.get(key)
            if ent and ent.state == "completed" and ent.response is not None:
                emit_runtime_event(
                    REPLAY_DETECTED,
                    route=route,
                    tenant_hash=tenant_hash,
                    outcome="replay",
                )
                _idempotency_key_ctx.set(key)
                _client_request_id_ctx.set(client_request_id)
                rs = str(ent.response.get("runtime_state") or COUNCIL_RUNNING)
                return IdempotencyBeginResult(
                    active=False,
                    store_key=key,
                    replay_response=copy.deepcopy(ent.response),
                    runtime_state=rs,
                )
            if ent and ent.state == "pending":
                emit_runtime_event(
                    IDEMPOTENCY_REJECTED,
                    level="warning",
                    route=route,
                    tenant_hash=tenant_hash,
                    outcome="rejected",
                )
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": IDEMPOTENCY_REJECTED_CODE,
                        "message": "This request is already in progress. Retry with the same client request id after it completes.",
                        "hint": "Wait for the in-flight request or use a new client request id for a new council.",
                        "recoverable": True,
                    },
                )
            self._entries[key] = IdempotencyEntry(
                route=route,
                tenant_hash=tenant_hash,
                state="pending",
                created_at=now,
                updated_at=now,
            )

        _idempotency_key_ctx.set(key)
        _client_request_id_ctx.set(client_request_id)
        if route == "/council":
            emit_runtime_event(
                COUNCIL_PENDING,
                route=route,
                tenant_hash=tenant_hash,
                outcome="pending",
            )
        return IdempotencyBeginResult(active=True, store_key=key, runtime_state=COUNCIL_PENDING)

    async def complete(self, store_key: str | None, response: dict[str, Any]) -> None:
        if not store_key:
            return
        now = time.monotonic()
        async with self._lock:
            ent = self._entries.get(store_key)
            if ent is None:
                return
            ent.state = "completed"
            ent.updated_at = now
            ent.response = _sanitize_replay_snapshot(response)

    async def fail(self, store_key: str | None) -> None:
        """Release pending slot so a retry with the same client request id may proceed."""
        if not store_key:
            return
        async with self._lock:
            ent = self._entries.get(store_key)
            if ent and ent.state == "pending":
                self._entries.pop(store_key, None)
        _idempotency_key_ctx.set(None)

    async def should_persist(self, store_key: str | None, marker: str) -> bool:
        if not store_key:
            return True
        async with self._lock:
            ent = self._entries.get(store_key)
            if ent is None:
                return True
            return marker not in ent.persist_markers

    async def mark_persisted(self, store_key: str | None, marker: str) -> None:
        if not store_key:
            return
        async with self._lock:
            ent = self._entries.get(store_key)
            if ent is None:
                return
            if marker not in ent.persist_markers:
                ent.persist_markers.add(marker)
                emit_runtime_event(
                    PERSISTENCE_RECOVERY,
                    route=ent.route,
                    tenant_hash=ent.tenant_hash,
                    operation=marker,
                    outcome="persisted",
                )

    async def transcript_persisted(self, store_key: str | None) -> bool:
        if not store_key:
            return False
        async with self._lock:
            ent = self._entries.get(store_key)
            return bool(ent and "council_transcript" in ent.persist_markers)


_registry: IdempotencyRegistry | None = None


def get_idempotency_registry() -> IdempotencyRegistry:
    global _registry
    if _registry is None:
        _registry = IdempotencyRegistry()
    return _registry


def reset_idempotency_registry_for_tests() -> IdempotencyRegistry:
    global _registry
    _registry = IdempotencyRegistry()
    _idempotency_key_ctx.set(None)
    _client_request_id_ctx.set(None)
    return _registry
