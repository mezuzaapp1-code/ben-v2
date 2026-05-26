"""T04 Chat: model gateway + persist thread/messages with RLS org context."""
import uuid
from typing import Any

from sqlalchemy import text

from database.connection import get_db_session
from database.models import Message, Thread
from services.chat_language import apply_language_context
from services.message_format import encode_chat_assistant, gateway_to_provider_id
from services.model_gateway import route_request
from services.ben_log_service import capture_chat_exchange
from services.thread_service import resolve_thread_id


async def handle_chat(
    message: str,
    user_id: str,
    tenant_id: str,
    tier: str,
    *,
    thread_id: uuid.UUID | None = None,
    provider_id: str | None = None,
    preferred_language: str | None = None,
) -> dict[str, Any]:
    _ = user_id
    org = uuid.UUID(tenant_id)
    title = (message.strip()[:512] or "Chat")[:512]
    tid = await resolve_thread_id(org, thread_id, title=title)

    effective_message = apply_language_context(message, preferred_language)
    raw = await route_request(effective_message, tenant_id, tier, provider_id=provider_id)
    resp = raw.get("content", "")
    model_u = raw.get("model_used", "")
    cost = raw.get("cost_usd", 0.0)
    provider_used = raw.get("provider_used") or ""
    resolved_provider_id = (provider_id or "").strip().lower() or gateway_to_provider_id(provider_used)

    user_message_id: uuid.UUID | None = None
    assistant_message_id: uuid.UUID | None = None
    async with get_db_session() as session:
        await session.execute(text("SELECT set_config('app.current_org_id', :v, true)"), {"v": str(org)})
        user_msg = Message(org_id=org, thread_id=tid, role="user", content=message)
        assistant_msg = Message(
            org_id=org,
            thread_id=tid,
            role="assistant",
            content=encode_chat_assistant(
                resp,
                model_used=model_u,
                cost_usd=float(cost or 0),
                provider_id=resolved_provider_id,
                provider_used=provider_used,
            ),
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
