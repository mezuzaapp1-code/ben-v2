"""Workspace File Library V1 — upload, process, list, search, download, delete."""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import desc, or_, select, text

from database.connection import get_db_session
from database.models import Project, WorkspaceFile
from services.ops.request_context import attach_request_id
from services.workspace_files import storage
from services.workspace_files.chunk_retriever import (
    MAX_CHARS_PER_FILE,
    MAX_EVIDENCE_CHARS,
    ReadyFile,
    RetrievalDiagnostics,
    apply_chunk_budget,
    build_or_tsquery,
    chunk_retrieval_enabled,
    claimed_indexed_ids,
    diagnostics_from_pack,
    group_chunks_by_file,
    log_index_chunk_mismatch,
    named_ready_files,
    normalize_query_tokens,
    pack_coverage,
    prove_chunk_rows,
    qualify_indexed_ids,
    ready_file_from_row,
    render_chunk_group,
    render_evidence_block,
    render_legacy_group,
    search_chunks_bounded,
)
from services.workspace_files.extract import extract_text
from services.workspace_files.file_resolver import (
    PER_FILE_MAX_CHARS,
    apply_context_budget,
    eligible_from_row,
    rank_eligible_files,
)
from services.workspace_files.job_queue import (
    JOB_TYPE_FILE_EXTRACTION,
    enqueue_document_processing_job,
)
from services.workspace_files.types import (
    MAX_UPLOAD_BYTES,
    REJECTED_EXTENSIONS,
    SUPPORTED_TYPES,
)

NON_READY_CHAT_STATUSES = frozenset({"queued", "processing", "uploaded"})


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
    except storage.DurableStorageUnavailable as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            str(exc) or "Durable file storage is required but unavailable.",
        ) from exc
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
    retrieval_mode: str = "off"
    files_eligible: int = 0
    files_searched: int = 0
    files_searched_ids: tuple[str, ...] = ()
    files_legacy: int = 0
    chunks_considered: int = 0
    chunks_selected: int = 0
    evidence_chars: int = 0
    evidence_pages: tuple[int, ...] = ()
    fts_latency_ms: float | None = None
    fallback_reason: str | None = None
    extraction_coverage: str = "legacy"
    used_files: tuple[dict[str, str], ...] = ()
    unavailable_count: int = 0


def _sanitize_file_name(name: str | None) -> str:
    cleaned = " ".join(str(name or "file").split())
    return cleaned.replace('"', "'")[:256] or "file"


def _used_files_payload(entries: list[tuple[Any, str]]) -> tuple[dict[str, str], ...]:
    """Tenant-scoped filenames of files actually injected. IDs must be present."""
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for file_id, name in entries:
        fid = str(file_id or "").strip()
        if not fid or fid in seen:
            continue
        seen.add(fid)
        out.append({"id": fid, "name": _sanitize_file_name(name)})
    return tuple(out)


def _used_files_from_budgeted(budgeted) -> tuple[dict[str, str], ...]:
    return _used_files_payload(
        [(getattr(item, "file_id", ""), item.name) for item in budgeted]
    )


def _unavailable_count(rows, org_id: uuid.UUID, workspace_id: uuid.UUID) -> int:
    n = 0
    for row in rows:
        if str(getattr(row, "org_id", "")) != str(org_id):
            continue
        if str(getattr(row, "workspace_id", "")) != str(workspace_id):
            continue
        if getattr(row, "status", None) in NON_READY_CHAT_STATUSES:
            n += 1
    return n


def _context_from_gate3d(
    eligible,
    user_query: str | None,
    max_chars: int,
    per_file_max: int | None,
    *,
    retrieval_mode: str = "off",
    fallback_reason: str | None = "flag_off",
    files_eligible: int = 0,
) -> WorkspaceFilesContext:
    """Exact Gate 3D injection format. Used when chunk retrieval is OFF."""
    ranked = rank_eligible_files(eligible, user_query)
    budgeted, truncated = apply_context_budget(
        ranked,
        max_chars=max_chars,
        per_file_max=per_file_max if per_file_max is not None else PER_FILE_MAX_CHARS,
        sanitize_name=_sanitize_file_name,
    )
    used_files = _used_files_from_budgeted(budgeted)
    if not budgeted:
        return WorkspaceFilesContext(
            block="",
            count=0,
            chars=0,
            truncated=truncated,
            retrieval_mode=retrieval_mode,
            files_eligible=files_eligible,
            fallback_reason=fallback_reason,
            extraction_coverage="legacy",
            used_files=used_files,
        )
    parts = [f'[file name="{item.name}"]\n{item.text}\n[/file]' for item in budgeted]
    total = sum(item.chars for item in budgeted)
    block = "<workspace_files>\n" + "\n".join(parts) + "\n</workspace_files>"
    return WorkspaceFilesContext(
        block=block,
        count=len(budgeted),
        chars=total,
        truncated=truncated,
        retrieval_mode=retrieval_mode,
        files_eligible=files_eligible,
        files_legacy=len(budgeted),
        evidence_chars=total,
        fallback_reason=fallback_reason,
        extraction_coverage="legacy",
        used_files=used_files,
    )


