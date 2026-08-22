"""Project management persistence (tenant-scoped via RLS)."""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import text

from database.connection import get_db_session
from database.models import PROJECT_STATUSES, Project
from services.ops.request_context import attach_request_id
from services.ops.structured_log import log_info
from services.project_library import (
    build_project_list_stmt,
    clamp_project_page_limit,
    decode_project_cursor,
    encode_project_cursor,
    fetch_file_counts,
    library_item,
    normalize_project_search_query,
)


async def _set_org(session, org_id: uuid.UUID) -> None:
    await session.execute(text("SELECT set_config('app.current_org_id', :v, true)"), {"v": str(org_id)})


def _project_payload(row: Project) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "org_id": str(row.org_id),
        "name": row.name,
        "description": row.description,
        "status": row.status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


async def create_project(
    org_id: uuid.UUID,
    *,
    name: str,
    description: str | None = None,
    status: str = "active",
) -> dict[str, Any]:
    title = (name or "").strip()
    if not title:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "name is required")
    status_norm = (status or "active").strip().lower()
    if status_norm not in PROJECT_STATUSES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"status must be one of: {', '.join(PROJECT_STATUSES)}")

    async with get_db_session() as session:
        await _set_org(session, org_id)
        row = Project(
            org_id=org_id,
            name=title[:512],
            description=(description or "").strip() or None,
            status=status_norm,
        )
        session.add(row)
        await session.flush()
        await session.commit()
        payload = _project_payload(row)
    return attach_request_id(payload)


async def list_projects(
    org_id: uuid.UUID,
    *,
    limit: int | None = None,
    cursor: str | None = None,
    query: str | None = None,
    include_file_counts: bool = True,
) -> dict[str, Any]:
    """Bounded org-scoped project page. Never returns an unbounded result set.

    Additive keys: ``items`` (library rows) and ``projects`` (same page, legacy
    alias). ``next_cursor`` is an opaque keyset token or null.
    Optional ``query`` filters by name contains (ILIKE) and/or exact UUID,
    still inside ``org_id``.
    """
    page_limit = clamp_project_page_limit(limit)
    needle = normalize_project_search_query(query)
    cursor_ts = cursor_id = None
    if cursor and str(cursor).strip():
        cursor_ts, cursor_id = decode_project_cursor(str(cursor))

    async with get_db_session() as session:
        await _set_org(session, org_id)
        stmt = build_project_list_stmt(
            org_id,
            limit=page_limit,
            cursor_ts=cursor_ts,
            cursor_id=cursor_id,
            query=needle,
        )
        fetched = list((await session.execute(stmt)).scalars().all())
        has_next = len(fetched) > page_limit
        page_rows = fetched[:page_limit]
        counts: dict[uuid.UUID, int] = {}
        if include_file_counts:
            counts = await fetch_file_counts(
                session,
                org_id=org_id,
                project_ids=[row.id for row in page_rows],
            )

    items = [
        library_item(row, file_count=counts.get(row.id, 0)) for row in page_rows
    ]
    next_cursor = None
    if has_next and page_rows:
        last = page_rows[-1]
        next_cursor = encode_project_cursor(updated_at=last.updated_at, project_id=last.id)

    log_info(
        "projects_list completed",
        subsystem="projects",
        operation="projects_list",
        outcome="ok",
        page_size=len(items),
        has_next_page=bool(next_cursor),
        limit=page_limit,
        search=bool(needle),
    )
    payload = {
        "items": items,
        "next_cursor": next_cursor,
        "limit": page_limit,
        # Additive alias for existing first-page consumers (picker / basalt).
        "projects": items,
    }
    return attach_request_id(payload)


async def get_project(org_id: uuid.UUID, project_id: uuid.UUID) -> dict[str, Any]:
    async with get_db_session() as session:
        await _set_org(session, org_id)
        row = await session.get(Project, project_id)
        if row is None or row.org_id != org_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
        payload = _project_payload(row)
    return attach_request_id(payload)
