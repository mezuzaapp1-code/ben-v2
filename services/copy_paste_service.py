"""Copy-paste / council opinion — delegates to rolling context pipeline."""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any, Literal

from services.chat_service import stream_chat_response
from services.rolling_context import DEFAULT_OPINION_REQUEST, RAW_STREAM_SYSTEM, build_rolling_stream_prompt
from services.model_gateway import route_request
from services.thread_service import resolve_thread_id

PersistKind = Literal["chat", "adhoc_expert", "council"]


async def stream_copy_paste_opinion(
    *,
    org_id: uuid.UUID,
    thread_id: uuid.UUID | None,
    tenant_id: str,
    tier: str,
    opinion_request: str,
    provider_id: str | None,
    persist_kind: PersistKind = "council",
    session_id: str | None = None,
) -> AsyncIterator[str]:
    """NDJSON stream via rolling context (all prior turns + opinion request)."""
    _ = persist_kind, session_id
    request = (opinion_request or "").strip() or DEFAULT_OPINION_REQUEST
    async for line in stream_chat_response(
        request,
        "anonymous",
        tenant_id,
        tier,
        thread_id=thread_id,
        provider_id=provider_id,
        expert_opinion=True,
    ):
        yield line


async def run_copy_paste_opinion(
    *,
    org_id: uuid.UUID,
    thread_id: uuid.UUID | None,
    tenant_id: str,
    tier: str,
    opinion_request: str,
    provider_id: str | None = None,
) -> dict[str, Any]:
    org = org_id
    title = (opinion_request.strip()[:512] or "Opinion")[:512]
    tid = await resolve_thread_id(org, thread_id, title=title)
    request = (opinion_request or "").strip() or DEFAULT_OPINION_REQUEST
    prompt = await build_rolling_stream_prompt(org, tid, request)
    raw = await route_request(prompt, tenant_id, tier, provider_id=provider_id, system=RAW_STREAM_SYSTEM)
    return {
        "question": opinion_request,
        "thread_id": str(tid),
        "response": str(raw.get("content") or ""),
        "model_used": raw.get("model_used"),
        "provider_used": raw.get("provider_used"),
        "cost_usd": raw.get("cost_usd", 0.0),
        "mode": "rolling",
        "council": [],
        "synthesis": None,
    }
