"""T04 Chat: model gateway + persist thread/messages with RLS org context."""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy import text

from database.connection import get_db_session
from database.models import Message
from services.ben_log_service import capture_chat_exchange
from services.chat_language import apply_language_context
from services.message_format import encode_chat_assistant, gateway_to_provider_id
from services.copilot_orchestrator import run_copilot_preamble
from services.inference.gateway_meter import get_last_accounted_call
from services.model_gateway import route_request, route_request_stream, validate_chat_model_override
from services.ops.failure_classification import classify_failure
from services.ops.runtime_diagnostics import attach_workspace_to_request_diagnostics
from services.ops.structured_log import log_warning
from services.chat_intent import apply_chat_intent_to_request, is_cross_engine_turn
from services.rolling_context import (
    CROSS_ENGINE_HANDOFF_SYSTEM,
    DEFAULT_OPINION_REQUEST,
    RAW_STREAM_SYSTEM,
    build_rolling_stream_prompt,
)
from services.knowledge_injection import inject_knowledge_few_shot
from services.project_agent_service import stream_project_agent_response
from services.workspace_resolver import resolve_workspace_context_for_org
from services.thread_service import (
    build_chat_message_with_thread_context,
    build_cross_engine_thread_prompt,
    format_full_thread_history_for_handoff,
    is_project_setup_thread,
    persist_chat_exchange_sqlite,
    resolve_thread_id,
    _load_chat_history_messages,
)


def _stream_ndjson(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False) + "\n"


