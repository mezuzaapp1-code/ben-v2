"""Load thread messages and build DB-authoritative transcript text for ad-hoc council."""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select, text

from database.connection import get_db_session
from database.models import Message
from services.message_format import (
    decode_message,
    gateway_to_provider_id,
    provider_display_label,
)
from services.thread_service import get_thread_for_org

_BEN_PREFIX = '{"ben":'
MAX_TRANSCRIPT_MESSAGES = 40
MAX_TRANSCRIPT_CHARS = 48_000
BACKGROUND_MAX_MESSAGES = 12
BACKGROUND_MAX_CHARS = 8_000


@dataclass(frozen=True)
class AdhocExpertClaim:
    provider_id: str
    model: str
    response: str
    outcome: str
    sequence: int
    cost_usd: float
    provider_used: str = ""


@dataclass(frozen=True)
class AdhocSessionSnapshot:
    session_id: str
    experts: tuple[AdhocExpertClaim, ...]
    anchor_user_text: str
    background_tail: str
    voice_keys: frozenset[str]
    closed: bool = False


@dataclass
class AdhocThreadIndex:
    """In-memory index built once per request from ordered thread messages."""

    messages: list[Message]
    window_start: int = 0
    closed_sessions: frozenset[str] = frozenset()

    def session_closed(self, session_id: str) -> bool:
        """Sessions stay open for multi-round expert + synthesis loops."""
        return False

    def experts_for(self, session_id: str) -> tuple[AdhocExpertClaim, ...]:
        session_key = str(session_id or "").strip().lower()
        experts: list[AdhocExpertClaim] = []
        start = _last_adhoc_synthesis_index(self.messages, session_key) + 1
        for m in self.messages[start:]:
            if m.role != "assistant" or _envelope_kind(m.role, m.content) != "adhoc_expert":
                continue
            if _envelope_session_id(m.role, m.content).lower() != session_key:
                continue
            try:
                data = json.loads(m.content)
            except json.JSONDecodeError:
                continue
            if not isinstance(data, dict) or data.get("ben") != 1:
                continue
            experts.append(
                AdhocExpertClaim(
                    provider_id=str(data.get("provider_id") or ""),
                    model=str(data.get("model") or ""),
                    response=str(data.get("response") or ""),
                    outcome=str(data.get("outcome") or "ok"),
                    sequence=int(data.get("sequence") or len(experts) + 1),
                    cost_usd=float(data.get("cost_usd") or 0),
                    provider_used=str(data.get("provider_used") or ""),
                )
            )
        experts.sort(key=lambda e: e.sequence)
        return tuple(experts)

    def voice_keys_for(self, session_id: str) -> frozenset[str]:
        session_key = str(session_id or "").strip().lower()
        voices: set[str] = set()
        start = _last_adhoc_synthesis_index(self.messages, session_key) + 1
        for m in self.messages[start:]:
            if m.role != "assistant":
                continue
            kind = _envelope_kind(m.role, m.content)
            if kind == "adhoc_expert":
                if _envelope_session_id(m.role, m.content).lower() != session_key:
                    continue
            elif kind in ("adhoc_synthesis", "council_synthesis"):
                continue
            decoded = decode_message(m.role, m.content)
            if kind == "chat" or (kind is None and _provider_id_from_envelope(m.role, m.content)):
                decoded = {**decoded, "kind": "chat"}
            key = _voice_key_from_decoded(decoded, role=m.role, content=m.content)
            if key:
                voices.add(key)
        return frozenset(voices)


async def _set_org(session, org_id: uuid.UUID) -> None:
    await session.execute(text("SELECT set_config('app.current_org_id', :v, true)"), {"v": str(org_id)})


async def load_thread_messages(org_id: uuid.UUID, thread_id: uuid.UUID) -> list[Message]:
    """Ordered Message rows for a tenant-owned thread; 404 if missing or cross-tenant."""
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


async def load_thread_index(org_id: uuid.UUID, thread_id: uuid.UUID) -> AdhocThreadIndex:
    messages = await load_thread_messages(org_id, thread_id)
    return build_adhoc_thread_index(messages)


def _last_adhoc_synthesis_index(messages: list[Message], session_id: str) -> int:
    """Index of the latest ad-hoc synthesis for session_id, or -1 if none."""
    session_key = str(session_id or "").strip().lower()
    last = -1
    for i, m in enumerate(messages):
        if _envelope_kind(m.role, m.content) != "adhoc_synthesis":
            continue
        if _envelope_session_id(m.role, m.content).lower() == session_key:
            last = i
    return last


