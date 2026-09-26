"""Resource Access — bounded allow/deny for File Scope V2 protected use.

Authoritative security/lifecycle decision. Not RBAC, not a policy platform,
and not a retrieval or prompt assembler. Callers must not treat tenant
membership, uploaded_by, or source_chat_id as a grant.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import text

from database.connection import get_db_session
from database.models import Thread, WorkspaceFile


@dataclass(frozen=True)
class AccessDecision:
    allowed: bool
    reason: str
    http_status: int = status.HTTP_404_NOT_FOUND


def allow(reason: str = "ok") -> AccessDecision:
    return AccessDecision(allowed=True, reason=reason, http_status=200)


def deny(reason: str, http_status: int = status.HTTP_404_NOT_FOUND) -> AccessDecision:
    return AccessDecision(allowed=False, reason=reason, http_status=http_status)


def raise_if_denied(decision: AccessDecision, *, detail: str = "Not found") -> None:
    if not decision.allowed:
        raise HTTPException(status_code=decision.http_status, detail=detail)


def authoritative_owner(resource: Any) -> str | None:
    """Return a durable principal binding. Never infer from provenance."""
    if resource is None:
        return None
    for attr in ("created_by", "owner_id", "owner"):
        value = getattr(resource, attr, None)
        if value is None:
            continue
        text_value = str(value).strip()
        if text_value:
            return text_value
    return None


def authorize_project(org_id: uuid.UUID, project: Any, *, action: str) -> AccessDecision:
    """Private Project: tenant equality is necessary, never sufficient."""
    del action
    if project is None:
        return deny("missing_project")
    if getattr(project, "org_id", None) != org_id:
        return deny("wrong_tenant")
    if authoritative_owner(project) is None:
        return deny("unresolved_owner")
    return allow()


def authorize_file(org_id: uuid.UUID, file_row: Any, *, action: str) -> AccessDecision:
    """Current source permission. Missing/deleted rows fail closed."""
    del action
    if file_row is None:
        return deny("missing_file")
    if getattr(file_row, "org_id", None) != org_id:
        return deny("wrong_tenant")
    return allow()


def authorize_thread(
    org_id: uuid.UUID,
    thread: Any,
    *,
    expected_id: uuid.UUID,
    action: str,
) -> AccessDecision:
    """Destination/execution thread. source_chat_id is provenance, not a grant."""
    del action
    if thread is None:
        return deny("missing_thread")
    thread_id = getattr(thread, "id", None)
    if thread_id is None or thread_id != expected_id:
        return deny("wrong_resource")
    if getattr(thread, "org_id", None) != org_id:
        return deny("wrong_tenant")
    return allow()


async def _set_org(session: Any, org_id: uuid.UUID) -> None:
    await session.execute(
        text("SELECT set_config('app.current_org_id', :oid, true)"),
        {"oid": str(org_id)},
    )


async def load_file_row(org_id: uuid.UUID, file_id: uuid.UUID) -> Any:
    """Resolve a file through the workspace-files session (test-patched there)."""
    from services.workspace_files import service as workspace_service

    try:
        async with workspace_service.get_db_session() as session:
            await workspace_service._set_org(session, org_id)
            row = await session.get(WorkspaceFile, file_id)
            if row is None or getattr(row, "org_id", None) != org_id:
                return None
            return row
    except HTTPException:
        raise
    except Exception:
        return None


async def authorize_file_use(
    org_id: uuid.UUID,
    file_id: uuid.UUID | None,
    *,
    action: str,
) -> AccessDecision:
    if file_id is None:
        return deny("unknown_source_identity")
    row = await load_file_row(org_id, file_id)
    return authorize_file(org_id, row, action=action)


async def load_thread_row(org_id: uuid.UUID, thread_id: uuid.UUID) -> Any:
    try:
        async with get_db_session() as session:
            await _set_org(session, org_id)
            row = await session.get(Thread, thread_id)
            if row is None or getattr(row, "org_id", None) != org_id:
                return None
            if getattr(row, "id", None) != thread_id:
                return None
            return row
    except HTTPException:
        raise
    except Exception:
        return None


async def require_thread_access(
    org_id: uuid.UUID,
    thread_id: uuid.UUID,
    *,
    action: str,
) -> None:
    row = await load_thread_row(org_id, thread_id)
    raise_if_denied(authorize_thread(org_id, row, expected_id=thread_id, action=action))


def authorize_destination_on_session(
    org_id: uuid.UUID,
    thread: Any,
    *,
    expected_id: uuid.UUID,
    action: str,
) -> AccessDecision:
    return authorize_thread(org_id, thread, expected_id=expected_id, action=action)
