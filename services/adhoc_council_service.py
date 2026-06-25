"""Ad-hoc expert opinion — copy-paste prompt, single model stream (no synthesis)."""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import HTTPException

from services.copy_paste_service import stream_copy_paste_opinion
from services.rolling_context import DEFAULT_OPINION_REQUEST
from services.model_gateway import normalize_chat_provider_id
from services.ops.request_context import attach_request_id


async def stream_adhoc_expert(
    org_id: uuid.UUID,
    thread_id: uuid.UUID,
    *,
    session_id: uuid.UUID,
    provider_id: str,
    tenant_id: str,
    tier: str,
    opinion_request: str | None = None,
) -> AsyncIterator[str]:
    try:
        normalized_provider = normalize_chat_provider_id(provider_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if normalized_provider is None:
        raise HTTPException(status_code=400, detail="provider_id is required")

    request = (opinion_request or "").strip() or DEFAULT_OPINION_REQUEST
    async for line in stream_copy_paste_opinion(
        org_id=org_id,
        thread_id=thread_id,
        tenant_id=tenant_id,
        tier=tier,
        opinion_request=request,
        provider_id=normalized_provider,
        persist_kind="adhoc_expert",
        session_id=str(session_id),
    ):
        yield line


async def run_adhoc_expert(
    org_id: uuid.UUID,
    thread_id: uuid.UUID,
    *,
    session_id: uuid.UUID,
    provider_id: str,
    tenant_id: str,
    tier: str,
    opinion_request: str | None = None,
) -> dict[str, Any]:
    """Non-stream ad-hoc expert (collects full stream server-side)."""
    parts: list[str] = []
    meta: dict[str, Any] = {}
    async for line in stream_adhoc_expert(
        org_id,
        thread_id,
        session_id=session_id,
        provider_id=provider_id,
        tenant_id=tenant_id,
        tier=tier,
        opinion_request=opinion_request,
    ):
        import json

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
    try:
        normalized_provider = normalize_chat_provider_id(provider_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    payload: dict[str, Any] = {
        "session_id": str(session_id),
        "provider_id": normalized_provider,
        "model_used": meta.get("model_used") or "",
        "provider_used": meta.get("provider_used") or "",
        "response": response_text,
        "content": response_text,
        "cost_usd": float(meta.get("cost_usd") or 0),
        "outcome": "ok" if response_text else "error",
        "mode": "copy_paste",
    }
    return attach_request_id(payload)
