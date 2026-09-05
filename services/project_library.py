"""Org-scoped project library listing: keyset pagination + bounded summaries.

Thread counts are omitted: threads are not joinable to projects in Postgres
without an N+1 or unbounded scan. File counts are aggregated in one query
over the current page's IDs only.
"""
from __future__ import annotations

import base64
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Project, WorkspaceFile

# Env-configurable, but never unlimited: code ceiling wins over a huge env value.
_ABSOLUTE_MAX_PAGE = 200
_DEFAULT_PAGE = 50
_DEFAULT_MAX = 100
_SEARCH_QUERY_MAX = 128


def _env_int(name: str, fallback: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return fallback
    try:
        return int(raw)
    except ValueError:
        return fallback


def projects_page_bounds() -> tuple[int, int]:
    """Return (default_page_size, hard_max) with a code-level ceiling."""
    maximum = min(_ABSOLUTE_MAX_PAGE, max(1, _env_int("BEN_PROJECTS_PAGE_MAX", _DEFAULT_MAX)))
    default = min(maximum, max(1, _env_int("BEN_PROJECTS_PAGE_SIZE", _DEFAULT_PAGE)))
    return default, maximum


def clamp_project_page_limit(raw: int | None) -> int:
    default, maximum = projects_page_bounds()
    if raw is None:
        return default
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return default
    return max(1, min(n, maximum))


def normalize_project_search_query(raw: str | None) -> str | None:
    """Trim and bound the search string. Empty → browse (no search filter)."""
    text = str(raw or "").strip()
    if not text:
        return None
    return text[:_SEARCH_QUERY_MAX]


def parse_project_uuid_query(query: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(query).strip())
    except (ValueError, AttributeError, TypeError):
        return None


def escape_project_like(query: str) -> str:
    return (
        str(query)
        .replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )


def encode_project_cursor(*, updated_at: datetime, project_id: uuid.UUID) -> str:
    ts = updated_at
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    else:
        ts = ts.astimezone(timezone.utc)
    raw = json.dumps(
        {"u": ts.isoformat(), "i": str(project_id)},
        separators=(",", ":"),
    )
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def decode_project_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    token = (cursor or "").strip()
    if not token:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid cursor")
    pad = "=" * ((4 - len(token) % 4) % 4)
    try:
        raw = base64.urlsafe_b64decode(token + pad)
        data = json.loads(raw.decode("utf-8"))
        pid = uuid.UUID(str(data["i"]))
        ts = datetime.fromisoformat(str(data["u"]))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        else:
            ts = ts.astimezone(timezone.utc)
        return ts, pid
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid cursor") from exc


def keyset_after(
    rows: list[tuple[datetime, uuid.UUID, Any]],
    *,
    cursor_ts: datetime | None,
    cursor_id: uuid.UUID | None,
    limit: int,
) -> tuple[list[Any], bool]:
    """Deterministic DESC(updated_at), DESC(id) page over already-sorted rows.

    Used by tests to prove no duplicates/missing rows across pages. SQL uses
    the same predicate.
    """
    selected: list[Any] = []
    started = cursor_ts is None
    for ts, pid, payload in rows:
        if not started:
            if ts < cursor_ts or (ts == cursor_ts and pid < cursor_id):
                started = True
            else:
                continue
        if started:
            selected.append(payload)
            if len(selected) > limit:
                return selected[:limit], True
    return selected, False


def build_project_list_stmt(
    org_id: uuid.UUID,
    *,
    limit: int,
    cursor_ts: datetime | None = None,
    cursor_id: uuid.UUID | None = None,
    query: str | None = None,
) -> Select:
    """Tenant-scoped keyset page. Optional name/UUID search never drops org_id."""
    stmt = select(Project).where(Project.org_id == org_id)
    needle = normalize_project_search_query(query)
    if needle:
        pattern = f"%{escape_project_like(needle)}%"
        name_match = Project.name.ilike(pattern, escape="\\")
        exact_id = parse_project_uuid_query(needle)
        if exact_id is not None:
            stmt = stmt.where(or_(name_match, Project.id == exact_id))
        else:
            stmt = stmt.where(name_match)
    stmt = stmt.order_by(Project.updated_at.desc(), Project.id.desc()).limit(int(limit) + 1)
    if cursor_ts is not None and cursor_id is not None:
        stmt = stmt.where(
            or_(
                Project.updated_at < cursor_ts,
                and_(Project.updated_at == cursor_ts, Project.id < cursor_id),
            )
        )
    return stmt


def build_file_count_stmt(org_id: uuid.UUID, project_ids: list[uuid.UUID]) -> Select:
    return (
        select(WorkspaceFile.workspace_id, func.count())
        .where(
            WorkspaceFile.org_id == org_id,
            WorkspaceFile.workspace_id.in_(project_ids),
        )
        .group_by(WorkspaceFile.workspace_id)
    )


def library_item(row: Project, *, file_count: int = 0) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "name": row.name,
        "status": row.status,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "file_count": int(file_count or 0),
    }


async def fetch_file_counts(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_ids: list[uuid.UUID],
) -> dict[uuid.UUID, int]:
    if not project_ids:
        return {}
    result = await session.execute(build_file_count_stmt(org_id, project_ids))
    return {wid: int(n) for wid, n in result.all() if wid is not None}
