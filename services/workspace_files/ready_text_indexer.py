"""Gate 2 — index already-extracted READY text for one explicit workspace.

Reuses Gate 4A chunk rows + ``simple`` FTS. Does not parse bytes, does not
claim jobs, does not call drain/runner, and cannot iterate all workspaces.

Fail-closed: ``BEN_WORKSPACE_READY_TEXT_INDEX_WORKSPACE_IDS`` must list the
target workspace. Protected historical/queued file IDs are never written.
"""
from __future__ import annotations

import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, func, select, text

from database.connection import get_db_session
from database.models import Project, WorkspaceFile, WorkspaceFileChunk
from services.ops.structured_log import log_info, log_warning
from services.workspace_files.chunking import CHUNKING_VERSION, chunk_extracted_text
from services.workspace_files.document_parser import EXTRACTION_VERSION
from services.workspace_files.extract import MAX_EXTRACT_CHARS
from services.workspace_files.extraction_pipeline import INDEXING_VERSION

_SUBSYSTEM = "workspace_files"
_OPERATION = "ready_text_index"

PROTECTED_FILE_IDS = frozenset(
    {
        uuid.UUID("43cef794-1fff-40ae-bd3c-47d9fc121518"),
        uuid.UUID("0bbd0dd0-cfd9-4ef4-a3b9-c1e96bef83a4"),
    }
)

DEFAULT_INDEX_LIMIT = 8
MAX_INDEX_LIMIT = 20
READY_STATUS = "ready"


def parse_index_workspace_allowlist(raw: str | None = None) -> set[str]:
    value = raw if raw is not None else os.getenv("BEN_WORKSPACE_READY_TEXT_INDEX_WORKSPACE_IDS")
    return {part.strip().lower() for part in (value or "").split(",") if part.strip()}


def ready_text_index_allowed(workspace_id: Any) -> bool:
    """Fail-closed. Empty allowlist indexes nothing (never a global backfill)."""
    allowed = parse_index_workspace_allowlist()
    if not allowed:
        return False
    return str(workspace_id).lower() in allowed


