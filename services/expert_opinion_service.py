"""Guest expert opinion — rolling context up to an anchor message, persisted per-thread."""
from __future__ import annotations

import json
import os
import uuid
from collections.abc import AsyncIterator, Sequence
from typing import Any

from fastapi import HTTPException

from database.thread_store import insert_thread_message, list_thread_messages_until
from services.message_format import (
    build_adhoc_expert_display_text,
    encode_adhoc_expert,
    user_turn_focus_query_source,
)
from services.inference.gateway_meter import get_last_accounted_call
from services.model_gateway import normalize_chat_provider_id, route_request_stream
from services.ops.failure_classification import classify_failure
from services.ops.request_context import attach_request_id
from services.ops.structured_log import log_warning
from services.rolling_context import DEFAULT_OPINION_REQUEST, RAW_STREAM_SYSTEM, build_rolling_context_prompt
from services.thread_service import ChatHistoryRow, thread_store_messages_as_chat_rows
from services.workspace_files.multi_source import (
    explicit_named_set_incomplete,
    resolve_turn_sources,
    restrict_arg_for_resolution,
    wrap_with_grounding_hint,
)
from services.workspace_files.response_evidence import sanitize_response_evidence
from services.workspace_files.service import load_ready_files_context
from services.workspace_files.thread_sources import load_source_state, log_source_state_error


# Same cap as standard chat. Do not invent a second budget.
WORKSPACE_FILES_CONTEXT_MAX_CHARS = int(
    os.getenv("BEN_WORKSPACE_FILES_CONTEXT_MAX_CHARS", "12000")
)


def _stream_ndjson(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False) + "\n"


def _last_user_query(chat_rows: Sequence[ChatHistoryRow]) -> str:
    """Retrieval query is the last real user turn, not the Add Opinion instruction."""
    for row in reversed(list(chat_rows or ())):
        if str(getattr(row, "role", "") or "").strip().lower() != "user":
            continue
        content = str(getattr(row, "content", "") or "")
        return user_turn_focus_query_source(content)
    return ""


def _used_files_payload(raw: Any) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for item in raw or ():
        if not isinstance(item, dict):
            continue
        file_id = str(item.get("id", "")).strip()
        name = str(item.get("name", "")).strip()
        if file_id and name:
            out.append({"id": file_id, "name": name})
    return out


async def _workspace_file_prompt_prefix(
    org_id: uuid.UUID,
    thread_id: uuid.UUID,
    project_id: uuid.UUID,
    chat_rows: Sequence[ChatHistoryRow],
) -> tuple[str | None, list[dict[str, str]], dict | None]:
    """Canonical assembler only. Clarify / errors skip the file block (fail-closed)."""
    user_query = _last_user_query(chat_rows)
    restrict_arg: list[str] | None = None
    cover_ids: list[str] | None = None
    try:
        source_state = await load_source_state(org_id, thread_id)
        resolution = resolve_turn_sources(user_query, source_state)
        if resolution.is_clarify:
            return None, [], None
        restrict_arg = restrict_arg_for_resolution(resolution)
        if resolution.is_multi:
            cover_ids = list(resolution.file_ids)
    except Exception as exc:  # noqa: BLE001
        log_source_state_error(exc, operation="load_source_restriction", file_id="")
        restrict_arg = []

    wsf = await load_ready_files_context(
        org_id,
        project_id,
        max_chars=WORKSPACE_FILES_CONTEXT_MAX_CHARS,
        user_query=user_query,
        restrict_to_file_ids=restrict_arg,
        cover_file_ids=cover_ids,
    )
    used_files = _used_files_payload(getattr(wsf, "used_files", None))
    used_ids = [item["id"] for item in used_files]
    if cover_ids and len(used_ids) < 2:
        return None, [], None
    if explicit_named_set_incomplete(
        user_query,
        named_ids=getattr(wsf, "explicit_named_ids", None),
        used_ids=used_ids,
    ):
        return None, [], None
    block = str(getattr(wsf, "block", "") or "")
    if not block:
        return None, used_files, sanitize_response_evidence(getattr(wsf, "response_evidence", None))
    payload = block
    if cover_ids or len(used_files) >= 2:
        payload = wrap_with_grounding_hint(payload)
    evidence = sanitize_response_evidence(getattr(wsf, "response_evidence", None))
    return payload, used_files, evidence


