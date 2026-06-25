"""Council — copy-paste prompt workflow (single model stream, no expert panel / synthesis)."""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

from services.copy_paste_service import run_copy_paste_opinion, stream_copy_paste_opinion
from services.ops.request_context import attach_request_id
from services.providers.anthropic_provider import ANTHROPIC_FLAGSHIP_MODEL
from services.providers.gemini_provider import GEMINI_FAST_MODEL
from services.providers.openai_provider import OPENAI_REASONING_MODEL

SYNTHESIS_MODEL_DEFAULT = OPENAI_REASONING_MODEL
BUSINESS_MODEL = OPENAI_REASONING_MODEL
GEMINI_MODEL_DEFAULT = GEMINI_FAST_MODEL
ANTHROPIC_MODEL_DEFAULT = ANTHROPIC_FLAGSHIP_MODEL


class CouncilTranscriptPersistError(Exception):
    """Council transcript could not be persisted."""


async def run_council(
    question: str,
    tenant_id: str,
    *,
    thread_id: uuid.UUID | None = None,
    force_codebase: bool = False,
) -> dict[str, Any]:
    _ = force_codebase
    org = uuid.UUID(tenant_id)
    raw = await run_copy_paste_opinion(
        org_id=org,
        thread_id=thread_id,
        tenant_id=tenant_id,
        tier="free",
        opinion_request=question,
        provider_id=None,
    )
    payload = {
        "question": question,
        "council": [],
        "active_experts": [],
        "synthesis": None,
        "available_experts": 0,
        "unavailable_experts": 0,
        "cost_usd": raw.get("cost_usd", 0.0),
        "room": {
            "id": str(uuid.uuid4()),
            "question_id": str(uuid.uuid4()),
            "status": "complete",
            "member_count": 0,
        },
        "fast_track": False,
        "mode": "copy_paste",
        "response": raw.get("response"),
        "thread_id": raw.get("thread_id"),
    }
    return attach_request_id(payload)


async def run_fast_track_council(
    question: str,
    tenant_id: str,
    *,
    thread_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    return await run_council(question, tenant_id, thread_id=thread_id)


async def stream_council_response(
    question: str,
    tenant_id: str,
    *,
    thread_id: uuid.UUID | None = None,
    force_codebase: bool = False,
) -> AsyncIterator[str]:
    _ = force_codebase
    org = uuid.UUID(tenant_id)
    async for line in stream_copy_paste_opinion(
        org_id=org,
        thread_id=thread_id,
        tenant_id=tenant_id,
        tier="free",
        opinion_request=question,
        provider_id=None,
        persist_kind="council",
    ):
        yield line


async def stream_fast_track_response(
    question: str,
    tenant_id: str,
    thread_id: uuid.UUID,
) -> AsyncIterator[str]:
    async for line in stream_council_response(question, tenant_id, thread_id=thread_id):
        yield line
