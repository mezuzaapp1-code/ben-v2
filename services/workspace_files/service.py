"""Workspace File Library V1 — upload, process, list, search, download, delete."""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import desc, or_, select, text

from database.connection import get_db_session
from database.models import Project, WorkspaceFile
from services.ops.request_context import attach_request_id
from services.workspace_files import storage
from services.workspace_files.extract import extract_text
from services.workspace_files.job_queue import (
    JOB_TYPE_FILE_EXTRACTION,
    enqueue_document_processing_job,
)
from services.workspace_files.types import (
    MAX_UPLOAD_BYTES,
    REJECTED_EXTENSIONS,
    SUPPORTED_TYPES,
)


def _doc_processing_enabled() -> bool:
    """Gate 3B activation flag (fail-safe OFF).

    OFF (default): preserve the existing synchronous upload path
    (persist -> await process_file -> READY), i.e. exactly current production.
    ON: use the Gate 3B durable path (persist + enqueue -> queued -> drain -> READY).
    """
    return os.getenv("BEN_DOC_PROCESSING_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _payload(row: WorkspaceFile) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "organization_id": str(row.org_id),
        "workspace_id": str(row.workspace_id),
        "project_id": str(row.project_id) if row.project_id else str(row.workspace_id),
        "original_filename": row.original_filename,
        "display_name": row.display_name,
        "media_type": row.media_type,
        "byte_size": row.byte_size,
        "checksum": row.checksum,
        "status": row.status,
        "uploaded_by": row.uploaded_by,
        "source_chat_id": row.source_chat_id,
        "failure_code": row.failure_code,
        "failure_message": row.failure_message,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
        "has_extracted_text": bool((row.extracted_text or "").strip()),
        "preview_kind": _preview_kind(row.media_type, row.original_filename),
    }


def _preview_kind(media_type: str, filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if media_type.startswith("image/") or suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
        return "image"
    if media_type == "application/pdf" or suffix == ".pdf":
        return "pdf"
    if media_type.startswith("text/") or suffix in {".txt", ".md", ".markdown", ".csv", ".json"}:
        return "text"
    return "download"


async def _set_org(session, org_id: uuid.UUID) -> None:
    await session.execute(
        text("SELECT set_config('app.current_org_id', :oid, true)"),
        {"oid": str(org_id)},
    )


async def _require_workspace(org_id: uuid.UUID, workspace_id: uuid.UUID) -> Project:
    async with get_db_session() as session:
        await _set_org(session, org_id)
        row = await session.get(Project, workspace_id)
        if row is None or row.org_id != org_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Workspace not found")
        return row


def _validate_upload_name(filename: str | None) -> tuple[str, str, bool]:
    safe = storage.sanitize_filename(filename)
    suffix = Path(safe).suffix.lower()
    if suffix in REJECTED_EXTENSIONS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"File type not allowed for security reasons: {suffix or 'unknown'}",
        )
    if suffix not in SUPPORTED_TYPES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Unsupported file type: {suffix or 'unknown'}. "
            "Supported: PDF, DOCX, TXT, Markdown, CSV, XLSX, PPTX, common images.",
        )
    media_type, processable = SUPPORTED_TYPES[suffix]
    return safe, media_type, processable


