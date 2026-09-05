"""Thread list/read and council transcript persistence (tenant-scoped via RLS)."""
from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select, text
from database.connection import get_db_session
from database.models import Message, Thread
from database.thread_store import (
    ThreadStoreMessage,
    delete_thread_database_file,
    delete_thread_metadata,
    get_thread_metadata,
    get_thread_project_slug,
    get_thread_session_type,
    insert_thread_message,
    list_thread_messages,
    promote_thread_to_portable_storage,
    release_thread_database_files,
    upsert_thread_metadata,
)
from services.message_format import (
    decode_message,
    encode_adhoc_expert,
    encode_adhoc_synthesis,
    encode_chat_assistant,
    encode_council_expert,
    encode_council_synthesis,
    provider_display_label,
)
from services.ops.persistence_integrity import (
    audit_thread_messages_for_org,
    findings_to_safe_codes,
    validate_council_member,
)
from services.ops.request_context import attach_request_id
from services.ops.runtime_diagnostics import record_transcript_persist_timeout
from services.ops.structured_log import log_warning
from services.ops.timeouts import DB_OPERATION_TIMEOUT_S

LIST_THREADS_LIMIT = 50
CHAT_HISTORY_MAX_CHARS = int(os.getenv("BEN_CHAT_HISTORY_MAX_CHARS", "16000"))
CHAT_HISTORY_MAX_TURNS = int(os.getenv("BEN_CHAT_HISTORY_MAX_TURNS", "24"))

_BEN_PREFIX = '{"ben":'


@dataclass
class ChatHistoryRow:
    """Lightweight message row for handoff / rolling-context (SQLite or Postgres)."""

    role: str
    content: str
    id: int | uuid.UUID | None = None
    created_at: str | None = None
    org_id: uuid.UUID | None = None
    thread_id: uuid.UUID | None = None


def thread_store_messages_as_chat_rows(messages: list[ThreadStoreMessage]) -> list[ChatHistoryRow]:
    return [
        ChatHistoryRow(
            id=m.id,
            role=m.role,
            content=m.content,
            created_at=m.created_at,
        )
        for m in messages
    ]


def persist_chat_exchange_sqlite(
    thread_id: uuid.UUID | str,
    *,
    user_text: str,
    assistant_content: str,
    provider: str | None = None,
) -> tuple[int, int]:
    tid = str(thread_id)
    user_id = insert_thread_message(
        tid,
        role="user",
        content=user_text,
        message_type="normal",
    )
    assistant_id = insert_thread_message(
        tid,
        role="assistant",
        content=assistant_content,
        provider=provider,
        message_type="normal",
    )
    return user_id, assistant_id


def persist_assistant_message_sqlite(
    thread_id: uuid.UUID | str,
    *,
    encoded_content: str,
    provider: str | None = None,
) -> int:
    """Assistant-only row (no fake user turn). Used by file initial-read."""
    return insert_thread_message(
        str(thread_id),
        role="assistant",
        content=encoded_content,
        provider=provider,
        message_type="normal",
    )


def persist_expert_message_sqlite(
    thread_id: uuid.UUID | str,
    *,
    encoded_content: str,
    provider: str,
    message_type: str = "expert_consult",
    insert_after_id: int | None = None,
) -> int:
    return insert_thread_message(
        str(thread_id),
        role="assistant",
        content=encoded_content,
        provider=provider,
        message_type=message_type,
        insert_after_id=insert_after_id,
    )


