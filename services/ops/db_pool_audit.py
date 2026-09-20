"""Opt-in Gate A7 pool/connection identity observers.

Enabled only when BEN_LATENCY_PATH_AUDIT is on. Listeners never change pool
behavior. Identifiers are opaque hashes — never log URLs, credentials, or
raw DBAPI/connection objects.

Limitations (SQLAlchemy 2.0.36 + asyncpg 0.30.0):
- PoolEvents.checkout fires AFTER pool wait, pre_ping, and connect.
  It does not measure wait duration.
- PoolEvents.connect fires AFTER physical connection creation completed.
  It is not a start boundary for TLS/handshake duration.
- DialectEvents has no do_ping. Successful pool_pre_ping duration is
  UNOBSERVABLE. Pre-ping is skipped for fresh (just-created) connections.
"""
from __future__ import annotations

import hashlib
import os
import threading
import time
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from typing import Any, AsyncIterator

from sqlalchemy import event

from services.ops.latency_path_audit import audit_enabled, mark, note_audit_fields

_installed_ids: set[int] = set()
_install_lock = threading.Lock()
_record_seq = 0
_record_lock = threading.Lock()
_process_token = hashlib.sha256(f"pid:{os.getpid()}".encode()).hexdigest()[:12]

_slot: ContextVar[str | None] = ContextVar("ben_a7_pool_slot", default=None)
_captures: ContextVar[dict[str, dict[str, Any]] | None] = ContextVar(
    "ben_a7_pool_captures", default=None
)

_PHYSICAL_STATES = frozenset(
    {"REUSED", "NEW", "RECONNECTED", "INVALIDATED", "UNKNOWN"}
)


def _opaque(kind: str, value: Any) -> str:
    raw = f"{_process_token}:{kind}:{value}".encode()
    return hashlib.sha256(raw).hexdigest()[:12]


def _next_record_seq() -> int:
    global _record_seq
    with _record_lock:
        _record_seq += 1
        return _record_seq


def _stamp_record(connection_record: Any, *, new_physical: bool = False) -> None:
    if getattr(connection_record, "_ben_record_seq", None) is None:
        connection_record._ben_record_seq = _next_record_seq()
    if new_physical:
        connection_record._ben_phys_gen = int(getattr(connection_record, "_ben_phys_gen", 0)) + 1


def _ids_for(connection_record: Any, engine: Any | None = None) -> dict[str, Any]:
    rec_seq = getattr(connection_record, "_ben_record_seq", None)
    gen = getattr(connection_record, "_ben_phys_gen", None)
    pool = getattr(connection_record, "_ConnectionRecord__pool", None)
    if pool is None:
        pool = getattr(connection_record, "pool", None)
    out: dict[str, Any] = {
        "process_id": _process_token,
        "record_id": _opaque("rec", rec_seq) if rec_seq is not None else None,
        "phys_gen": int(gen) if gen is not None else None,
    }
    if engine is not None:
        out["engine_id"] = _opaque("engine", id(engine))
        pool_obj = getattr(engine, "pool", None)
        out["pool_id"] = _opaque("pool", id(pool_obj)) if pool_obj is not None else None
        out["pool_class"] = type(pool_obj).__name__ if pool_obj is not None else None
    elif pool is not None:
        out["pool_id"] = _opaque("pool", id(pool))
        out["pool_class"] = type(pool).__name__
    return out


def _capture() -> dict[str, Any] | None:
    slot = _slot.get()
    if not slot:
        return None
    bag = _captures.get()
    if bag is None:
        bag = {}
        _captures.set(bag)
    rec = bag.get(slot)
    if rec is None:
        rec = {
            "slot": slot,
            "events": [],
            "connects": 0,
            "first_connects": 0,
            "checkouts": 0,
            "checkins": 0,
            "closes": 0,
            "invalidates": 0,
            "resets": 0,
            "physical": "UNKNOWN",
        }
        bag[slot] = rec
    return rec


def _append_event(kind: str, connection_record: Any, engine: Any | None = None) -> None:
    rec = _capture()
    if rec is None:
        return
    ids = _ids_for(connection_record, engine)
    rec["events"].append(
        {
            "kind": kind,
            "t": time.perf_counter(),
            "record_id": ids.get("record_id"),
            "phys_gen": ids.get("phys_gen"),
        }
    )
    rec[f"{kind}s"] = int(rec.get(f"{kind}s") or 0) + 1
    for key in ("process_id", "engine_id", "pool_id", "pool_class", "record_id", "phys_gen"):
        if ids.get(key) is not None:
            rec[key] = ids[key]


def classify_physical(capture: dict[str, Any]) -> str:
    """Map observed pool events to a physical-connection state.

    A checkout callback alone is not pool-wait. connect is a completion
    mark, not handshake duration.
    """
    kinds = [e.get("kind") for e in capture.get("events") or []]
    has_connect = "connect" in kinds or "first_connect" in kinds
    has_invalidate = "invalidate" in kinds
    has_checkout = "checkout" in kinds
    gens = [e.get("phys_gen") for e in (capture.get("events") or []) if e.get("phys_gen")]
    max_gen = max(gens) if gens else int(capture.get("phys_gen") or 0)

    if has_invalidate and has_connect:
        return "RECONNECTED"
    if has_invalidate and not has_connect:
        return "INVALIDATED"
    if has_connect and max_gen > 1:
        return "RECONNECTED"
    if has_connect:
        return "NEW"
    if has_checkout:
        return "REUSED"
    return "UNKNOWN"


