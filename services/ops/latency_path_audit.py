"""TEMPORARY Gate A latency diagnosis. Not a product feature.

Enabled only when BEN_LATENCY_PATH_AUDIT=1. Marks use monotonic clocks and
never store secrets, Authorization headers, API keys, or prompt text.

Default-off: mark() is a no-op. Does not change outbound payloads or yields.
"""
from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Any, AsyncIterator

from services.ops.request_context import get_request_id
from services.ops.structured_log import log_info

_ENABLED: bool | None = None
_marks: ContextVar[dict[str, Any] | None] = ContextVar("ben_latency_path_audit", default=None)

MARK_ORDER = (
    "t0_accepted",
    "t1_auth",
    "admission_skipped",
    "TR0",
    "TR1",
    "thread_acquire_start",
    "thread_acquire_end",
    "TR2",
    "TR3",
    "TR4",
    "TR5",
    "TR6",
    "db_thread_ready",
    "CX0",
    "CX1",
    "CX2",
    "context_acquire_start",
    "context_acquire_end",
    "CX3",
    "CX4",
    "CX5",
    "CX6",
    "CX7",
    "files_start",
    "files_end",
    "files_skipped",
    "t2_context",
    "t3_routing",
    "httpx_client_ready",
    "t4_http_start",
    "t5_http_headers",
    "t6_first_sse",
    "p3_reasoning",
    "p4_answer",
    "t7_first_content",
    "t8_ben_forward",
    "t9_provider_end",
    "t10_ben_complete",
)


def audit_enabled() -> bool:
    global _ENABLED
    if _ENABLED is None:
        _ENABLED = os.getenv("BEN_LATENCY_PATH_AUDIT", "").strip().lower() in {"1", "true", "yes", "on"}
    return _ENABLED


def configure_audit_for_process(*, enabled: bool) -> None:
    """Test/script helper — not used in production request handling."""
    global _ENABLED
    _ENABLED = bool(enabled)


def reset_latency_audit(*, provider: str = "", model: str = "", path: str = "") -> None:
    if not audit_enabled():
        return
    _marks.set(
        {
            "marks": {},
            "extra": {},
            "provider": (provider or "").strip(),
            "model": (model or "").strip(),
            "path": (path or "").strip(),
            "attempt_id": 0,
        }
    )


def note_audit_fields(group: str, data: dict[str, Any]) -> None:
    """Attach opaque identity/counts under extra[group]. No secrets."""
    if not audit_enabled():
        return
    state = _marks.get()
    if state is None:
        reset_latency_audit()
        state = _marks.get()
        if state is None:
            return
    safe = {
        k: v
        for k, v in data.items()
        if k not in {"message", "api_key", "authorization", "token", "password", "database_url"}
        and not str(k).lower().endswith(("_url", "password", "secret"))
    }
    extra: dict[str, Any] = state["extra"]
    extra[group] = safe


async def audit_acquire_connection(session: Any, slot: str) -> None:
    """Audit-only: explicit pool checkout immediately before _set_org.

    No-op when BEN_LATENCY_PATH_AUDIT is off. Does not emit SQL.
    """
    if not audit_enabled():
        return
    from services.ops.db_pool_audit import acquire_pooled_connection

    await acquire_pooled_connection(session, slot)


@asynccontextmanager
async def audit_db_slot(slot: str) -> AsyncIterator[None]:
    """Audit-only wrapper around a DB session so pool checkin is observed."""
    if not audit_enabled():
        yield
        return
    from services.ops.db_pool_audit import audit_db_slot as _slot

    async with _slot(slot):
        yield


def mark(name: str, **extra: Any) -> None:
    if not audit_enabled():
        return
    state = _marks.get()
    if state is None:
        reset_latency_audit()
        state = _marks.get()
        if state is None:
            return
    marks: dict[str, float] = state["marks"]
    if name not in marks:
        marks[name] = time.perf_counter()
        if extra:
            safe = {k: v for k, v in extra.items() if k not in {"message", "api_key", "authorization", "token"}}
            if safe:
                state["extra"][name] = safe