def build_adhoc_thread_index(messages: list[Message]) -> AdhocThreadIndex:
    return AdhocThreadIndex(messages=list(messages))


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


def _envelope_session_id(role: str, content: str) -> str:
    if role != "assistant" or not content.startswith(_BEN_PREFIX):
        return ""
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return ""
    if isinstance(data, dict) and data.get("ben") == 1:
        return str(data.get("session_id") or "").strip()
    return ""


def _provider_id_from_envelope(role: str, content: str) -> str:
    """Resolve speaking provider_id from a BEN JSON envelope (chat or adhoc)."""
    if role != "assistant" or not content.startswith(_BEN_PREFIX):
        return ""
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return ""
    if not isinstance(data, dict) or data.get("ben") != 1:
        return ""
    pid = str(data.get("provider_id") or "").strip().lower()
    if pid:
        return pid
    pu = str(data.get("provider_used") or "").strip().lower()
    return gateway_to_provider_id(pu) if pu else ""


def _format_chat_assistant_transcript_line(role: str, content: str, decoded: dict[str, Any]) -> str | None:
    """Provider-first chat line for ad-hoc transcript (not generic Assistant)."""
    text = (decoded.get("content") or "").strip()
    if not text and content.startswith(_BEN_PREFIX):
        try:
            data = json.loads(content)
            if isinstance(data, dict):
                text = str(data.get("text") or data.get("response") or "").strip()
        except json.JSONDecodeError:
            pass
    if not text:
        return None
    pid = str(decoded.get("provider_id") or "").strip().lower() or _provider_id_from_envelope(role, content)
    label = provider_display_label(pid) if pid else "Assistant"
    return f"{label} (Chat Assistant): {text}"


def latest_user_question(messages: list[Message]) -> str:
    """Most recent user message in the thread (target query for ad-hoc expert)."""
    return _anchor_user_text(messages)


def _line_for_message(
    role: str,
    content: str,
    *,
    session_synthesis_round: dict[str, int] | None = None,
) -> str | None:
    if role == "user":
        text = (content or "").strip()
        return f"User: {text}" if text else None

    decoded = decode_message(role, content)
    kind = decoded.get("kind") or _envelope_kind(role, content)

    if kind == "council_synthesis":
        body = (decoded.get("content") or "").strip()
        return f"🧠 BEN Council Synthesis: {body}" if body else "🧠 BEN Council Synthesis"
    if kind == "adhoc_synthesis":
        body = (decoded.get("content") or "").strip()
        sid = _envelope_session_id(role, content).lower() or "_"
        if session_synthesis_round is not None:
            session_synthesis_round[sid] = session_synthesis_round.get(sid, 0) + 1
            round_n = session_synthesis_round[sid]
        else:
            round_n = 1
        label = f"BEN (Synthesis Summary Round {round_n})"
        return f"{label}: {body}" if body else label
    if kind == "chat":
        return _format_chat_assistant_transcript_line(role, content, decoded)
    if kind == "adhoc_expert":
        body = (decoded.get("content") or "").strip()
        return body if body else None
    if kind == "council_expert":
        body = (decoded.get("content") or "").strip()
        return body if body else None
    if role == "assistant":
        pid = _provider_id_from_envelope(role, content)
        if pid:
            return _format_chat_assistant_transcript_line(role, content, decoded)
        body = (decoded.get("content") or content or "").strip()
        if body:
            return f"Assistant (Chat Assistant): {body}"
        return None

    body = (decoded.get("content") or content or "").strip()
    return body if body else None


def build_transcript_lines(
    messages: list[Message],
    *,
    max_messages: int = MAX_TRANSCRIPT_MESSAGES,
    max_chars: int = MAX_TRANSCRIPT_CHARS,
) -> str:
    """Deterministic transcript block; truncates oldest lines first."""
    lines: list[str] = []
    session_synthesis_round: dict[str, int] = {}
    for m in messages:
        line = _line_for_message(
            m.role, m.content, session_synthesis_round=session_synthesis_round
        )
        if line:
            lines.append(line)

    if len(lines) > max_messages:
        lines = lines[-max_messages:]

    while lines and sum(len(s) + 1 for s in lines) > max_chars:
        lines.pop(0)

    return "\n\n".join(lines)