def _sqlite_messages_for_api(thread_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = list_thread_messages(str(thread_id))
    payload: list[dict[str, Any]] = []
    for row in rows:
        decoded = decode_message(row.role, row.content)
        payload.append(
            {
                "id": str(row.id),
                "sqlite_message_id": row.id,
                "role": row.role,
                "created_at": row.created_at,
                "message_type": row.message_type,
                "insert_after_id": row.insert_after_id,
                **decoded,
            }
        )
    return payload
_SYNTHESIS_REF_KEYWORDS_EN = frozenset({"synthesis", "summary"})
_SYNTHESIS_REF_KEYWORDS_HE = (
    "סינתזה",
    "סינטזה",
    "סינטז",
    "סיכום",
    "סינטזה של בן",
    "סיכום של בן",
)


async def _set_org(session, org_id: uuid.UUID) -> None:
    await session.execute(text("SELECT set_config('app.current_org_id', :v, true)"), {"v": str(org_id)})


async def get_thread_for_org(org_id: uuid.UUID, thread_id: uuid.UUID) -> Thread | None:
    async with get_db_session() as session:
        await _set_org(session, org_id)
        row = await session.get(Thread, thread_id)
        if row is None or row.org_id != org_id:
            return None
        return row


_PROJECT_SLUG_RE = re.compile(r'"project_slug"\s*:\s*"([^"]+)"')


def _project_slug_for_thread(thread_id: uuid.UUID | str, *, title: str | None = None) -> str | None:
    meta = get_thread_metadata(str(thread_id))
    if meta and meta.get("project_slug"):
        return str(meta["project_slug"]).strip() or None
    for row in list_thread_messages(str(thread_id)):
        for match in _PROJECT_SLUG_RE.finditer(row.content or ""):
            slug = match.group(1).strip()
            if slug:
                return slug
    if meta and meta.get("session_type") == "project_setup" and title:
        from services.project_tools import projects_root, slugify_project_name

        slug = slugify_project_name(title)
        if (projects_root() / slug).exists():
            return slug
    return None


async def delete_thread(org_id: uuid.UUID, thread_id: uuid.UUID) -> dict[str, Any]:
    """Purge Postgres thread, per-thread SQLite, system metadata, and project folder."""
    row = await get_thread_for_org(org_id, thread_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Thread not found")

    project_slug = get_thread_project_slug(str(thread_id))

    async with get_db_session() as session:
        await _set_org(session, org_id)
        pg_row = await session.get(Thread, thread_id)
        if pg_row is None or pg_row.org_id != org_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Thread not found")
        await session.delete(pg_row)
        await session.commit()

    release_thread_database_files(str(thread_id))

    project_folder_removed = False
    if project_slug:
        from services.project_tools import delete_project_directory

        delete_project_directory(project_slug)
        project_folder_removed = True
    else:
        delete_thread_database_file(str(thread_id))

    delete_thread_metadata(str(thread_id), str(org_id))

    return attach_request_id(
        {
            "deleted": True,
            "thread_id": str(thread_id),
            "project_slug": project_slug,
            "project_folder_removed": project_folder_removed,
        }
    )


async def promote_thread_to_project(
    org_id: uuid.UUID,
    thread_id: uuid.UUID,
    *,
    project_slug: str,
) -> dict[str, Any]:
    """Promote a standard chat thread into a portable project workspace portfolio."""
    row = await get_thread_for_org(org_id, thread_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Thread not found")
    if is_project_setup_thread(thread_id):
        raise HTTPException(status.HTTP_409_CONFLICT, "Thread is already a project workspace")

    slug = (project_slug or "").strip()
    if not slug:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "project_slug is required")

    try:
        payload = promote_thread_to_portable_storage(
            thread_id=str(thread_id),
            org_id=str(org_id),
            project_slug=slug,
        )
    except ValueError as exc:
        message = str(exc)
        if "already" in message.lower():
            raise HTTPException(status.HTTP_409_CONFLICT, message) from exc
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, message) from exc

    return attach_request_id({"thread": payload, "promoted": True})


async def resolve_thread_id(org_id: uuid.UUID, thread_id: uuid.UUID | None, *, title: str) -> uuid.UUID:
    """Return existing thread id or create a new thread."""
    async with get_db_session() as session:
        await _set_org(session, org_id)
        if thread_id is not None:
            row = await session.get(Thread, thread_id)
            if row is None or row.org_id != org_id:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Thread not found")
            upsert_thread_metadata(
                thread_id=str(thread_id),
                org_id=str(org_id),
                title=row.title,
            )
            return thread_id
        t = Thread(org_id=org_id, title=(title.strip()[:512] or "Conversation")[:512])
        session.add(t)
        await session.flush()
        await session.commit()
        upsert_thread_metadata(
            thread_id=str(t.id),
            org_id=str(org_id),
            title=t.title,
        )
        return t.id


async def create_conversation_thread(
    org_id: uuid.UUID,
    *,
    title: str | None = None,
) -> dict[str, Any]:
    """Persist a chat thread via the same path as the first user message."""
    thread_title = (title or "").strip() or "Conversation"
    tid = await resolve_thread_id(org_id, None, title=thread_title)
    meta = get_thread_metadata(str(tid)) or {}
    payload = {
        "thread": {
            "id": str(tid),
            "title": thread_title,
            "session_type": meta.get("session_type") or "chat",
            "project_slug": meta.get("project_slug"),
        }
    }
    return attach_request_id(payload)


async def create_project_workspace_thread(
    org_id: uuid.UUID,
    *,
    project_slug: str | None = None,
    title: str | None = None,
) -> dict[str, Any]:
    """Instant project onboarding workspace — metadata in system_main.db + Postgres thread."""
    from services.project_tools import create_project_directory, slugify_project_name

    thread_title = (title or "").strip() or "New Project Workspace"
    tid = await resolve_thread_id(org_id, None, title=thread_title)
    if project_slug:
        slug = slugify_project_name(project_slug)
        if not slug:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid project_slug")
    else:
        slug = slugify_project_name(f"workspace-{str(tid).replace('-', '')[:12]}")
    create_project_directory(slug)
    upsert_thread_metadata(
        thread_id=str(tid),
        org_id=str(org_id),
        title=thread_title,
        session_type="project_setup",
        project_slug=slug,
    )
    payload = {
        "thread": {
            "id": str(tid),
            "title": thread_title,
            "session_type": "project_setup",
            "project_slug": slug,
        }
    }
    return attach_request_id(payload)


def is_project_setup_thread(thread_id: uuid.UUID | str) -> bool:
    return get_thread_session_type(str(thread_id)) == "project_setup"


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
        threads = []
        for t in rows:
            meta = get_thread_metadata(str(t.id))
            threads.append(
                {
                    "id": str(t.id),
                    "title": t.title,
                    "created_at": t.created_at.isoformat() if t.created_at else None,
                    "updated_at": t.updated_at.isoformat() if t.updated_at else None,
                    "session_type": (meta or {}).get("session_type") or "chat",
                    "project_slug": (meta or {}).get("project_slug"),
                }
            )
    return attach_request_id({"threads": threads})


async def get_thread_detail(org_id: uuid.UUID, thread_id: uuid.UUID) -> dict[str, Any]:
    async with get_db_session() as session:
        await _set_org(session, org_id)
        row = (await session.execute(select(Thread).where(Thread.id == thread_id, Thread.org_id == org_id))).scalar_one_or_none()
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Thread not found")
        sqlite_messages = _sqlite_messages_for_api(thread_id)
        if sqlite_messages:
            api_messages = sqlite_messages
            integrity_codes: list[str] = []
        else:
            msg_q = (
                select(Message)
                .where(Message.thread_id == thread_id, Message.org_id == org_id)
                .order_by(Message.created_at.asc())
            )
            messages = (await session.execute(msg_q)).scalars().all()
            integrity_findings = audit_thread_messages_for_org(org_id, thread_id, messages)
            integrity_codes = findings_to_safe_codes(integrity_findings)
            if integrity_codes:
                log_warning(
                    "thread rehydrate integrity findings",
                    subsystem="persistence_integrity",
                    operation="thread_rehydrate",
                    outcome="warning",
                    integrity_codes=integrity_codes,
                    finding_count=len(integrity_findings),
                )
            api_messages = [
                {
                    "id": str(m.id),
                    "role": m.role,
                    "created_at": m.created_at.isoformat() if m.created_at else None,
                    **decode_message(m.role, m.content),
                }
                for m in messages
            ]
        thread_meta = get_thread_metadata(str(thread_id)) or {}
        payload = {
            "thread": {
                "id": str(row.id),
                "title": row.title,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
                "session_type": thread_meta.get("session_type") or "chat",
                "project_slug": thread_meta.get("project_slug"),
            },
            "messages": api_messages,
        }
        if integrity_codes:
            payload["integrity_warnings"] = integrity_codes
    return attach_request_id(payload)


async def list_thread_messages_ordered(org_id: uuid.UUID, thread_id: uuid.UUID) -> list[Message]:
    """Ordered message rows for a tenant-owned thread; 404 if missing."""
    if await get_thread_for_org(org_id, thread_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Thread not found")
    async with get_db_session() as session:
        await _set_org(session, org_id)
        msg_q = (
            select(Message)
            .where(Message.thread_id == thread_id, Message.org_id == org_id)
            .order_by(Message.created_at.asc())
        )
        return list((await session.execute(msg_q)).scalars().all())


def user_message_references_synthesis(message: str) -> bool:
    """True when the user is likely asking about a prior BEN synthesis."""
    text = (message or "").strip()
    if not text:
        return False
    lower = text.lower()
    if any(kw in lower for kw in _SYNTHESIS_REF_KEYWORDS_EN):
        return True
    return any(kw in text for kw in _SYNTHESIS_REF_KEYWORDS_HE)


def _envelope_kind(role: str, content: str) -> str | None:
    if role != "assistant" or not content.startswith(_BEN_PREFIX):
        return None
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return None
    if isinstance(data, dict) and data.get("ben") == 1:
        kind = data.get("kind")
        return str(kind) if kind else None
    return None


def _synthesis_body_from_decoded(decoded: dict[str, Any]) -> str:
    text = str(decoded.get("content") or "").strip()
    if text:
        return text
    syn = decoded.get("synthesis")
    if not isinstance(syn, dict):
        return ""
    parts: list[str] = []
    for key in (
        "recommendation",
        "shared_recommendation",
        "consensus_points",
        "disagreement_points",
        "legal_reasoning",
        "operational_reasoning",
        "strategic_reasoning",
        "infrastructure_reasoning",
        "minority_or_unique_views",
    ):
        val = syn.get(key)
        if val is None or val == "":
            continue
        if isinstance(val, list):
            val = "; ".join(str(x) for x in val)
        parts.append(f"{key}: {val}")
    next_steps = syn.get("next_steps")
    if isinstance(next_steps, list) and next_steps:
        parts.append("next_steps: " + json.dumps(next_steps, ensure_ascii=False))
    return "\n".join(parts).strip()


def format_latest_adhoc_synthesis_for_chat(messages: list[Message]) -> str | None:
    """Latest ad-hoc synthesis line for main /chat context injection."""
    for m in reversed(messages):
        if m.role != "assistant":
            continue
        if _envelope_kind(m.role, m.content) != "adhoc_synthesis":
            continue
        decoded = decode_message(m.role, m.content)
        body = _synthesis_body_from_decoded(decoded)
        if body:
            return f"BEN (Prior Session Synthesis): {body}"
    return None


def augment_chat_message_with_synthesis_context(message: str, messages: list[Message]) -> str:
    """Prepend latest ad-hoc synthesis when the user explicitly references it."""
    if not user_message_references_synthesis(message):
        return message
    block = format_latest_adhoc_synthesis_for_chat(messages)
    if not block:
        return message
    return f"{block}\n\nUser request:\n{message}"


def format_thread_history_for_chat(messages: list[Message]) -> str | None:
    """Plain transcript from DB rows for 1:1 /chat context — no synthesis re-injection."""
    if not messages:
        return None
    lines: list[str] = []
    for m in messages[-CHAT_HISTORY_MAX_TURNS:]:
        decoded = decode_message(m.role, m.content)
        text = str(decoded.get("content") or "").strip()
        if not text:
            continue
        label = "User" if m.role == "user" else "Assistant"
        lines.append(f"{label}: {text}")
    while lines and sum(len(line) for line in lines) > CHAT_HISTORY_MAX_CHARS:
        lines.pop(0)
    if not lines:
        return None
    return "\n\n".join(lines)


def _history_speaker_label(role: str, decoded: dict[str, Any]) -> str:
    if role == "user":
        return "User"
    provider_id = str(decoded.get("provider_id") or "").strip()
    if provider_id:
        return provider_display_label(provider_id) or provider_id.title()
    return "Assistant"


def format_full_thread_history_for_handoff(messages: list[Message] | list[ChatHistoryRow]) -> str | None:
    """Full DB transcript with provider labels for cross-engine thread handoff."""
    if not messages:
        return None
    lines: list[str] = []
    for m in messages:
        decoded = decode_message(m.role, m.content)
        text = str(decoded.get("content") or "").strip()
        if not text:
            continue
        label = _history_speaker_label(m.role, decoded)
        lines.append(f"{label}: {text}")
    while lines and sum(len(line) for line in lines) > CHAT_HISTORY_MAX_CHARS:
        lines.pop(0)
    if not lines:
        return None
    return "\n\n".join(lines)


async def build_cross_engine_thread_prompt(
    org_id: uuid.UUID,
    thread_id: uuid.UUID,
    user_text: str,
) -> str:
    """Inject entire persisted thread history when routing to a different Tier 1 engine."""
    from services.chat_prompt import compose_chat_user_message

    messages = await _load_chat_history_messages(org_id, thread_id)
    history = format_full_thread_history_for_handoff(messages)
    return compose_chat_user_message(conversation_history=history, user_text=user_text)


async def _load_chat_history_messages(
    org_id: uuid.UUID,
    thread_id: uuid.UUID,
) -> list[Message] | list[ChatHistoryRow]:
    """Best-effort prior turns for /chat; prefers isolated per-thread SQLite."""
    try:
        store_rows = list_thread_messages(str(thread_id))
        if store_rows:
            return thread_store_messages_as_chat_rows(store_rows)
        async with asyncio.timeout(DB_OPERATION_TIMEOUT_S):
            async with get_db_session() as session:
                await _set_org(session, org_id)
                row = await session.get(Thread, thread_id)
                if row is None or row.org_id != org_id:
                    return []
                msg_q = (
                    select(Message)
                    .where(Message.thread_id == thread_id, Message.org_id == org_id)
                    .order_by(Message.created_at.asc())
                )
                return list((await session.execute(msg_q)).scalars().all())
    except Exception:
        return []


async def build_chat_message_with_thread_context(
    org_id: uuid.UUID,
    thread_id: uuid.UUID,
    message: str,
) -> str:
    """Full DB thread history with provider labels + new user turn."""
    from services.chat_prompt import compose_chat_user_message

    messages = await _load_chat_history_messages(org_id, thread_id)
    conversation_history = format_full_thread_history_for_handoff(messages)
    return compose_chat_user_message(conversation_history=conversation_history, user_text=message)


async def _persist_council_transcript_inner(
    org_id: uuid.UUID,
    thread_id: uuid.UUID,
    question: str,
    *,
    council_members: list[dict[str, Any]],
    synthesis: dict[str, Any] | None,
    total_cost_usd: float,
    synthesis_display_text: str,
    room_id: str | None = None,
    question_id: str | None = None,
    room_status: str | None = None,
) -> None:
    """Tier-1 transcript body (unbounded; caller must apply DB_OPERATION_TIMEOUT_S)."""
    async with get_db_session() as session:
        await _set_org(session, org_id)
        row = await session.get(Thread, thread_id)
        if row is None or row.org_id != org_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Thread not found")

        for m in council_members:
            for finding in validate_council_member(m):
                log_warning(
                    "council member validation finding",
                    subsystem="persistence_integrity",
                    operation="persist_council_transcript",
                    outcome="warning",
                    integrity_code=finding.code,
                )

        to_add: list[Message] = [
            Message(org_id=org_id, thread_id=thread_id, role="user", content=question),
        ]
        for i, m in enumerate(council_members):
            expert = str(m.get("expert") or "Advisor")
            resp = str(m.get("response") or "")
            is_last = i == len(council_members) - 1 and synthesis is None
            cost = total_cost_usd if is_last else 0.0
            idx = m.get("expert_index")
            expert_index = int(idx) if idx is not None else i
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
                        room_id=room_id,
                        question_id=question_id,
                        expert_index=expert_index,
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
                        room_id=room_id,
                        question_id=question_id,
                        room_status=room_status,
                    ),
                )
            )
        session.add_all(to_add)
        await session.commit()


