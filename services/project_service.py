"""Project management persistence (tenant-scoped via RLS)."""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select, text

from database.connection import get_db_session
from database.models import PROJECT_STATUSES, Project
from services.ops.request_context import attach_request_id


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


async def list_projects(org_id: uuid.UUID) -> dict[str, Any]:
    async with get_db_session() as session:
        await _set_org(session, org_id)
        q = select(Project).where(Project.org_id == org_id).order_by(Project.updated_at.desc())
        rows = (await session.execute(q)).scalars().all()
        payload = {"projects": [_project_payload(r) for r in rows]}
    return attach_request_id(payload)


async def get_project(org_id: uuid.UUID, project_id: uuid.UUID) -> dict[str, Any]:
    async with get_db_session() as session:
        await _set_org(session, org_id)
        row = await session.get(Project, project_id)
        if row is None or row.org_id != org_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
        payload = _project_payload(row)
    return attach_request_id(payload)
