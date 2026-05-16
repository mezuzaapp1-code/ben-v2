"""T04 Chat: model gateway + persist thread/messages with RLS org context."""
import uuid
from typing import Any

from sqlalchemy import text

from database.connection import get_db_session
from database.models import Message, Thread
from services.message_format import encode_chat_assistant
from services.model_gateway import route_request
from services.thread_service import resolve_thread_id


async def handle_chat(
    message: str,
    user_id: str,
    tenant_id: str,
    tier: str,
    *,
    thread_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    _ = user_id
    org = uuid.UUID(tenant_id)
    title = (message.strip()[:512] or "Chat")[:512]
    tid = await resolve_thread_id(org, thread_id, title=title)

    raw = await route_request(message, tenant_id, tier)
    resp, model_u, cost = raw.get("content", ""), raw.get("model_used", ""), raw.get("cost_usd", 0.0)

    async with get_db_session() as session:
        await session.execute(text("SELECT set_config('app.current_org_id', :v, true)"), {"v": str(org)})
        session.add_all(
            [
                Message(org_id=org, thread_id=tid, role="user", content=message),
                Message(
                    org_id=org,
                    thread_id=tid,
                    role="assistant",
                    content=encode_chat_assistant(resp, model_used=model_u, cost_usd=float(cost or 0)),
                ),
            ]
        )
        await session.commit()
    return {"thread_id": str(tid), "response": resp, "model_used": model_u, "cost_usd": cost}