async def stream_expert_opinion(
    org_id: uuid.UUID,
    thread_id: uuid.UUID,
    *,
    session_id: uuid.UUID,
    provider_id: str,
    tenant_id: str,
    tier: str,
    anchor_message_id: int | None = None,
    opinion_request: str | None = None,
    message_type: str = "expert_consult",
    project_id: uuid.UUID | None = None,
) -> AsyncIterator[str]:
    try:
        normalized_provider = normalize_chat_provider_id(provider_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if normalized_provider is None:
        raise HTTPException(status_code=400, detail="provider_id is required")

    tid = str(thread_id)
    if anchor_message_id is not None and not list_thread_messages_until(tid, anchor_message_id):
        raise HTTPException(status_code=404, detail="Anchor message not found")

    store_rows = list_thread_messages_until(tid, anchor_message_id)
    chat_rows = thread_store_messages_as_chat_rows(store_rows)
    request = (opinion_request or "").strip() or DEFAULT_OPINION_REQUEST
    prompt = build_rolling_context_prompt(chat_rows, opinion_request=request)

    workspace_files_used: list[dict[str, str]] = []
    persisted_evidence: dict | None = None
    if project_id is not None:
        try:
            prefix, workspace_files_used, persisted_evidence = await _workspace_file_prompt_prefix(
                org_id,
                thread_id,
                project_id,
                chat_rows,
            )
            if prefix:
                prompt = f"{prefix}\n\n{prompt}"
        except Exception as exc:  # noqa: BLE001
            log_warning(
                "workspace files context load failed",
                subsystem="adhoc",
                provider="database",
                category=classify_failure(exc),
                exc=exc,
                operation="workspace_files_context",
                outcome="error",
            )
            workspace_files_used = []
            persisted_evidence = None

    yield _stream_ndjson(
        {
            "type": "meta",
            "thread_id": tid,
            "mode": "expert_consult" if message_type == "expert_consult" else "panel",
            "provider_id": normalized_provider,
            "anchor_message_id": anchor_message_id,
        }
    )

    parts: list[str] = []
    model_u = ""
    provider_used = ""

    try:
        async for chunk, model, prov in route_request_stream(
            prompt,
            tenant_id,
            tier,
            provider_id=normalized_provider,
            system=RAW_STREAM_SYSTEM,
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
            parts.append(chunk)
            yield _stream_ndjson({"type": "chunk", "content": chunk})
    except Exception as exc:
        yield _stream_ndjson({"type": "error", "message": str(exc) or "Expert stream failed."})
        return

    response_text = "".join(parts)
    accounted = get_last_accounted_call() or {}
    stream_cost = float(accounted.get("cost_usd") or 0.0)
    encoded = encode_adhoc_expert(
        session_id=str(session_id),
        provider_id=normalized_provider,
        response=response_text,
        provider_used=provider_used,
        model=model_u,
        outcome="ok" if response_text else "error",
        cost_usd=stream_cost,
        display_content=build_adhoc_expert_display_text(normalized_provider, model_u, response_text),
        used_files=workspace_files_used,
        response_evidence=persisted_evidence,
    )
    sqlite_id = insert_thread_message(
        tid,
        role="assistant",
        content=encoded,
        provider=normalized_provider,
        message_type=message_type,
        insert_after_id=anchor_message_id,
    )

    done_event: dict[str, Any] = {
        "type": "done",
        "thread_id": tid,
        "response": response_text,
        "model_used": model_u,
        "provider_used": provider_used,
        "provider_id": normalized_provider,
        "cost_usd": stream_cost,
        "sqlite_message_id": sqlite_id,
        "kind": "adhoc_expert",
        "message_type": message_type,
        "anchor_message_id": anchor_message_id,
        "execution_id": accounted.get("execution_id"),
        "call_id": accounted.get("call_id"),
        "usage_status": accounted.get("usage_status"),
        "pricing_version": accounted.get("pricing_version"),
        "workspace_files_used": workspace_files_used,
    }
    if persisted_evidence:
        done_event["response_evidence"] = persisted_evidence
    yield _stream_ndjson(done_event)


async def run_expert_opinion(
    org_id: uuid.UUID,
    thread_id: uuid.UUID,
    *,
    session_id: uuid.UUID,
    provider_id: str,
    tenant_id: str,
    tier: str,
    anchor_message_id: int | None = None,
    opinion_request: str | None = None,
    message_type: str = "expert_consult",
    project_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    parts: list[str] = []
    async for line in stream_expert_opinion(
        org_id,
        thread_id,
        session_id=session_id,
        provider_id=provider_id,
        tenant_id=tenant_id,
        tier=tier,
        anchor_message_id=anchor_message_id,
        opinion_request=opinion_request,
        message_type=message_type,
        project_id=project_id,
    ):
        event = json.loads(line)
        if event.get("type") == "meta":
            meta = event
        elif event.get("type") == "chunk":
            parts.append(str(event.get("content") or ""))
        elif event.get("type") == "done":
            meta.update(event)
        elif event.get("type") == "error":
            raise HTTPException(status_code=502, detail=event.get("message") or "Expert stream failed.")

    response_text = "".join(parts) or str(meta.get("response") or "")
    payload: dict[str, Any] = {
        "session_id": str(session_id),
        "provider_id": meta.get("provider_id") or provider_id,
        "model_used": meta.get("model_used") or "",
        "provider_used": meta.get("provider_used") or "",
        "response": response_text,
        "content": response_text,
        "cost_usd": float(meta.get("cost_usd") or 0),
        "sqlite_message_id": meta.get("sqlite_message_id"),
        "anchor_message_id": anchor_message_id,
        "message_type": message_type,
        "kind": "adhoc_expert",
        "outcome": "ok" if response_text else "error",
        "workspace_files_used": meta.get("workspace_files_used") or [],
    }
    if meta.get("response_evidence"):
        payload["response_evidence"] = meta["response_evidence"]
    return attach_request_id(payload)