def _labeled_prefix_context(
    eligible,
    user_query: str | None,
    max_chars: int,
    per_file_max: int | None,
    *,
    allow_ids: set[str] | None,
    diag: RetrievalDiagnostics,
) -> WorkspaceFilesContext:
    ranked = rank_eligible_files(eligible, user_query)
    if allow_ids is not None:
        ranked = [item for item in ranked if str(item.file.id) in allow_ids]
    budget = min(max_chars, MAX_EVIDENCE_CHARS)
    file_cap = min(
        per_file_max if per_file_max is not None else PER_FILE_MAX_CHARS,
        MAX_CHARS_PER_FILE,
    )
    budgeted, truncated = apply_context_budget(
        ranked,
        max_chars=budget,
        per_file_max=file_cap,
        sanitize_name=_sanitize_file_name,
    )
    used_files = _used_files_from_budgeted(budgeted)
    if not budgeted:
        return WorkspaceFilesContext(
            block="",
            count=0,
            chars=0,
            truncated=truncated,
            retrieval_mode=diag.retrieval_mode,
            files_eligible=diag.files_eligible,
            files_searched=diag.files_searched,
            files_searched_ids=diag.files_searched_ids,
            files_legacy=0,
            chunks_considered=diag.chunks_considered,
            chunks_selected=0,
            evidence_chars=0,
            evidence_pages=(),
            fts_latency_ms=diag.fts_latency_ms,
            fallback_reason=diag.fallback_reason,
            extraction_coverage=diag.extraction_coverage,
            used_files=used_files,
        )
    parts = [render_legacy_group(item.name, item.text) for item in budgeted]
    total = sum(item.chars for item in budgeted)
    coverage = "legacy"
    block = render_evidence_block(
        retrieval_mode="prefix_fallback",
        coverage=coverage,
        file_parts=parts,
    )
    return WorkspaceFilesContext(
        block=block,
        count=len(budgeted),
        chars=total,
        truncated=truncated,
        retrieval_mode="prefix_fallback",
        files_eligible=diag.files_eligible,
        files_searched=diag.files_searched,
        files_searched_ids=diag.files_searched_ids,
        files_legacy=len(budgeted),
        chunks_considered=diag.chunks_considered,
        chunks_selected=0,
        evidence_chars=total,
        evidence_pages=(),
        fts_latency_ms=diag.fts_latency_ms,
        fallback_reason=diag.fallback_reason,
        extraction_coverage=coverage,
        used_files=used_files,
    )


def _empty_gate4a_context(diag: RetrievalDiagnostics) -> WorkspaceFilesContext:
    """No model-context injection. Used Files must be empty."""
    return WorkspaceFilesContext(
        block="",
        count=0,
        chars=0,
        truncated=False,
        retrieval_mode="empty",
        files_eligible=diag.files_eligible,
        files_searched=diag.files_searched,
        files_searched_ids=diag.files_searched_ids,
        files_legacy=0,
        chunks_considered=diag.chunks_considered,
        chunks_selected=0,
        evidence_chars=0,
        evidence_pages=(),
        fts_latency_ms=diag.fts_latency_ms,
        fallback_reason=diag.fallback_reason,
        extraction_coverage="none",
        used_files=(),
    )


