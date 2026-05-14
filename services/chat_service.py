"""T04 Chat: model gateway + persist thread/messages with RLS org context."""
import uuid
from typing import Any

from sqlalchemy import text

from database.connection import get_db_session
from database.models import Message, Thread
from services.model_gateway import route_request


async def handle_chat(
    message: str, user_id: str, tenant_id: str, tier: str, *, thread_id: uuid.UUID | None = None
) -> dict[str, Any]:
    _ = user_id
    raw = await route_request(message, tenant_id, tier)
    resp, model_u, cost = raw.get("content", ""), raw.get("model_used", ""), raw.get("cost_usd", 0.0)
    org = uuid.UUID(tenant_id)
    async with get_db_session() as session:
        await session.execute(text("SELECT set_config('app.current_org_id', :v, true)"), {"v": str(org)})
        if thread_id is None:
            title = (message.strip()[:512] or "Chat")[:512]
            t = Thread(org_id=org, title=title)
            session.add(t)
            await session.flush()
            tid = t.id
        else:
            tid = thread_id
        session.add_all(
            [
                Message(org_id=org, thread_id=tid, role="user", content=message),
                Message(org_id=org, thread_id=tid, role="assistant", content=resp),
            ]
        )
        await session.commit()
    return {"thread_id": str(tid), "response": resp, "model_used": model_u, "cost_usd": cost}
