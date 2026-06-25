"""Rolling context pipeline — sequential append of all thread turns for expert opinions."""
from __future__ import annotations

import uuid

from services.message_format import decode_message
from services.thread_service import ChatHistoryRow, _load_chat_history_messages

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
    if message.role == "user":
        text = str(message.content or "").strip()
        return text or None
    if message.role == "assistant":
        decoded = decode_message(message.role, message.content)
        text = str(decoded.get("content") or "").strip()
        return text or None
    return None


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


async def build_rolling_stream_prompt(
    org_id: uuid.UUID,
    thread_id: uuid.UUID,
    opinion_request: str,
) -> str:
    """Load thread history from DB and build cumulative rolling prompt."""
    messages = await _load_chat_history_messages(org_id, thread_id)
    return build_rolling_context_prompt(messages, opinion_request=opinion_request)
