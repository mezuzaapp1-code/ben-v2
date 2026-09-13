"""Compile chat payloads. Legacy few-shot injection is contained (Gate 0)."""
from __future__ import annotations

import logging
from typing import Any, Optional

from services.chat_prompt import compose_chat_user_message
from services.knowledge_containment import contained_few_shot_block

logger = logging.getLogger("ben.knowledge_injection")


def wrap_with_few_shot(*, few_shot_block: str, inner_payload: str) -> str:
    examples = (few_shot_block or "").strip()
    body = (inner_payload or "").strip()
    if not examples:
        return body
    if not body:
        return f"<few_shot_examples>\n{examples}\n</few_shot_examples>"
    return (
        f"<few_shot_examples>\n{examples}\n</few_shot_examples>\n\n"
        f"{body}"
    )


async def inject_knowledge_few_shot(
    message: str, compiled_payload: str, context_id: Optional[str] = None
) -> str:
    """Do not attach legacy knowledge examples to the model prompt."""
    few_shot = contained_few_shot_block(message, context_id)
    if few_shot:
        logger.warning("contained few-shot produced content; dropping it")
        few_shot = ""
    return wrap_with_few_shot(few_shot_block=few_shot, inner_payload=compiled_payload)


async def build_thread_payload_with_knowledge(
    *,
    message: str,
    conversation_history: str | None,
    user_text: str,
    context: Optional[Any] = None,
) -> str:
    inner = compose_chat_user_message(
        conversation_history=conversation_history,
        user_text=user_text,
    )
    context_id = getattr(context, "context_id", None)
    return await inject_knowledge_few_shot(message, inner, context_id=context_id)