def _estimate_output_tokens(text: str) -> int:
    """Lightweight output token estimate (chars/4) — computed after stream ends."""
    length = len(text or "")
    return max(0, length // 4)


def _finalize_stream_perf(
    *,
    stream_started: float,
    first_token_at: float | None,
    last_token_at: float | None,
    output_text: str,
) -> dict[str, float]:
    """Deferred TTFT/TPS metrics — never called inside the token forward loop."""
    if first_token_at is None:
        return {}
    metrics: dict[str, float] = {
        "ttft_ms": round((first_token_at - stream_started) * 1000.0, 1),
    }
    tokens = _estimate_output_tokens(output_text)
    gen_s = (last_token_at - first_token_at) if last_token_at is not None else 0.0
    if tokens > 0 and gen_s > 0:
        metrics["tps"] = round(tokens / gen_s, 1)
    return metrics


async def _persist_chat_messages(
    org: uuid.UUID,
    tid: uuid.UUID,
    message: str,
    resp: str,
    *,
    model_u: str,
    cost: float,
    resolved_provider_id: str,
    provider_used: str,
) -> None:
    user_message_id: uuid.UUID | None = None
    assistant_message_id: uuid.UUID | None = None
    assistant_payload = encode_chat_assistant(
        resp,
        model_used=model_u,
        cost_usd=float(cost or 0),
        provider_id=resolved_provider_id,
        provider_used=provider_used,
    )
    try:
        async with get_db_session() as session:
            await session.execute(text("SELECT set_config('app.current_org_id', :v, true)"), {"v": str(org)})
            user_msg = Message(org_id=org, thread_id=tid, role="user", content=message)
            assistant_msg = Message(
                org_id=org,
                thread_id=tid,
                role="assistant",
                content=assistant_payload,
            )
            session.add_all([user_msg, assistant_msg])
            await session.flush()
            user_message_id = user_msg.id
            assistant_message_id = assistant_msg.id
            await session.commit()
        await capture_chat_exchange(
            org_id=org,
            thread_id=tid,
            user_message=message,
            assistant_response=resp,
            provider_id=resolved_provider_id or None,
            model_used=model_u or None,
            user_message_id=user_message_id,
            assistant_message_id=assistant_message_id,
        )
    except Exception as e:
        log_warning(
            "chat stream persist failed",
            subsystem="chat",
            provider="database",
            category=classify_failure(e),
            exc=e,
            operation="chat_stream_persist",
            outcome="error",
        )


def _schedule_chat_persist(coro) -> None:
    task = asyncio.create_task(coro)
    task.add_done_callback(lambda t: None if t.cancelled() or t.exception() is None else log_warning(
        "chat background persist failed",
        subsystem="chat",
        category="unknown_error",
        exc=t.exception(),
        operation="chat_stream_persist",
        outcome="error",
    ))


async def stream_chat_response(
    message: str,
    user_id: str,
    tenant_id: str,
    tier: str,
    *,
    thread_id: uuid.UUID | None = None,
    provider_id: str | None = None,
    model_override: str | None = None,
    preferred_language: str | None = None,
    expert_opinion: bool = False,
    project_id: uuid.UUID | None = None,
    project_setup_bootstrap: bool = False,
) -> AsyncIterator[str]:
    """Zero-buffer NDJSON chat stream — tokens forwarded as they arrive; persist after done."""
    _ = user_id
    stream_started = time.perf_counter()
    first_token_at: float | None = None
    last_token_at: float | None = None
    org = uuid.UUID(tenant_id)
    title = (message.strip()[:512] or "Chat")[:512]
    tid = await resolve_thread_id(org, thread_id, title=title)

    if is_project_setup_thread(tid):
        workspace_ctx = resolve_workspace_context_for_org(tenant_id, thread_id=str(tid))
        attach_workspace_to_request_diagnostics(workspace_ctx)
        # Portable portfolio DB: data/projects/{slug}/project_context.db (see thread_store.resolve_thread_db_path)
        history_rows = await _load_chat_history_messages(org, tid)
        history = format_full_thread_history_for_handoff(history_rows)
        bootstrap = project_setup_bootstrap or not history_rows
        user_visible = message if not bootstrap else ""
        parts: list[str] = []
        done_event: dict[str, Any] | None = None
        async for line in stream_project_agent_response(
            user_message=user_visible,
            tenant_id=tenant_id,
            thread_id=tid,
            bootstrap=bootstrap,
            conversation_history=history,
        ):
            event = json.loads(line)
            if event.get("type") == "chunk":
                parts.append(str(event.get("content") or ""))
            if event.get("type") == "done":
                done_event = event
            yield line
        resp = "".join(parts) or str((done_event or {}).get("response") or "")
        if resp:
            try:
                persist_chat_exchange_sqlite(
                    tid,
                    user_text=message if not bootstrap else "[project_setup_bootstrap]",
                    assistant_content=encode_chat_assistant(
                        resp,
                        model_used=str((done_event or {}).get("model_used") or ""),
                        cost_usd=float((done_event or {}).get("cost_usd") or 0.0),
                        provider_id="gpt",
                    ),
                    provider="gpt",
                )
            except Exception as e:
                log_warning(
                    "project setup sqlite persist failed",
                    subsystem="chat",
                    provider="thread_store",
                    category=classify_failure(e),
                    exc=e,
                    operation="project_setup_persist",
                    outcome="error",
                )
        return

    # Standard chat threads: no filesystem tools in the LLM payload (lean latency path).
    ui_provider_id = (provider_id or "").strip().lower() or None
    provider_id, model_override, expert_opinion = apply_chat_intent_to_request(
        message,
        provider_id=provider_id,
        model_override=model_override,
        expert_opinion=expert_opinion,
    )
    resolved_provider_id = (provider_id or "").strip().lower() or None
    cross_engine = is_cross_engine_turn(
        ui_provider_id=ui_provider_id,
        resolved_provider_id=resolved_provider_id,
    )

    try:
        validate_chat_model_override(provider_id, model_override)
    except ValueError as e:
        yield _stream_ndjson({"type": "error", "message": str(e)})
        return

    if expert_opinion:
        opinion_request = (message or "").strip() or DEFAULT_OPINION_REQUEST
        contextual_message = await build_rolling_stream_prompt(org, tid, opinion_request)
        effective_message = await inject_knowledge_few_shot(message, contextual_message)
        effective_message = apply_language_context(effective_message, preferred_language)
        stream_system = RAW_STREAM_SYSTEM
        persist_user_text = opinion_request
    elif cross_engine:
        request_text = (message or "").strip()
        contextual_message = await build_cross_engine_thread_prompt(org, tid, request_text)
        effective_message = await inject_knowledge_few_shot(message, contextual_message)
        effective_message = apply_language_context(effective_message, preferred_language)
        stream_system = CROSS_ENGINE_HANDOFF_SYSTEM
        persist_user_text = request_text
    else:
        contextual_message = await build_chat_message_with_thread_context(org, tid, message)
        effective_message = await inject_knowledge_few_shot(message, contextual_message)
        effective_message = apply_language_context(effective_message, preferred_language)
        stream_system = None
        persist_user_text = message

    yield _stream_ndjson(
        {
            "type": "meta",
            "thread_id": str(tid),
            "mode": "rolling" if expert_opinion else ("handoff" if cross_engine else "chat"),
            "provider_id": resolved_provider_id,
        }
    )

    if project_id and not expert_opinion:
        try:
            copilot_events = await run_copilot_preamble(message, org, project_id)
            for evt in copilot_events:
                yield _stream_ndjson(evt)
        except Exception as e:
            yield _stream_ndjson({"type": "error", "message": str(e) or "Copilot tool execution failed."})
            return

    parts: list[str] = []
    model_u = ""
    provider_used = ""
    resolved_provider_id = resolved_provider_id or (provider_id or "").strip().lower()

    try:
        async for chunk, model, prov in route_request_stream(
            effective_message,
            tenant_id,
            tier,
            provider_id=provider_id,
            model_override=model_override,
            system=stream_system,
        ):
            if model:
                model_u = model
            if prov:
                provider_used = prov
            if not model and chunk:
                yield _stream_ndjson({"type": "error", "message": chunk})
                return
            if not chunk:
                continue
            now = time.perf_counter()
            if first_token_at is None:
                first_token_at = now
            last_token_at = now
            parts.append(chunk)
            yield _stream_ndjson({"type": "chunk", "content": chunk})
    except Exception as e:
        yield _stream_ndjson({"type": "error", "message": str(e) or "Chat stream failed."})
        return

    resp = "".join(parts)
    if not resolved_provider_id:
        resolved_provider_id = gateway_to_provider_id(provider_used)

    accounted = get_last_accounted_call() or {}
    stream_cost = float(accounted.get("cost_usd") or 0.0)

    perf = _finalize_stream_perf(
        stream_started=stream_started,
        first_token_at=first_token_at,
        last_token_at=last_token_at,
        output_text=resp,
    )

    sqlite_user_id: int | None = None
    sqlite_assistant_id: int | None = None
    try:
        sqlite_user_id, sqlite_assistant_id = persist_chat_exchange_sqlite(
            tid,
            user_text=persist_user_text,
            assistant_content=encode_chat_assistant(
                resp,
                model_used=model_u,
                cost_usd=stream_cost,
                provider_id=resolved_provider_id,
                provider_used=provider_used,
            ),
            provider=resolved_provider_id or None,
        )
    except Exception as e:
        log_warning(
            "chat sqlite persist failed",
            subsystem="chat",
            provider="thread_store",
            category=classify_failure(e),
            exc=e,
            operation="chat_sqlite_persist",
            outcome="error",
        )

    _schedule_chat_persist(
        _persist_chat_messages(
            org,
            tid,
            persist_user_text,
            resp,
            model_u=model_u,
            cost=stream_cost,
            resolved_provider_id=resolved_provider_id,
            provider_used=provider_used,
        )
    )

    yield _stream_ndjson(
        {
            "type": "done",
            "thread_id": str(tid),
            "response": resp,
            "model_used": model_u,
            "cost_usd": stream_cost,
            "provider_id": resolved_provider_id,
            "provider_used": provider_used,
            "mode": "rolling" if expert_opinion else ("handoff" if cross_engine else "chat"),
            "sqlite_user_id": sqlite_user_id,
            "sqlite_assistant_id": sqlite_assistant_id,
            "execution_id": accounted.get("execution_id"),
            "call_id": accounted.get("call_id"),
            "usage_status": accounted.get("usage_status"),
            "pricing_version": accounted.get("pricing_version"),
            **perf,
        }
    )


async def handle_chat(
    message: str,
    user_id: str,
    tenant_id: str,
    tier: str,
    *,
    thread_id: uuid.UUID | None = None,
    provider_id: str | None = None,
    model_override: str | None = None,
    preferred_language: str | None = None,
) -> dict[str, Any]:
    _ = user_id
    org = uuid.UUID(tenant_id)
    title = (message.strip()[:512] or "Chat")[:512]
    tid = await resolve_thread_id(org, thread_id, title=title)

    contextual_message = await build_chat_message_with_thread_context(org, tid, message)
    effective_message = apply_language_context(contextual_message, preferred_language)
    raw = await route_request(
        effective_message,
        tenant_id,
        tier,
        provider_id=provider_id,
        model_override=model_override,
    )
    resp = raw.get("content", "")
    model_u = raw.get("model_used", "")
    cost = raw.get("cost_usd", 0.0)
    provider_used = raw.get("provider_used") or ""
    resolved_provider_id = (provider_id or "").strip().lower() or gateway_to_provider_id(provider_used)

    user_message_id: uuid.UUID | None = None
    assistant_message_id: uuid.UUID | None = None
    assistant_payload = encode_chat_assistant(
        resp,
        model_used=model_u,
        cost_usd=float(cost or 0),
        provider_id=resolved_provider_id,
        provider_used=provider_used,
    )
    try:
        persist_chat_exchange_sqlite(
            tid,
            user_text=message,
            assistant_content=assistant_payload,
            provider=resolved_provider_id or None,
        )
    except Exception as e:
        log_warning(
            "chat sqlite persist failed",
            subsystem="chat",
            provider="thread_store",
            category=classify_failure(e),
            exc=e,
            operation="chat_sqlite_persist",
            outcome="error",
        )
    async with get_db_session() as session:
        await session.execute(text("SELECT set_config('app.current_org_id', :v, true)"), {"v": str(org)})
        user_msg = Message(org_id=org, thread_id=tid, role="user", content=message)
        assistant_msg = Message(
            org_id=org,
            thread_id=tid,
            role="assistant",
            content=assistant_payload,
        )
        session.add_all([user_msg, assistant_msg])
        await session.flush()
        user_message_id = user_msg.id
        assistant_message_id = assistant_msg.id
        await session.commit()

    await capture_chat_exchange(
        org_id=org,
        thread_id=tid,
        user_message=message,
        assistant_response=resp,
        provider_id=resolved_provider_id or None,
        model_used=model_u or None,
        user_message_id=user_message_id,
        assistant_message_id=assistant_message_id,
    )
    return {
        "thread_id": str(tid),
        "response": resp,
        "model_used": model_u,
        "cost_usd": cost,
        "provider_id": resolved_provider_id,
        "provider_used": provider_used,
    }