async def persist_council_transcript(
    org_id: uuid.UUID,
    thread_id: uuid.UUID,
    question: str,
    *,
    council_members: list[dict[str, Any]],
    synthesis: dict[str, Any] | None,
    total_cost_usd: float,
    synthesis_display_text: str,
    room_id: str | None = None,
    question_id: str | None = None,
    room_status: str | None = None,
) -> None:
    """Append user question + council expert rows + optional synthesis (Tier-1 budget)."""
    try:
        await asyncio.wait_for(
            _persist_council_transcript_inner(
                org_id,
                thread_id,
                question,
                council_members=council_members,
                synthesis=synthesis,
                total_cost_usd=total_cost_usd,
                synthesis_display_text=synthesis_display_text,
                room_id=room_id,
                question_id=question_id,
                room_status=room_status,
            ),
            timeout=DB_OPERATION_TIMEOUT_S,
        )
    except (TimeoutError, asyncio.TimeoutError):
        await record_transcript_persist_timeout()
        raise


async def _append_adhoc_expert_message_inner(
    org_id: uuid.UUID,
    thread_id: uuid.UUID,
    *,
    session_id: str,
    provider_id: str,
    response: str,
    provider_used: str,
    model: str,
    outcome: str,
    cost_usd: float,
    sequence: int,
    display_content: str,
) -> uuid.UUID:
    async with get_db_session() as session:
        await _set_org(session, org_id)
        row = await session.get(Thread, thread_id)
        if row is None or row.org_id != org_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Thread not found")
        msg = Message(
            org_id=org_id,
            thread_id=thread_id,
            role="assistant",
            content=encode_adhoc_expert(
                session_id=session_id,
                provider_id=provider_id,
                response=response,
                provider_used=provider_used,
                model=model,
                outcome=outcome,
                cost_usd=cost_usd,
                sequence=sequence,
                display_content=display_content,
            ),
        )
        session.add(msg)
        await session.flush()
        message_id = msg.id
        await session.commit()
        return message_id


