"""Guest expert opinion — rolling context up to an anchor message, persisted per-thread."""
from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import HTTPException

from database.thread_store import insert_thread_message, list_thread_messages_until
from services.message_format import build_adhoc_expert_display_text, encode_adhoc_expert
from services.model_gateway import normalize_chat_provider_id, route_request_stream
from services.ops.request_context import attach_request_id
from services.rolling_context import DEFAULT_OPINION_REQUEST, RAW_STREAM_SYSTEM, build_rolling_context_prompt
from services.thread_service import thread_store_messages_as_chat_rows


def _stream_ndjson(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False) + "\n"


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
    encoded = encode_adhoc_expert(
        session_id=str(session_id),
        provider_id=normalized_provider,
        response=response_text,
        provider_used=provider_used,
        model=model_u,
        outcome="ok" if response_text else "error",
        cost_usd=0.0,
        display_content=build_adhoc_expert_display_text(normalized_provider, model_u, response_text),
    )
    sqlite_id = insert_thread_message(
        tid,
        role="assistant",
        content=encoded,
        provider=normalized_provider,
        message_type=message_type,
        insert_after_id=anchor_message_id,
    )

    yield _stream_ndjson(
        {
            "type": "done",
            "thread_id": tid,
            "response": response_text,
            "model_used": model_u,
            "provider_used": provider_used,
            "provider_id": normalized_provider,
            "cost_usd": 0.0,
            "sqlite_message_id": sqlite_id,
            "kind": "adhoc_expert",
            "message_type": message_type,
            "anchor_message_id": anchor_message_id,
        }
    )


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
    }
    return attach_request_id(payload)
