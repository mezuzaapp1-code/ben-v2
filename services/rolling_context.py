"""Rolling context pipeline — sequential append of all thread turns for expert opinions."""
from __future__ import annotations

import uuid

from services.message_format import decode_message
from services.thread_service import ChatHistoryRow, _load_chat_history_messages
from services.workspace_files.context_eligibility import source_bound_message_eligible
from services.workspace_files.resource_access import authorize_file_use
from services.workspace_files.source_policy import FILE_INITIAL_READ_EVENT

DEFAULT_OPINION_REQUEST = (
    "Provide your expert opinion on the discussion above. Be direct and concise."
)

RAW_STREAM_SYSTEM = (
    "Respond directly in clear markdown. No JSON. No committee format. "
    "No scorecards or tables unless the user explicitly asked for them."
)

CROSS_ENGINE_HANDOFF_SYSTEM = (
    "You are joining an ongoing 1:1 thread. Prior turns may include answers from other models — "
    "treat the full conversation history as ground truth and respond with complete continuity."
)


def _turn_text(message: ChatHistoryRow) -> str | None:
    decoded = decode_message(message.role, message.content)
    text = str(decoded.get("content") or "").strip()
    return text or None


def build_rolling_context_prompt(
    messages: list[ChatHistoryRow],
    *,
    opinion_request: str,
) -> str:
    """Append every prior turn sequentially, then the latest opinion request."""
    blocks: list[str] = []
    for m in messages:
        text = _turn_text(m)
        if text:
            blocks.append(text)
    req = (opinion_request or "").strip() or DEFAULT_OPINION_REQUEST
    blocks.append(req)
    return "\n\n".join(blocks)


async def _eligible_history_for_execution(
    org_id: uuid.UUID,
    messages: list[ChatHistoryRow],
) -> list[ChatHistoryRow]:
    """Drop source-bound Initial Read turns whose source is not currently authorized."""
    kept: list[ChatHistoryRow] = []
    for message in messages:
        decoded = decode_message(message.role, message.content)
        if str(decoded.get("source_event") or "").strip() != FILE_INITIAL_READ_EVENT:
            kept.append(message)
            continue
        raw_id = str(decoded.get("source_file_id") or "").strip()
        file_id: uuid.UUID | None
        try:
            file_id = uuid.UUID(raw_id) if raw_id else None
        except (TypeError, ValueError):
            file_id = None
        access = await authorize_file_use(org_id, file_id, action="use_in_context")
        if source_bound_message_eligible(decoded, access):
            kept.append(message)
    return kept


async def build_rolling_stream_prompt(
    org_id: uuid.UUID,
    thread_id: uuid.UUID,
    opinion_request: str,
) -> str:
    """Load thread history from DB and build cumulative rolling prompt."""
    messages = await _load_chat_history_messages(org_id, thread_id)
    eligible = await _eligible_history_for_execution(org_id, messages)
    return build_rolling_context_prompt(eligible, opinion_request=opinion_request)