def clamp_index_limit(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_INDEX_LIMIT
    try:
        value = int(limit)
    except (TypeError, ValueError):
        return DEFAULT_INDEX_LIMIT
    return max(1, min(value, MAX_INDEX_LIMIT))


def _is_protected(file_id: Any) -> bool:
    try:
        return uuid.UUID(str(file_id)) in PROTECTED_FILE_IDS
    except (TypeError, ValueError, AttributeError):
        return False


async def _set_org(session, org_id: uuid.UUID) -> None:
    await session.execute(
        text("SELECT set_config('app.current_org_id', :oid, true)"),
        {"oid": str(org_id)},
    )


def _clip_extracted(text: str) -> str:
    body = (text or "").replace("\x00", " ").strip()
    if len(body) > MAX_EXTRACT_CHARS:
        return body[:MAX_EXTRACT_CHARS]
    return body


async def index_ready_extracted_text(
    *,
    org_id: uuid.UUID,
    workspace_id: uuid.UUID,
    limit: int | None = None,
) -> dict[str, Any]:
    """Chunk/index READY files with extracted_text in one workspace.

    Explicit org + workspace required. Bounded. Idempotent. Never writes
    queued/processing/failed rows or protected file IDs.
    """
    bounded = clamp_index_limit(limit)
    summary: dict[str, Any] = {
        "ok": False,
        "org_id": str(org_id),
        "workspace_id": str(workspace_id),
        "limit": bounded,
        "considered": 0,
        "indexed": 0,
        "already_indexed": 0,
        "skipped": 0,
        "protected_skipped": 0,
        "files": [],
    }

    if not ready_text_index_allowed(workspace_id):
        summary["error"] = "workspace_not_allowlisted"
        log_warning(
            "ready-text index refused: workspace not allowlisted",
            subsystem=_SUBSYSTEM,
            operation=_OPERATION,
            outcome="error",
            workspace_id=str(workspace_id),
        )
        return summary

    t_start = time.perf_counter()
    async with get_db_session() as session:
        await _set_org(session, org_id)
        workspace = await session.get(Project, workspace_id)
        if workspace is None or workspace.org_id != org_id:
            summary["error"] = "workspace_not_found"
            return summary

        protected_in_workspace = await session.execute(
            select(func.count())
            .select_from(WorkspaceFile)
            .where(
                WorkspaceFile.org_id == org_id,
                WorkspaceFile.workspace_id == workspace_id,
                WorkspaceFile.id.in_(tuple(PROTECTED_FILE_IDS)),
            )
        )
        summary["protected_in_workspace"] = int(protected_in_workspace.scalar_one() or 0)

        stmt = (
            select(WorkspaceFile)
            .where(
                WorkspaceFile.org_id == org_id,
                WorkspaceFile.workspace_id == workspace_id,
                WorkspaceFile.status == READY_STATUS,
                WorkspaceFile.id.notin_(tuple(PROTECTED_FILE_IDS)),
                WorkspaceFile.index_status != "indexed",
                WorkspaceFile.extracted_text.is_not(None),
                func.length(func.trim(WorkspaceFile.extracted_text)) > 0,
            )
            .order_by(WorkspaceFile.created_at.asc(), WorkspaceFile.id.asc())
            .limit(bounded)
        )
        rows = (await session.execute(stmt)).scalars().all()
        file_ids = [row.id for row in rows]

    for file_id in file_ids:
        summary["considered"] += 1
        outcome = await _index_one_ready_file(
            org_id=org_id, workspace_id=workspace_id, file_id=file_id
        )
        summary["files"].append(outcome)
        reason = outcome.get("reason")
        if outcome.get("outcome") == "indexed":
            summary["indexed"] += 1
        elif reason == "already_indexed":
            summary["already_indexed"] += 1
            summary["skipped"] += 1
        elif reason == "protected":
            summary["protected_skipped"] += 1
            summary["skipped"] += 1
        else:
            summary["skipped"] += 1

    summary["ok"] = True
    summary["total_ms"] = round((time.perf_counter() - t_start) * 1000.0, 1)
    log_info(
        "ready-text index complete",
        subsystem=_SUBSYSTEM,
        operation=_OPERATION,
        outcome="ok",
        workspace_id=str(workspace_id),
        considered=summary["considered"],
        indexed=summary["indexed"],
        skipped=summary["skipped"],
        protected_skipped=summary["protected_skipped"],
        already_indexed=summary["already_indexed"],
        total_ms=summary["total_ms"],
    )
    return summary


async def _index_one_ready_file(
    *,
    org_id: uuid.UUID,
    workspace_id: uuid.UUID,
    file_id: uuid.UUID,
) -> dict[str, Any]:
    base = {"id": str(file_id), "outcome": "skipped", "chunk_count": 0, "reason": "unknown"}
    if _is_protected(file_id):
        base["reason"] = "protected"
        return base

    async with get_db_session() as session:
        await _set_org(session, org_id)
        stmt = (
            select(WorkspaceFile)
            .where(
                WorkspaceFile.id == file_id,
                WorkspaceFile.org_id == org_id,
                WorkspaceFile.workspace_id == workspace_id,
            )
            .with_for_update()
        )
        row = (await session.execute(stmt)).scalar_one_or_none()
        if row is None:
            base["reason"] = "file_not_found"
            return base
        if _is_protected(row.id):
            base["reason"] = "protected"
            return base
        if row.org_id != org_id or row.workspace_id != workspace_id:
            base["reason"] = "isolation"
            return base
        if row.status != READY_STATUS:
            base["reason"] = f"status_{row.status}"
            return base
        extracted = _clip_extracted(row.extracted_text or "")
        if not extracted:
            base["reason"] = "no_extracted_text"
            return base

        existing = await session.execute(
            select(func.count())
            .select_from(WorkspaceFileChunk)
            .where(
                WorkspaceFileChunk.org_id == org_id,
                WorkspaceFileChunk.workspace_id == workspace_id,
                WorkspaceFileChunk.file_id == file_id,
                WorkspaceFileChunk.chunking_version == CHUNKING_VERSION,
            )
        )
        existing_count = int(existing.scalar_one() or 0)
        if row.index_status == "indexed" and existing_count > 0:
            base["outcome"] = "skipped"
            base["reason"] = "already_indexed"
            base["chunk_count"] = existing_count
            return base

        chunks = chunk_extracted_text(extracted)
        if not chunks:
            base["reason"] = "no_chunks"
            return base

        await session.execute(
            delete(WorkspaceFileChunk).where(
                WorkspaceFileChunk.file_id == file_id,
                WorkspaceFileChunk.chunking_version == CHUNKING_VERSION,
            )
        )
        for ch in chunks:
            session.add(
                WorkspaceFileChunk(
                    org_id=org_id,
                    workspace_id=workspace_id,
                    file_id=file_id,
                    page_id=None,
                    page_number=ch.page_number,
                    page_chunk_index=ch.page_chunk_index,
                    document_chunk_index=ch.document_chunk_index,
                    text=ch.text,
                    char_count=ch.char_count,
                    extraction_version=row.extraction_version or EXTRACTION_VERSION,
                    chunking_version=CHUNKING_VERSION,
                )
            )
        row.index_status = "indexed"
        row.chunking_version = CHUNKING_VERSION
        row.indexing_version = INDEXING_VERSION
        row.indexed_chunk_count = len(chunks)
        row.indexed_at = datetime.now(timezone.utc)
        row.processing_error = None
        await session.commit()

    return {
        "id": str(file_id),
        "outcome": "indexed",
        "chunk_count": len(chunks),
        "reason": None,
    }