async def append_adhoc_expert_message(
    org_id: uuid.UUID,
    thread_id: uuid.UUID,
    *,
    session_id: str,
    provider_id: str,
    response: str,
    provider_used: str = "",
    model: str = "",
    outcome: str = "ok",
    cost_usd: float = 0.0,
    sequence: int = 1,
    display_content: str = "",
) -> uuid.UUID:
    """Append one ad-hoc expert assistant row (Tier-1 DB budget)."""
    try:
        return await asyncio.wait_for(
            _append_adhoc_expert_message_inner(
                org_id,
                thread_id,
                session_id=session_id,
                provider_id=provider_id,
                response=response,
                provider_used=provider_used,
                model=model,
                outcome=outcome,
                cost_usd=cost_usd,
                sequence=sequence,
                display_content=display_content,
            ),
            timeout=DB_OPERATION_TIMEOUT_S,
        )
    except (TimeoutError, asyncio.TimeoutError):
        await record_transcript_persist_timeout()
        raise


async def _append_adhoc_synthesis_message_inner(
    org_id: uuid.UUID,
    thread_id: uuid.UUID,
    *,
    session_id: str,
    synthesis: dict[str, Any],
    display_text: str,
    cost_usd: float,
) -> uuid.UUID:
    async with get_db_session() as session:
        await _set_org(session, org_id)
        lock_q = (
            select(Thread)
            .where(Thread.id == thread_id, Thread.org_id == org_id)
            .with_for_update()
        )
        row = (await session.execute(lock_q)).scalar_one_or_none()
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Thread not found")
        msg_q = (
            select(Message)
            .where(Message.thread_id == thread_id, Message.org_id == org_id)
            .order_by(Message.created_at.asc())
        )
        messages = list((await session.execute(msg_q)).scalars().all())
        msg = Message(
            org_id=org_id,
            thread_id=thread_id,
            role="assistant",
            content=encode_adhoc_synthesis(
                session_id=session_id,
                synthesis=synthesis,
                display_text=display_text,
                cost_usd=cost_usd,
            ),
        )
        session.add(msg)
        await session.flush()
        message_id = msg.id
        await session.commit()
        return message_id


async def append_adhoc_synthesis_message(
    org_id: uuid.UUID,
    thread_id: uuid.UUID,
    *,
    session_id: str,
    synthesis: dict[str, Any],
    display_text: str,
    cost_usd: float = 0.0,
) -> uuid.UUID:
    """Append ad-hoc BEN synthesis row (Tier-1 DB budget)."""
    try:
        return await asyncio.wait_for(
            _append_adhoc_synthesis_message_inner(
                org_id,
                thread_id,
                session_id=session_id,
                synthesis=synthesis,
                display_text=display_text,
                cost_usd=cost_usd,
            ),
            timeout=DB_OPERATION_TIMEOUT_S,
        )
    except (TimeoutError, asyncio.TimeoutError):
        await record_transcript_persist_timeout()
        raise
