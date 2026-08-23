"""Gate 2 extraction pipeline: source bytes -> StructuredDocument -> atomic
persistence of WorkspaceFilePage + WorkspaceFileChunk with truthful lifecycle.

Explicit entrypoint only. NOT auto-wired into the upload critical path in Gate 2,
so existing upload behavior is unchanged and no historical file is processed. A
later gate (under review) may invoke this for new uploads or an explicit
reprocessing path.

Guarantees:
- every detected source page is persisted exactly once (coverage truth);
- extraction completeness is independent of index completeness;
- persistence is a single transaction with delete-by-version + insert, so retries
  are idempotent (protected by the Gate 1 uniqueness constraints);
- no state where the file claims 'complete'/'indexed' while rows are incomplete;
- no document text or secrets are logged.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select, text

from database.connection import get_db_session
from database.models import WorkspaceFile, WorkspaceFileChunk, WorkspaceFilePage
from services.ops.failure_classification import classify_failure
from services.ops.structured_log import log_info, log_warning
from services.workspace_files import storage
from services.workspace_files.chunking import CHUNKING_VERSION, chunk_structured_document
from services.workspace_files.document_parser import (
    EXTRACTION_VERSION,
    MAX_EXTRACT_PAGES,
    PAGE_EMPTY,
    PAGE_EXTRACTED,
    PAGE_FAILED,
    PAGE_NEEDS_OCR,
    PAGE_SKIPPED,
    StructuredDocument,
    resolve_parser,
)
from services.workspace_files.extract import MAX_EXTRACT_CHARS

# Bump when the physical index representation changes (FTS config/schema).
INDEXING_VERSION = 1


def valid_source_without_text(doc: StructuredDocument) -> bool:
    """True for a parsed, non-broken source that has no usable text.

    Image-only / needs_ocr-only pages are a valid stored source. That is not the
    same as a parse/storage failure (zero pages, PAGE_FAILED, or extracted text).
    Empty-only documents are not this case.
    """
    if not doc.pages:
        return False
    if any(p.status == PAGE_FAILED for p in doc.pages):
        return False
    if any(p.status == PAGE_EXTRACTED and (p.char_count or 0) > 0 for p in doc.pages):
        return False
    return any(p.status == PAGE_NEEDS_OCR for p in doc.pages)


def _legacy_projection(
    doc: StructuredDocument, extraction_status: str
) -> tuple[str, str | None, str | None, str | None]:
    """Gate 3C temporary compatibility bridge (removed when Gate 4 retrieval lands).

    Derive the legacy WorkspaceFile.status + extracted_text from the SAME parsed
    StructuredDocument (no second parse, no provider) so current chat retrieval
    (status='ready' + extracted_text) keeps working. Structured lifecycle remains
    authoritative; these legacy fields are a projection only.

    Returns (status, extracted_text, failure_code, failure_message).

    Source lifecycle (``status``) is independent of text extraction. A valid
    image / needs_ocr-only source is ``ready`` with empty text. Broken sources
    stay ``failed``.
    """
    if extraction_status in ("complete", "partial"):
        parts = [p.text for p in doc.pages if p.status == PAGE_EXTRACTED and p.text]
        text = "\n".join(parts).replace("\x00", " ")
        if len(text) > MAX_EXTRACT_CHARS:
            text = text[:MAX_EXTRACT_CHARS]
        # complete/partial always carry usable text (see derive_lifecycle).
        return "ready", (text or None), None, None
    if valid_source_without_text(doc):
        # Stored source is fine. No text extractor / OCR ran. Do not fabricate
        # text and do not mark the file as a source failure.
        return "ready", "", None, None
    return "failed", None, "extraction_failed", "No usable text extracted from document."


async def _set_org(session, org_id: uuid.UUID) -> None:
    await session.execute(
        text("SELECT set_config('app.current_org_id', :oid, true)"), {"oid": str(org_id)}
    )


def derive_lifecycle(doc: StructuredDocument, chunk_count: int) -> tuple[str, bool]:
    """Return (extraction_status, extraction_truncated).

    complete: not truncated AND every page is extracted/empty (fully read).
    partial : has usable text but some page needs_ocr/failed/skipped OR truncated.
    failed  : no usable extracted text anywhere.
    """
    has_usable = chunk_count > 0 or any(
        p.status == PAGE_EXTRACTED and p.char_count > 0 for p in doc.pages
    )
    problem = any(p.status in (PAGE_NEEDS_OCR, PAGE_FAILED, PAGE_SKIPPED) for p in doc.pages)
    if not has_usable:
        return "failed", doc.truncated
    if doc.truncated or problem:
        return "partial", doc.truncated
    return "complete", doc.truncated


async def _mark(file_id: uuid.UUID, org_id: uuid.UUID, **fields: Any) -> None:
    async with get_db_session() as session:
        await _set_org(session, org_id)
        row = await session.get(WorkspaceFile, file_id)
        if row is None:
            return
        for k, v in fields.items():
            setattr(row, k, v)
        await session.commit()


async def run_structured_extraction(
    org_id: uuid.UUID,
    workspace_id: uuid.UUID,
    file_id: uuid.UUID,
) -> dict[str, Any]:
    """Explicit, idempotent, atomic extraction+chunk persistence for one file."""
    t_start = time.perf_counter()

    # --- Claim (own transaction): verify tenant + mark in-progress ---
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
            return {"error": "file_not_found", "file_id": str(file_id)}
        media_type = row.media_type
        filename = row.original_filename
        storage_key = row.storage_key
        row.extraction_status = "extracting"
        # Indexing is a later stage; claiming both together hid INDEXING from the UI.
        row.index_status = "not_indexed"
        row.processing_error = None
        await session.commit()

    diag: dict[str, Any] = {
        "file_id": str(file_id),
        "extraction_version": EXTRACTION_VERSION,
        "chunking_version": CHUNKING_VERSION,
        "indexing_version": INDEXING_VERSION,
    }

    try:
        path = storage.absolute_path_for_key(storage_key)
        if not path.exists():
            await _mark(
                file_id, org_id,
                extraction_status="failed", index_status="not_indexed",
                status="failed", extracted_text=None,
                failure_code="missing_bytes", failure_message="Stored file bytes were not found.",
                processing_error="missing_bytes", extraction_version=EXTRACTION_VERSION,
            )
            diag.update({"error": "missing_bytes", "final_extraction_status": "failed",
                         "final_index_status": "not_indexed"})
            log_warning("extraction missing bytes", subsystem="workspace_files",
                        operation="structured_extraction", outcome="error", **_safe(diag))
            return diag

        parser = resolve_parser(media_type, filename)
        t_parse = time.perf_counter()
        doc: StructuredDocument = await asyncio.to_thread(
            parser.parse, path, media_type=media_type, filename=filename, max_pages=MAX_EXTRACT_PAGES,
        )
        parse_ms = round((time.perf_counter() - t_parse) * 1000.0, 1)

        # Parse finished; chunk persist is the indexing stage. Status stays
        # non-ready so retrieval cannot see the file early.
        await _mark(file_id, org_id, index_status="indexing")

        t_chunk = time.perf_counter()
        chunks = chunk_structured_document(doc)
        chunk_ms = round((time.perf_counter() - t_chunk) * 1000.0, 1)

        extraction_status, truncated = derive_lifecycle(doc, len(chunks))
        index_status = "indexed" if extraction_status != "failed" else "not_indexed"

        # --- Atomic persistence: delete-by-version then insert; single txn ---
        t_persist = time.perf_counter()
        async with get_db_session() as session:
            await _set_org(session, org_id)
            await session.execute(
                delete(WorkspaceFileChunk).where(
                    WorkspaceFileChunk.file_id == file_id,
                    WorkspaceFileChunk.chunking_version == CHUNKING_VERSION,
                )
            )
            await session.execute(
                delete(WorkspaceFilePage).where(
                    WorkspaceFilePage.file_id == file_id,
                    WorkspaceFilePage.extraction_version == EXTRACTION_VERSION,
                )
            )
            page_id_by_number: dict[int, uuid.UUID] = {}
            for p in doc.pages:
                pg = WorkspaceFilePage(
                    org_id=org_id, workspace_id=workspace_id, file_id=file_id,
                    page_number=p.page_number, extraction_status=p.status,
                    char_count=p.char_count, needs_ocr=p.needs_ocr,
                    failure_code=p.failure_code, failure_detail=p.failure_detail,
                    extraction_version=EXTRACTION_VERSION,
                )
                session.add(pg)
                await session.flush()
                page_id_by_number[p.page_number] = pg.id
            for ch in chunks:
                session.add(
                    WorkspaceFileChunk(
                        org_id=org_id, workspace_id=workspace_id, file_id=file_id,
                        page_id=page_id_by_number.get(ch.page_number),
                        page_number=ch.page_number, page_chunk_index=ch.page_chunk_index,
                        document_chunk_index=ch.document_chunk_index,
                        text=ch.text, char_count=ch.char_count,
                        extraction_version=EXTRACTION_VERSION, chunking_version=CHUNKING_VERSION,
                    )
                )
            file_row = await session.get(WorkspaceFile, file_id)
            file_row.extraction_status = extraction_status
            file_row.index_status = index_status
            file_row.page_count = doc.source_page_count
            file_row.extraction_truncated = truncated
            file_row.extraction_version = EXTRACTION_VERSION
            file_row.chunking_version = CHUNKING_VERSION
            file_row.indexing_version = INDEXING_VERSION
            file_row.indexed_chunk_count = len(chunks)
            file_row.indexed_at = datetime.now(timezone.utc) if index_status == "indexed" else None
            file_row.processing_error = None
            # Legacy compatibility projection (same transaction, same parse).
            legacy_status, legacy_text, legacy_code, legacy_msg = _legacy_projection(doc, extraction_status)
            file_row.status = legacy_status
            file_row.extracted_text = legacy_text
            file_row.failure_code = legacy_code
            file_row.failure_message = legacy_msg
            await session.commit()
        persist_ms = round((time.perf_counter() - t_persist) * 1000.0, 1)

        diag.update({
            "parser_id": doc.parser_id,
            "parser_version": doc.parser_version,
            "source_page_count": doc.source_page_count,
            "pages_extracted": sum(1 for p in doc.pages if p.status == PAGE_EXTRACTED),
            "pages_empty": sum(1 for p in doc.pages if p.status == PAGE_EMPTY),
            "pages_needs_ocr": sum(1 for p in doc.pages if p.status == PAGE_NEEDS_OCR),
            "pages_failed": sum(1 for p in doc.pages if p.status == PAGE_FAILED),
            "pages_skipped": sum(1 for p in doc.pages if p.status == PAGE_SKIPPED),
            "chunk_count": len(chunks),
            "truncated": truncated,
            "final_extraction_status": extraction_status,
            "final_index_status": index_status,
            "valid_source_without_text": valid_source_without_text(doc),
            "parse_ms": parse_ms, "chunk_ms": chunk_ms, "persist_ms": persist_ms,
            "total_ms": round((time.perf_counter() - t_start) * 1000.0, 1),
        })
        log_info("structured extraction complete", subsystem="workspace_files",
                 operation="structured_extraction", outcome="ok", **_safe(diag))
        return diag

    except Exception as e:  # noqa: BLE001
        # Infrastructure/persistence error (not a determinate document outcome):
        # keep legacy status truthful and let the drain classify for retry.
        await _mark(
            file_id, org_id,
            extraction_status="failed", index_status="failed",
            status="failed", extracted_text=None,
            failure_code="extraction_error", failure_message=f"{type(e).__name__}"[:500],
            processing_error=f"{type(e).__name__}"[:64], extraction_version=EXTRACTION_VERSION,
        )
        diag.update({"error": type(e).__name__, "final_extraction_status": "failed",
                     "final_index_status": "failed"})
        log_warning("structured extraction failed", subsystem="workspace_files",
                    provider="database", category=classify_failure(e), exc=e,
                    operation="structured_extraction", outcome="error", **_safe(diag))
        return diag


def _safe(d: dict[str, Any]) -> dict[str, Any]:
    """Only non-sensitive scalar diagnostics for logs (never text/secrets)."""
    keep = {
        "file_id", "parser_id", "parser_version", "extraction_version", "chunking_version",
        "indexing_version", "source_page_count", "pages_extracted", "pages_empty",
        "pages_needs_ocr", "pages_failed", "pages_skipped", "chunk_count", "truncated",
        "final_extraction_status", "final_index_status", "valid_source_without_text",
        "parse_ms", "chunk_ms", "persist_ms", "total_ms", "error",
    }
    return {k: v for k, v in d.items() if k in keep}