def note_attempt(*, attempt_id: int, provider: str = "", model: str = "") -> None:
    if not audit_enabled():
        return
    state = _marks.get()
    if state is None:
        reset_latency_audit(provider=provider, model=model)
        state = _marks.get()
        if state is None:
            return
    state["attempt_id"] = int(attempt_id)
    if provider:
        state["provider"] = provider
    if model:
        state["model"] = model


def _ms(state: dict[str, Any], later: str, earlier: str) -> float | None:
    marks: dict[str, float] = state["marks"]
    if later not in marks or earlier not in marks:
        return None
    return round((marks[later] - marks[earlier]) * 1000.0, 1)


def audit_snapshot() -> dict[str, Any]:
    if not audit_enabled():
        return {}
    state = _marks.get()
    if not state:
        return {}
    marks: dict[str, float] = state["marks"]
    origin = marks.get("t0_accepted") or marks.get("t2_context")
    from_t0 = {
        name: round((marks[name] - origin) * 1000.0, 1)
        for name in MARK_ORDER
        if name in marks and origin is not None
    }
    answer_mark = "p4_answer" if "p4_answer" in marks else "t7_first_content"
    db_thread_ms = _ms(state, "db_thread_ready", "t1_auth") or _ms(
        state, "db_thread_ready", "t0_accepted"
    )
    thread_resolve_ms = _ms(state, "TR6", "TR0")
    pre_thread_overhead_ms = None
    if db_thread_ms is not None and thread_resolve_ms is not None:
        pre_thread_overhead_ms = round(db_thread_ms - thread_resolve_ms, 1)
    extra = state.get("extra") or {}
    thread_pool = extra.get("thread_pool") or {}
    context_pool = extra.get("context_pool") or {}
    same_engine = "UNKNOWN"
    if thread_pool.get("engine_id") and context_pool.get("engine_id"):
        same_engine = "YES" if thread_pool.get("engine_id") == context_pool.get("engine_id") else "NO"
    elif thread_pool.get("engine_id") or context_pool.get("engine_id"):
        same_engine = "YES"
    return {
        "path": state.get("path") or "",
        "provider": state.get("provider") or "",
        "model": state.get("model") or "",
        "request_id": get_request_id(),
        "attempt_id": state.get("attempt_id") or 0,
        "from_t0_ms": from_t0,
        "pre_dispatch_ms": _ms(state, "t4_http_start", "t0_accepted")
        if "t0_accepted" in marks
        else _ms(state, "t4_http_start", "t2_context"),
        "http_headers_ms": _ms(state, "t5_http_headers", "t4_http_start"),
        "first_sse_after_headers_ms": _ms(state, "t6_first_sse", "t5_http_headers"),
        "first_content_after_sse_ms": _ms(state, "t7_first_content", "t6_first_sse"),
        "provider_first_content_ms": _ms(state, "t7_first_content", "t4_http_start"),
        "provider_first_answer_ms": _ms(state, answer_mark, "t4_http_start"),
        "ben_forward_ms": _ms(state, "t8_ben_forward", "t7_first_content"),
        "provider_complete_after_content_ms": _ms(state, "t9_provider_end", "t7_first_content"),
        "ben_complete_after_provider_ms": _ms(state, "t10_ben_complete", "t9_provider_end"),
        "user_visible_ttft_ms": _ms(state, "t8_ben_forward", "t0_accepted")
        if "t0_accepted" in marks
        else _ms(state, "t8_ben_forward", "t2_context"),
        "total_complete_ms": _ms(state, "t10_ben_complete", "t0_accepted")
        if "t0_accepted" in marks
        else _ms(state, "t10_ben_complete", "t2_context"),
        "gate_a": {
            "B0_entry": "t0_accepted",
            "AUTH_ms": _ms(state, "t1_auth", "t0_accepted"),
            "DB_thread_ms": db_thread_ms,
            "thread_resolve_ms": thread_resolve_ms,
            "session_checkout_ms": _ms(state, "TR1", "TR0"),
            "set_config_ms": _ms(state, "TR2", "TR1"),
            "thread_connection_acquire_ms": _ms(state, "thread_acquire_end", "thread_acquire_start"),
            "thread_set_config_sql_ms": _ms(state, "TR2", "thread_acquire_end"),
            "context_connection_acquire_ms": _ms(
                state, "context_acquire_end", "context_acquire_start"
            ),
            "context_set_config_sql_ms": _ms(state, "CX3", "context_acquire_end"),
            "thread_physical": thread_pool.get("physical") or "UNKNOWN",
            "context_physical": context_pool.get("physical") or "UNKNOWN",
            "thread_phys_gen": thread_pool.get("phys_gen"),
            "context_phys_gen": context_pool.get("phys_gen"),
            "thread_record_id": thread_pool.get("record_id"),
            "context_record_id": context_pool.get("record_id"),
            "db_process_id": thread_pool.get("process_id") or context_pool.get("process_id"),
            "db_engine_id": thread_pool.get("engine_id") or context_pool.get("engine_id"),
            "db_pool_id": thread_pool.get("pool_id") or context_pool.get("pool_id"),
            "db_pool_class": thread_pool.get("pool_class") or context_pool.get("pool_class"),
            "thread_pool_events": thread_pool.get("event_kinds") or "",
            "context_pool_events": context_pool.get("event_kinds") or "",
            "same_engine_pool": same_engine,
            "insert_flush_ms": _ms(state, "TR3", "TR2"),
            "commit_ms": _ms(state, "TR4", "TR3"),
            "sqlite_metadata_ms": _ms(state, "TR5", "TR4"),
            "return_tail_ms": _ms(state, "TR6", "TR5"),
            "pre_thread_overhead_ms": pre_thread_overhead_ms,
            "pool_pre_ping": "UNOBSERVABLE",
            "FILE_ms": _ms(state, "files_end", "files_start"),
            "CONTEXT_ms": _ms(state, "t2_context", "db_thread_ready")
            or _ms(state, "t2_context", "t0_accepted"),
            "context_preamble_ms": _ms(state, "CX0", "db_thread_ready"),
            "sqlite_history_ms": _ms(state, "CX1", "CX0"),
            "context_pg_session_ms": _ms(state, "CX2", "CX1"),
            "context_set_config_ms": _ms(state, "CX3", "CX2"),
            "context_thread_lookup_ms": _ms(state, "CX4", "CX3"),
            "context_messages_query_ms": _ms(state, "CX5", "CX4"),
            "context_history_compose_ms": _ms(state, "CX6", "CX5")
            or _ms(state, "CX6", "CX1"),
            "knowledge_inject_ms": _ms(state, "CX7", "CX6"),
            "context_tail_ms": _ms(state, "t2_context", "CX7"),
            "ADMISSION": "skipped_on_chat_stream" if "admission_skipped" in marks else "unobserved",
            "B0_to_B1_ms": _ms(state, "t4_http_start", "t0_accepted"),
            "B1_to_B2_ms": _ms(state, "t8_ben_forward", "t4_http_start"),
            "B0_to_B3_ms": _ms(state, "t10_ben_complete", "t0_accepted"),
            "P0_to_Ph_ms": _ms(state, "t5_http_headers", "t4_http_start"),
            "Ph_to_P2_ms": _ms(state, "t6_first_sse", "t5_http_headers"),
            "P0_to_P3_ms": _ms(state, "p3_reasoning", "t4_http_start"),
            "P0_to_P4_ms": _ms(state, answer_mark, "t4_http_start"),
            "P0_to_P5_ms": _ms(state, "t9_provider_end", "t4_http_start"),
            "Pb_first_body": "UNOBSERVABLE",
            "httpx_client_ms": _ms(state, "httpx_client_ready", "t3_routing"),
        },
        "extra": extra,
    }


