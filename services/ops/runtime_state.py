"""Normalized runtime lifecycle states (recovery & idempotency v1)."""
from __future__ import annotations

from typing import Any

COUNCIL_PENDING = "council_pending"
COUNCIL_RUNNING = "council_running"
COUNCIL_COMPLETED = "council_completed"
COUNCIL_DEGRADED = "council_degraded"
COUNCIL_FAILED = "council_failed"

CHAT_COMPLETED = "chat_completed"
CHAT_FAILED = "chat_failed"

PERSISTENCE_PENDING = "persistence_pending"
PERSISTENCE_COMPLETED = "persistence_completed"
PERSISTENCE_FAILED = "persistence_failed"


def derive_council_runtime_state(
    council_members: list[dict[str, Any]],
    synthesis: dict[str, Any] | None,
) -> str:
    if not council_members:
        return COUNCIL_FAILED
    outcomes = [str(m.get("outcome") or "ok") for m in council_members]
    ok_count = sum(1 for o in outcomes if o == "ok")
    if ok_count == 0 and synthesis is None:
        return COUNCIL_FAILED
    if synthesis is None:
        return COUNCIL_DEGRADED
    if any(o != "ok" for o in outcomes):
        return COUNCIL_DEGRADED
    return COUNCIL_COMPLETED


def derive_persistence_state(*, transcript_persisted: bool, persist_scheduled: bool) -> str:
    if transcript_persisted:
        return PERSISTENCE_COMPLETED
    if persist_scheduled:
        return PERSISTENCE_PENDING
    return PERSISTENCE_PENDING


async def finalize_council_payload(
    payload: dict[str, Any],
    *,
    client_request_id: str | None,
    idempotent_replay: bool = False,
) -> dict[str, Any]:
    from services.ops.idempotency import get_idempotency_registry, get_idempotency_store_key

    members = payload.get("council") or []
    syn = payload.get("synthesis") if isinstance(payload.get("synthesis"), dict) else None
    rs = derive_council_runtime_state(members, syn)
    key = get_idempotency_store_key()
    persisted = await get_idempotency_registry().transcript_persisted(key)
    ps = derive_persistence_state(transcript_persisted=persisted, persist_scheduled=not persisted)
    return attach_runtime_fields(
        payload,
        runtime_state=rs,
        persistence_state=ps,
        client_request_id=client_request_id,
        idempotent_replay=idempotent_replay,
    )


async def finalize_chat_payload(
    payload: dict[str, Any],
    *,
    client_request_id: str | None,
    idempotent_replay: bool = False,
) -> dict[str, Any]:
    rs = CHAT_COMPLETED if payload.get("response") is not None else CHAT_FAILED
    return attach_runtime_fields(
        payload,
        runtime_state=rs,
        persistence_state=PERSISTENCE_COMPLETED,
        client_request_id=client_request_id,
        idempotent_replay=idempotent_replay,
    )


def attach_runtime_fields(
    payload: dict[str, Any],
    *,
    runtime_state: str,
    persistence_state: str | None = None,
    client_request_id: str | None = None,
    idempotent_replay: bool = False,
) -> dict[str, Any]:
    out = dict(payload)
    out["runtime_state"] = runtime_state
    if persistence_state is not None:
        out["persistence_state"] = persistence_state
    if client_request_id:
        out["client_request_id"] = client_request_id
    out["idempotent_replay"] = bool(idempotent_replay)
    return out
