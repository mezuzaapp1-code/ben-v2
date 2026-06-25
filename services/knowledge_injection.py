"""Compile chat payloads with optional few-shot knowledge examples."""
from __future__ import annotations
import logging
from typing import Optional, Any
from services.chat_prompt import compose_chat_user_message
from services.knowledge_service import build_knowledge_few_shot_block

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


async def inject_knowledge_few_shot(message: str, compiled_payload: str, context_id: Optional[str] = None) -> str:
    """Attach gold templates when the user references a named knowledge base, scoped to context."""
    
    # Step B: Diagnostic Observability Contract
    if context_id:
        logger.info(f"[CONTEXT NAMESPACE] Query executed. Scoped strictly to Context ID: {context_id}. Global search skipped.")
    else:
        logger.warning("[CONTEXT NAMESPACE] Global query execution trace. Missing context isolation boundary.")

    # Pass the context filter to the knowledge engine
    few_shot = await build_knowledge_few_shot_block(message, context_id=context_id)
    return wrap_with_few_shot(few_shot_block=few_shot, inner_payload=compiled_payload)


async def build_thread_payload_with_knowledge(
    *,
    message: str,
    conversation_history: str | None,
    user_text: str,
    context: Optional[Any] = None
) -> str:
    """Assembles the user payload and threads the active namespace context through to RAG."""
    inner = compose_chat_user_message(
        conversation_history=conversation_history,
        user_text=user_text,
    )
    
    # Extract context_id dynamically from WorkspaceContext if present
    context_id = getattr(context, "context_id", None)
    
    return await inject_knowledge_few_shot(message, inner, context_id=context_id)
