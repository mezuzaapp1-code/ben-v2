"""Thread list/read and council transcript persistence (tenant-scoped via RLS)."""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select, text
from database.connection import get_db_session
from database.models import Message, Thread
from services.message_format import (
    decode_message,
    encode_chat_assistant,
    encode_council_expert,
    encode_council_synthesis,
)
from services.ops.request_context import attach_request_id

LIST_THREADS_LIMIT = 50


async def _set_org(session, org_id: uuid.UUID) -> None:
    await session.execute(text("SELECT set_config('app.current_org_id', :v, true)"), {"v": str(org_id)})


async def get_thread_for_org(org_id: uuid.UUID, thread_id: uuid.UUID) -> Thread | None:
    async with get_db_session() as session:
        await _set_org(session, org_id)
        row = await session.get(Thread, thread_id)
        if row is None or row.org_id != org_id:
            return None
        return row


async def resolve_thread_id(org_id: uuid.UUID, thread_id: uuid.UUID | None, *, title: str) -> uuid.UUID:
    """Return existing thread id or create a new thread."""
    async with get_db_session() as session:
        await _set_org(session, org_id)
        if thread_id is not None:
            row = await session.get(Thread, thread_id)
            if row is None or row.org_id != org_id:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Thread not found")
            return thread_id
        t = Thread(org_id=org_id, title=(title.strip()[:512] or "Conversation")[:512])
        session.add(t)
        await session.flush()
        await session.commit()
        return t.id


async def list_threads(org_id: uuid.UUID) -> dict[str, Any]:
    async with get_db_session() as session:
        await _set_org(session, org_id)
        q = (
            select(Thread)
            .where(Thread.org_id == org_id)
            .order_by(Thread.updated_at.desc())
            .limit(LIST_THREADS_LIMIT)
        )
        rows = (await session.execute(q)).scalars().all()
        threads = [
            {
                "id": str(t.id),
                "title": t.title,
                "created_at": t.created_at.isoformat() if t.created_at else None,
                "updated_at": t.updated_at.isoformat() if t.updated_at else None,
            }
            for t in rows
        ]
    return attach_request_id({"threads": threads})


async def get_thread_detail(org_id: uuid.UUID, thread_id: uuid.UUID) -> dict[str, Any]:
    async with get_db_session() as session:
        await _set_org(session, org_id)
        row = (await session.execute(select(Thread).where(Thread.id == thread_id, Thread.org_id == org_id))).scalar_one_or_none()
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Thread not found")
        msg_q = (
            select(Message)
            .where(Message.thread_id == thread_id, Message.org_id == org_id)
            .order_by(Message.created_at.asc())
        )
        messages = (await session.execute(msg_q)).scalars().all()
        payload = {
            "thread": {
                "id": str(row.id),
                "title": row.title,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            },
            "messages": [
                {
                    "id": str(m.id),
                    "role": m.role,
                    "created_at": m.created_at.isoformat() if m.created_at else None,
                    **decode_message(m.role, m.content),
                }
                for m in messages
            ],
        }
    return attach_request_id(payload)


async def persist_council_transcript(
    org_id: uuid.UUID,
    thread_id: uuid.UUID,
    question: str,
    *,
    council_members: list[dict[str, Any]],
    synthesis: dict[str, Any] | None,
    total_cost_usd: float,
    synthesis_display_text: str,
) -> None:
    """Append user question + council expert rows + optional synthesis to thread."""
    async with get_db_session() as session:
        await _set_org(session, org_id)
        row = await session.get(Thread, thread_id)
        if row is None or row.org_id != org_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Thread not found")

        to_add: list[Message] = [
            Message(org_id=org_id, thread_id=thread_id, role="user", content=question),
        ]
        for i, m in enumerate(council_members):
            expert = str(m.get("expert") or "Advisor")
            resp = str(m.get("response") or "")
            is_last = i == len(council_members) - 1 and synthesis is None
            cost = total_cost_usd if is_last else 0.0
            to_add.append(
                Message(
                    org_id=org_id,
                    thread_id=thread_id,
                    role="assistant",
                    content=encode_council_expert(
                        expert=expert,
                        response=resp,
                        provider=str(m.get("provider") or ""),
                        model=str(m.get("model") or ""),
                        outcome=str(m.get("outcome") or "ok"),
                        cost_usd=cost if not synthesis else 0.0,
                    ),
                )
            )
        if synthesis is not None:
            to_add.append(
                Message(
                    org_id=org_id,
                    thread_id=thread_id,
                    role="assistant",
                    content=encode_council_synthesis(
                        synthesis=synthesis,
                        cost_usd=total_cost_usd,
                        display_text=synthesis_display_text,
                    ),
                )
            )
        session.add_all(to_add)
        await session.commit()