def _finalize(capture: dict[str, Any]) -> dict[str, Any]:
    capture["physical"] = classify_physical(capture)
    if capture["physical"] not in _PHYSICAL_STATES:
        capture["physical"] = "UNKNOWN"
    capture["event_kinds"] = ",".join(
        str(e.get("kind")) for e in (capture.get("events") or [])
    )
    # Drop raw timestamps from the published view; keep counts + identity.
    published = {
        "physical": capture["physical"],
        "process_id": capture.get("process_id"),
        "engine_id": capture.get("engine_id"),
        "pool_id": capture.get("pool_id"),
        "pool_class": capture.get("pool_class"),
        "record_id": capture.get("record_id"),
        "phys_gen": capture.get("phys_gen"),
        "event_kinds": capture.get("event_kinds") or "",
        "connects": int(capture.get("connects") or 0),
        "checkouts": int(capture.get("checkouts") or 0),
        "checkins": int(capture.get("checkins") or 0),
        "closes": int(capture.get("closes") or 0),
        "invalidates": int(capture.get("invalidates") or 0),
        "checkout_is_not_wait": True,
        "connect_is_completion_not_duration": True,
    }
    return published


def _safe_listen(fn):
    def wrapped(*args: Any, **kwargs: Any) -> None:
        if not audit_enabled():
            return
        try:
            fn(*args, **kwargs)
        except Exception:
            return

    wrapped.__name__ = getattr(fn, "__name__", "ben_pool_listener")
    return wrapped


def ensure_installed(engine: Any | None = None) -> None:
    """Attach PoolEvents to the given engine. Idempotent. No-op if audit off."""
    if not audit_enabled():
        return
    if engine is None:
        from database.connection import get_engine

        engine = get_engine()
    sync = getattr(engine, "sync_engine", engine)
    eid = id(sync)
    with _install_lock:
        if eid in _installed_ids:
            return

        @_safe_listen
        def _on_first_connect(dbapi_conn: Any, connection_record: Any) -> None:
            _stamp_record(connection_record, new_physical=False)
            _append_event("first_connect", connection_record, sync)

        @_safe_listen
        def _on_connect(dbapi_conn: Any, connection_record: Any) -> None:
            _stamp_record(connection_record, new_physical=True)
            _append_event("connect", connection_record, sync)

        @_safe_listen
        def _on_checkout(dbapi_conn: Any, connection_record: Any, connection_proxy: Any) -> None:
            _stamp_record(connection_record, new_physical=False)
            _append_event("checkout", connection_record, sync)

        @_safe_listen
        def _on_checkin(dbapi_conn: Any, connection_record: Any) -> None:
            _append_event("checkin", connection_record, sync)

        @_safe_listen
        def _on_close(dbapi_conn: Any, connection_record: Any) -> None:
            _append_event("close", connection_record, sync)

        @_safe_listen
        def _on_invalidate(dbapi_conn: Any, connection_record: Any, exception: Any) -> None:
            _stamp_record(connection_record, new_physical=False)
            _append_event("invalidate", connection_record, sync)

        @_safe_listen
        def _on_reset(*args: Any, **kwargs: Any) -> None:
            connection_record = args[1] if len(args) > 1 else None
            if connection_record is None:
                return
            _append_event("reset", connection_record, sync)

        event.listen(sync, "first_connect", _on_first_connect)
        event.listen(sync, "connect", _on_connect)
        event.listen(sync, "checkout", _on_checkout)
        event.listen(sync, "checkin", _on_checkin)
        event.listen(sync, "close", _on_close)
        event.listen(sync, "invalidate", _on_invalidate)
        event.listen(sync, "reset", _on_reset)
        _installed_ids.add(eid)


@contextmanager
def slot_scope(slot: str):
    """Attribute subsequent pool events to thread|context for this task."""
    token = _slot.set(slot)
    bag = _captures.get()
    if bag is None:
        bag = {}
        _captures.set(bag)
    bag[slot] = {
        "slot": slot,
        "events": [],
        "connects": 0,
        "first_connects": 0,
        "checkouts": 0,
        "checkins": 0,
        "closes": 0,
        "invalidates": 0,
        "resets": 0,
        "physical": "UNKNOWN",
    }
    try:
        yield bag[slot]
    finally:
        _slot.reset(token)


def publish_slot(slot: str) -> dict[str, Any]:
    bag = _captures.get() or {}
    published = _finalize(bag.get(slot) or {"events": []})
    note_audit_fields(f"{slot}_pool", published)
    return published


@asynccontextmanager
async def audit_db_slot(slot: str) -> AsyncIterator[None]:
    """Cover the whole AsyncSession with-block so checkin is observed."""
    if not audit_enabled():
        yield
        return
    ensure_installed()
    with slot_scope(slot):
        try:
            yield
        finally:
            publish_slot(slot)


async def acquire_pooled_connection(session: Any, slot: str) -> None:
    """Make lazy pool checkout explicit, then leave set_config as first SQL.

    SQLAlchemy 2.0.36 Session.connection() begins Session transactional
    state only. DefaultDialect.do_begin is a no-op for Postgres, so no
    DBAPI BEGIN is sent until the existing _set_org / set_config execute.
    RLS (set_config is_local=true) is unchanged.
    """
    if not audit_enabled():
        return
    ensure_installed()
    mark(f"{slot}_acquire_start")
    await session.connection()
    mark(f"{slot}_acquire_end")