def log_latency_audit() -> dict[str, Any]:
    snap = audit_snapshot()
    if not snap:
        return {}
    ga = snap.get("gate_a") or {}
    log_info(
        "latency path audit",
        subsystem="latency_path_audit",
        operation="latency_path_audit",
        outcome="ok",
        provider=snap.get("provider") or None,
        model=snap.get("model") or None,
        path=snap.get("path") or None,
        request_id=snap.get("request_id"),
        attempt_id=snap.get("attempt_id"),
        pre_dispatch_ms=snap.get("pre_dispatch_ms"),
        provider_first_content_ms=snap.get("provider_first_content_ms"),
        provider_first_answer_ms=snap.get("provider_first_answer_ms"),
        ben_forward_ms=snap.get("ben_forward_ms"),
        user_visible_ttft_ms=snap.get("user_visible_ttft_ms"),
        total_complete_ms=snap.get("total_complete_ms"),
        AUTH_ms=ga.get("AUTH_ms"),
        DB_thread_ms=ga.get("DB_thread_ms"),
        thread_resolve_ms=ga.get("thread_resolve_ms"),
        session_checkout_ms=ga.get("session_checkout_ms"),
        set_config_ms=ga.get("set_config_ms"),
        thread_connection_acquire_ms=ga.get("thread_connection_acquire_ms"),
        thread_set_config_sql_ms=ga.get("thread_set_config_sql_ms"),
        context_connection_acquire_ms=ga.get("context_connection_acquire_ms"),
        context_set_config_sql_ms=ga.get("context_set_config_sql_ms"),
        thread_physical=ga.get("thread_physical"),
        context_physical=ga.get("context_physical"),
        thread_phys_gen=ga.get("thread_phys_gen"),
        context_phys_gen=ga.get("context_phys_gen"),
        thread_record_id=ga.get("thread_record_id"),
        context_record_id=ga.get("context_record_id"),
        db_process_id=ga.get("db_process_id"),
        db_engine_id=ga.get("db_engine_id"),
        db_pool_id=ga.get("db_pool_id"),
        db_pool_class=ga.get("db_pool_class"),
        thread_pool_events=ga.get("thread_pool_events"),
        context_pool_events=ga.get("context_pool_events"),
        same_engine_pool=ga.get("same_engine_pool"),
        insert_flush_ms=ga.get("insert_flush_ms"),
        commit_ms=ga.get("commit_ms"),
        sqlite_metadata_ms=ga.get("sqlite_metadata_ms"),
        return_tail_ms=ga.get("return_tail_ms"),
        pre_thread_overhead_ms=ga.get("pre_thread_overhead_ms"),
        FILE_ms=ga.get("FILE_ms"),
        CONTEXT_ms=ga.get("CONTEXT_ms"),
        context_preamble_ms=ga.get("context_preamble_ms"),
        sqlite_history_ms=ga.get("sqlite_history_ms"),
        context_pg_session_ms=ga.get("context_pg_session_ms"),
        context_set_config_ms=ga.get("context_set_config_ms"),
        context_thread_lookup_ms=ga.get("context_thread_lookup_ms"),
        context_messages_query_ms=ga.get("context_messages_query_ms"),
        context_history_compose_ms=ga.get("context_history_compose_ms"),
        knowledge_inject_ms=ga.get("knowledge_inject_ms"),
        context_tail_ms=ga.get("context_tail_ms"),
        ADMISSION=ga.get("ADMISSION"),
        B0_to_B1_ms=ga.get("B0_to_B1_ms"),
        B1_to_B2_ms=ga.get("B1_to_B2_ms"),
        B0_to_B3_ms=ga.get("B0_to_B3_ms"),
        P0_to_Ph_ms=ga.get("P0_to_Ph_ms"),
        Ph_to_P2_ms=ga.get("Ph_to_P2_ms"),
        P0_to_P3_ms=ga.get("P0_to_P3_ms"),
        P0_to_P4_ms=ga.get("P0_to_P4_ms"),
        P0_to_P5_ms=ga.get("P0_to_P5_ms"),
    )
    return snap