async def load_ready_files_context(
    org_id: uuid.UUID,
    workspace_id: uuid.UUID,
    *,
    max_chars: int,
    user_query: str | None = None,
    per_file_max: int | None = None,
) -> WorkspaceFilesContext:
    """Read-only: assemble a filename-labeled, size-capped text block from the
    ready Workspace Files of a single org + workspace.

    Gate 3D pipeline (selection before budgeting) when chunk retrieval is OFF:

    1. Query READY rows for the supplied ``org_id`` + ``workspace_id``.
    2. Filter eligibility (org/workspace/status/non-empty text) with no budget.
    3. Rank the full eligible set from ``user_query`` (explicit filename, then
       lexical overlap, then recency).
    4. Apply per-file and global character budgets only to the ranked list.

    Gate 4A (flag ON **and** workspace allowlisted) searches authorized indexed
    chunks across the eligible READY set (or the explicitly named subset).

    No-match policy: inject selected chunks on a lexical hit; inject nothing
    when there is no hit; if the user explicitly names READY file(s), a
    bounded prefix of **those files only** may be used. Unrelated READY
    prefixes are never dumped.

    Isolation is unchanged: SQL ``WHERE`` plus per-row re-check; RLS org scope
    is set on the session. Never writes. Ranking cannot admit a non-eligible row.
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
            )
        )
        rows = (await session.execute(stmt)).scalars().all()

    unavailable = _unavailable_count(rows, org_id, workspace_id)
    eligible = []
    for row in rows:
        item = eligible_from_row(row, org_id, workspace_id)
        if item is not None:
            eligible.append(item)

    if not chunk_retrieval_enabled(workspace_id):
        ctx = _context_from_gate3d(
            eligible,
            user_query,
            max_chars,
            per_file_max,
            retrieval_mode="off",
            fallback_reason="flag_off",
            files_eligible=len(eligible),
        )
        return replace(ctx, unavailable_count=unavailable)

    ctx = await _load_gate4a_context(
        org_id,
        workspace_id,
        rows,
        eligible,
        user_query=user_query,
        max_chars=max_chars,
        per_file_max=per_file_max,
    )
    return replace(ctx, unavailable_count=unavailable)


async def _load_gate4a_context(
    org_id: uuid.UUID,
    workspace_id: uuid.UUID,
    rows,
    eligible,
    *,
    user_query: str | None,
    max_chars: int,
    per_file_max: int | None,
) -> WorkspaceFilesContext:
    ready: list[ReadyFile] = []
    for row in rows:
        item = ready_file_from_row(row, org_id, workspace_id)
        if item is not None:
            ready.append(item)

    named = named_ready_files(ready, user_query)
    search_set = named if named else ready
    named_ids = {str(f.id) for f in named} if named else None

    tokens = normalize_query_tokens(user_query)
    tsquery = build_or_tsquery(tokens)
    claimed, mismatch = claimed_indexed_ids(search_set)

    base_diag = diagnostics_from_pack(
        mode="prefix_fallback",
        eligible=len(ready),
        searched_ids=[],
        legacy_count=0,
        considered=0,
        selected=[],
        evidence_chars=0,
        latency_ms=None,
        fallback_reason="not_indexed",
        coverage="legacy",
        mismatch_ids=mismatch,
    )

    def _prefix(reason: str, *, searched: list | None = None, latency: float | None = None) -> WorkspaceFilesContext:
        diag = diagnostics_from_pack(
            mode="prefix_fallback",
            eligible=len(ready),
            searched_ids=searched if searched is not None else [],
            legacy_count=0,
            considered=0,
            selected=[],
            evidence_chars=0,
            latency_ms=latency if latency is not None else base_diag.fts_latency_ms,
            fallback_reason=reason,
            coverage="legacy",
            mismatch_ids=mismatch,
        )
        if named_ids:
            return _labeled_prefix_context(
                eligible,
                user_query,
                max_chars,
                per_file_max,
                allow_ids=named_ids,
                diag=diag,
            )
        return _empty_gate4a_context(diag)

    if not tokens or not tsquery:
        return _prefix("no_tokens")

    async with get_db_session() as session:
        await _set_org(session, org_id)
        try:
            counts = await prove_chunk_rows(
                session, org_id=org_id, workspace_id=workspace_id, file_ids=claimed
            )
        except Exception:
            return _prefix("fts_error")
        qualified, mismatch_all = qualify_indexed_ids(claimed, mismatch, counts)
        mismatch = mismatch_all
        if mismatch:
            log_index_chunk_mismatch(mismatch)
        if not qualified:
            return _prefix("index_chunk_mismatch" if mismatch else "not_indexed")

        hits, latency, error = await search_chunks_bounded(
            session,
            org_id=org_id,
            workspace_id=workspace_id,
            file_ids=qualified,
            tsquery=tsquery,
        )

    if error:
        return _prefix(error, searched=qualified, latency=latency)
    if not hits:
        return _prefix("no_lexical_match", searched=qualified, latency=latency)

    selected = apply_chunk_budget(hits)
    by_id = {str(f.id): f for f in search_set}
    grouped = group_chunks_by_file(selected)
    chunk_files = [by_id[str(fid)] for fid, _ch in grouped if str(fid) in by_id]

    parts: list[str] = []
    evidence_chars = 0
    for fid, chunks in grouped:
        meta = by_id.get(str(fid))
        if meta is None:
            continue
        parts.append(render_chunk_group(meta, chunks))
        evidence_chars += sum(len(c.text) for c in chunks)

    if not parts:
        return _prefix("no_lexical_match", searched=qualified, latency=latency)

    coverage = pack_coverage(chunk_files, has_chunks=True, has_legacy=False)
    block = render_evidence_block(retrieval_mode="chunks", coverage=coverage, file_parts=parts)
    truncated = len(hits) > len(selected) or evidence_chars >= MAX_EVIDENCE_CHARS
    used_entries: list[tuple[Any, str]] = []
    for fid, _chunks in grouped:
        meta = by_id.get(str(fid))
        if meta is None:
            continue
        used_entries.append((meta.id, meta.display_name or meta.original_filename))
    return WorkspaceFilesContext(
        block=block,
        count=len(grouped),
        chars=evidence_chars,
        truncated=truncated,
        retrieval_mode="chunks",
        files_eligible=len(ready),
        files_searched=len(qualified),
        files_searched_ids=tuple(str(i) for i in qualified),
        files_legacy=0,
        chunks_considered=len(hits),
        chunks_selected=len(selected),
        evidence_chars=evidence_chars,
        evidence_pages=tuple(h.page_number for h in selected),
        fts_latency_ms=latency,
        fallback_reason=None,
        extraction_coverage=coverage,
        used_files=_used_files_payload(used_entries),
    )


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