def _voice_key_from_decoded(decoded: dict[str, Any], *, role: str, content: str) -> str | None:
    kind = decoded.get("kind") or _envelope_kind(role, content)
    provider_id = str(decoded.get("provider_id") or "").strip().lower()
    if not provider_id:
        provider_id = _provider_id_from_envelope(role, content)
    if provider_id:
        return provider_id
    provider_used = str(decoded.get("provider_used") or "").strip().lower()
    if provider_used:
        mapped = gateway_to_provider_id(provider_used)
        if mapped:
            return mapped
    if kind == "chat":
        model = str(decoded.get("model_used") or "").strip()
        if model:
            return f"chat:{model}"
    if kind == "council_expert":
        model = str(decoded.get("model_used") or "").strip()
        return f"council:{model}" if model else "council:expert"
    model = str(decoded.get("model_used") or "").strip()
    if model and model != "synthesis":
        return f"model:{model}"
    return None


def _anchor_user_text(messages: list[Message]) -> str:
    for m in reversed(messages):
        if m.role == "user":
            text = (m.content or "").strip()
            if text:
                return text
    return ""


def _background_messages_for_session(
    index: AdhocThreadIndex,
    session_id: str,
) -> list[Message]:
    """Context before the current synthesis round (prior syntheses, chat, earlier experts)."""
    session_key = str(session_id or "").strip().lower()
    round_start = _last_adhoc_synthesis_index(index.messages, session_key) + 1
    if round_start > 0:
        return list(index.messages[:round_start])
    first_session_adhoc: int | None = None
    for i, m in enumerate(index.messages):
        if _envelope_kind(m.role, m.content) != "adhoc_expert":
            continue
        if _envelope_session_id(m.role, m.content).lower() == session_key:
            first_session_adhoc = i
            break
    if first_session_adhoc is not None and first_session_adhoc > 0:
        return list(index.messages[:first_session_adhoc])
    return []


def build_adhoc_session_snapshot(index: AdhocThreadIndex, session_id: str) -> AdhocSessionSnapshot:
    session_key = str(session_id or "").strip().lower()
    experts = index.experts_for(session_id)
    round_start = _last_adhoc_synthesis_index(index.messages, session_key) + 1
    window = index.messages[round_start:]
    background_msgs = _background_messages_for_session(index, session_id)
    background_tail = build_transcript_lines(
        background_msgs,
        max_messages=BACKGROUND_MAX_MESSAGES,
        max_chars=BACKGROUND_MAX_CHARS,
    )
    return AdhocSessionSnapshot(
        session_id=session_key,
        experts=experts,
        anchor_user_text=_anchor_user_text(window),
        background_tail=background_tail,
        voice_keys=index.voice_keys_for(session_id),
        closed=False,
    )


def build_adhoc_session_snapshot_from_messages(
    messages: list[Message],
    session_id: str,
) -> AdhocSessionSnapshot:
    return build_adhoc_session_snapshot(build_adhoc_thread_index(messages), session_id)


def count_ai_voices_in_session(messages: list[Message], session_id: str) -> int:
    return len(build_adhoc_thread_index(messages).voice_keys_for(session_id))


def session_has_adhoc_synthesis(messages: list[Message], session_id: str) -> bool:
    session_key = str(session_id or "").strip().lower()
    for m in messages:
        if _envelope_kind(m.role, m.content) != "adhoc_synthesis":
            continue
        if _envelope_session_id(m.role, m.content).lower() == session_key:
            return True
    return False


def collect_session_experts(messages: list[Message], session_id: str) -> list[dict[str, Any]]:
    claims = build_adhoc_thread_index(messages).experts_for(session_id)
    return [
        {
            "provider_id": c.provider_id,
            "provider_used": c.provider_used,
            "model": c.model,
            "response": c.response,
            "outcome": c.outcome,
            "sequence": c.sequence,
            "cost_usd": c.cost_usd,
        }
        for c in claims
    ]


def format_expert_lines_for_synthesis(experts: list[dict[str, Any]] | tuple[AdhocExpertClaim, ...]) -> str:
    lines: list[str] = []
    for e in experts:
        if isinstance(e, AdhocExpertClaim):
            pid, model, outcome, resp = e.provider_id, e.model, e.outcome, e.response
        else:
            pid = str(e.get("provider_id") or "")
            model = str(e.get("model") or "unknown")
            outcome = str(e.get("outcome") or "ok")
            resp = str(e.get("response") or "")
        label = provider_display_label(pid) or pid or "Model"
        lines.append(f"- {label} ({model}, outcome={outcome}): {resp}")
    return "\n".join(lines)