async def upload_file(
    *,
    org_id: uuid.UUID,
    workspace_id: uuid.UUID,
    upload: UploadFile,
    uploaded_by: str | None = None,
    source_chat_id: str | None = None,
) -> dict[str, Any]:
    await _require_workspace(org_id, workspace_id)
    safe_name, media_type, processable = _validate_upload_name(upload.filename)
    # Prefer client content-type when compatible; else mapped type.
    client_type = (upload.content_type or "").split(";")[0].strip().lower()
    if client_type and client_type != "application/octet-stream":
        # Keep mapped type as source of truth for process routing.
        pass

    file_id = uuid.uuid4()
    try:
        storage_key, byte_size, checksum = await storage.write_upload(
            org_id=org_id,
            workspace_id=workspace_id,
            file_id=file_id,
            filename=safe_name,
            upload=upload,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    # Gate 3B activation flag (fail-safe OFF -> synchronous, current production).
    async_enabled = _doc_processing_enabled()

    async with get_db_session() as session:
        await _set_org(session, org_id)
        row = WorkspaceFile(
            id=file_id,
            org_id=org_id,
            workspace_id=workspace_id,
            project_id=workspace_id,
            original_filename=safe_name,
            display_name=safe_name,
            media_type=media_type,
            byte_size=byte_size,
            checksum=checksum,
            storage_key=storage_key,
            # ON: 'queued' (a durable job drives extraction; NOT ready until a worker
            # runs process_file, so READY-only retrieval never exposes it early).
            # OFF: 'uploaded', then synchronous process_file below (unchanged behavior).
            status="queued" if async_enabled else "uploaded",
            uploaded_by=(uploaded_by or "")[:256] or None,
            source_chat_id=(source_chat_id or "")[:128] or None,
        )
        session.add(row)
        await session.flush()
        if async_enabled:
            # Persist file + durable processing job ATOMICALLY in one transaction. If
            # the enqueue fails, the whole transaction rolls back so a WorkspaceFile is
            # never left without a job (no orphaned/stuck user file).
            await enqueue_document_processing_job(
                org_id, workspace_id, row.id,
                job_type=JOB_TYPE_FILE_EXTRACTION, session=session,
            )
        await session.commit()
        await session.refresh(row)
        file_uuid = row.id
        payload = _payload(row)

    if async_enabled:
        # Durable async path: return 'queued'; a drain will process it to READY.
        return attach_request_id(payload)

    # OFF (default): preserve the existing synchronous processing path.
    processed = await process_file(org_id=org_id, workspace_id=workspace_id, file_id=file_uuid)
    return attach_request_id(processed)


async def process_file(
    *,
    org_id: uuid.UUID,
    workspace_id: uuid.UUID,
    file_id: uuid.UUID,
) -> dict[str, Any]:
    await _require_workspace(org_id, workspace_id)
    async with get_db_session() as session:
        await _set_org(session, org_id)
        row = await session.get(WorkspaceFile, file_id)
        if row is None or row.org_id != org_id or row.workspace_id != workspace_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
        row.status = "queued"
        await session.commit()

    async with get_db_session() as session:
        await _set_org(session, org_id)
        row = await session.get(WorkspaceFile, file_id)
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
        row.status = "processing"
        await session.commit()

    async with get_db_session() as session:
        await _set_org(session, org_id)
        row = await session.get(WorkspaceFile, file_id)
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
        path = storage.absolute_path_for_key(row.storage_key)
        if not path.exists():
            row.status = "failed"
            row.failure_code = "missing_bytes"
            row.failure_message = "Stored file bytes were not found."
            await session.commit()
            await session.refresh(row)
            return attach_request_id(_payload(row))

        text_out, err = extract_text(path, media_type=row.media_type, filename=row.original_filename)
        soft_codes = {
            "pdf_parser_unavailable",
            "docx_parser_unavailable",
            "xlsx_parser_unavailable",
        }
        suffix = Path(row.original_filename).suffix.lower()
        processable = SUPPORTED_TYPES.get(suffix, ("", False))[1]
        if err and processable and err not in soft_codes:
            row.status = "failed"
            row.failure_code = err.split(":")[0][:64]
            row.failure_message = err[:500]
            await session.commit()
            await session.refresh(row)
            return attach_request_id(_payload(row))

        row.extracted_text = text_out if text_out is not None else ""
        row.status = "ready"
        row.failure_code = err[:64] if err else None
        row.failure_message = (
            "Stored successfully; text extraction limited or unavailable."
            if err
            else None
        )
        await session.commit()
        await session.refresh(row)
        return attach_request_id(_payload(row))


async def list_files(
    *,
    org_id: uuid.UUID,
    workspace_id: uuid.UUID,
    status_filter: str | None = None,
    q: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    await _require_workspace(org_id, workspace_id)
    limit = max(1, min(200, int(limit)))
    async with get_db_session() as session:
        await _set_org(session, org_id)
        stmt = select(WorkspaceFile).where(
            WorkspaceFile.org_id == org_id,
            WorkspaceFile.workspace_id == workspace_id,
        )
        if status_filter == "processing":
            stmt = stmt.where(WorkspaceFile.status.in_(("uploaded", "queued", "processing")))
        elif status_filter == "failed":
            stmt = stmt.where(WorkspaceFile.status == "failed")
        elif status_filter == "ready":
            stmt = stmt.where(WorkspaceFile.status == "ready")
        query = (q or "").strip()
        if query:
            like = f"%{query}%"
            stmt = stmt.where(
                or_(
                    WorkspaceFile.display_name.ilike(like),
                    WorkspaceFile.original_filename.ilike(like),
                    WorkspaceFile.extracted_text.ilike(like),
                    WorkspaceFile.media_type.ilike(like),
                )
            )
        stmt = stmt.order_by(desc(WorkspaceFile.created_at)).limit(limit)
        rows = (await session.execute(stmt)).scalars().all()
    return attach_request_id(
        {
            "items": [_payload(r) for r in rows],
            "count": len(rows),
            "workspace_id": str(workspace_id),
            "max_upload_bytes": MAX_UPLOAD_BYTES,
            "supported_extensions": sorted(SUPPORTED_TYPES.keys()),
        }
    )


async def get_file(
    *,
    org_id: uuid.UUID,
    workspace_id: uuid.UUID,
    file_id: uuid.UUID,
    include_text_preview: bool = False,
) -> dict[str, Any]:
    await _require_workspace(org_id, workspace_id)
    async with get_db_session() as session:
        await _set_org(session, org_id)
        row = await session.get(WorkspaceFile, file_id)
        if row is None or row.org_id != org_id or row.workspace_id != workspace_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
        payload = _payload(row)
        if include_text_preview and row.extracted_text:
            payload["text_preview"] = row.extracted_text[:8000]
    return attach_request_id(payload)


async def open_file_bytes(
    *,
    org_id: uuid.UUID,
    workspace_id: uuid.UUID,
    file_id: uuid.UUID,
) -> tuple[Path, str, str]:
    """Return (path, media_type, download_name) after authz."""
    await _require_workspace(org_id, workspace_id)
    async with get_db_session() as session:
        await _set_org(session, org_id)
        row = await session.get(WorkspaceFile, file_id)
        if row is None or row.org_id != org_id or row.workspace_id != workspace_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
        path = storage.absolute_path_for_key(row.storage_key)
        if not path.exists():
            raise HTTPException(status.HTTP_404_NOT_FOUND, "File bytes missing")
        return path, row.media_type, row.display_name


@dataclass(frozen=True)
class WorkspaceFilesContext:
    """Rendered, size-capped block of ready Workspace File text for chat context."""

    block: str
    count: int
    chars: int
    truncated: bool


def _sanitize_file_name(name: str | None) -> str:
    cleaned = " ".join(str(name or "file").split())
    return cleaned.replace('"', "'")[:256] or "file"


async def load_ready_files_context(
    org_id: uuid.UUID,
    workspace_id: uuid.UUID,
    *,
    max_chars: int,
) -> WorkspaceFilesContext:
    """Read-only: assemble a filename-labeled, size-capped text block from the
    ready Workspace Files of a single org + workspace.

    - Queries only ``WorkspaceFile`` rows for the supplied ``org_id`` + ``workspace_id``.
    - Includes only ``status == "ready"`` rows with non-empty ``extracted_text``.
    - Deterministic ordering (``created_at``, then ``id``).
    - Strict total size cap (``max_chars``); the last included file is truncated
      deterministically when the cap is reached.
    - Preserves workspace/org isolation: the SQL ``WHERE`` clause plus a per-row
      re-check (defense-in-depth); RLS org scope is also set on the session.
    - Never writes.
    """
    if max_chars <= 0:
        return WorkspaceFilesContext(block="", count=0, chars=0, truncated=False)

    async with get_db_session() as session:
        await _set_org(session, org_id)
        stmt = (
            select(WorkspaceFile)
            .where(
                WorkspaceFile.org_id == org_id,
                WorkspaceFile.workspace_id == workspace_id,
                WorkspaceFile.status == "ready",
            )
            .order_by(WorkspaceFile.created_at.asc(), WorkspaceFile.id.asc())
        )
        rows = (await session.execute(stmt)).scalars().all()

    parts: list[str] = []
    total = 0
    count = 0
    truncated = False
    for row in rows:
        # Defense-in-depth: never emit a row outside the requested org/workspace,
        # and only ready rows that actually carry extracted text.
        if str(row.org_id) != str(org_id) or str(row.workspace_id) != str(workspace_id):
            continue
        if row.status != "ready":
            continue
        body = (row.extracted_text or "").strip()
        if not body:
            continue
        remaining = max_chars - total
        if remaining <= 0:
            truncated = True
            break
        if len(body) > remaining:
            body = body[:remaining]
            truncated = True
        name = _sanitize_file_name(row.display_name or row.original_filename)
        parts.append(f'[file name="{name}"]\n{body}\n[/file]')
        total += len(body)
        count += 1
        if truncated:
            break

    if not parts:
        return WorkspaceFilesContext(block="", count=0, chars=0, truncated=truncated)
    block = "<workspace_files>\n" + "\n".join(parts) + "\n</workspace_files>"
    return WorkspaceFilesContext(block=block, count=count, chars=total, truncated=truncated)


async def delete_file(
    *,
    org_id: uuid.UUID,
    workspace_id: uuid.UUID,
    file_id: uuid.UUID,
) -> dict[str, Any]:
    await _require_workspace(org_id, workspace_id)
    async with get_db_session() as session:
        await _set_org(session, org_id)
        row = await session.get(WorkspaceFile, file_id)
        if row is None or row.org_id != org_id or row.workspace_id != workspace_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
        key = row.storage_key
        await session.delete(row)
        await session.commit()
    try:
        storage.delete_storage(key)
    except Exception:
        pass
    return attach_request_id({"deleted": True, "id": str(file_id)})
