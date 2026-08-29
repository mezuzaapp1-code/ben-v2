"""Post-READY grounded Initial Read. Never runs inside drain extraction or upload HTTP."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select, text, update

from database.connection import get_db_session
from database.models import Message, WorkspaceFile, WorkspaceFileChunk, WorkspaceFilePage
from database.thread_store import list_thread_messages
from services.chat_prompt import GLOBAL_CHAT_SYSTEM
from services.message_format import decode_message, encode_chat_assistant
from services.model_gateway import route_request
from services.ops.failure_classification import classify_failure
from services.ops.structured_log import log_info, log_warning
from services.thread_service import persist_assistant_message_sqlite
from services.workspace_files.initial_read_pack import (
    PackChunk,
    render_pack_evidence,
    select_representative_chunks,
)
from services.workspace_files.source_policy import (
    FILE_INITIAL_READ_EVENT,
    INITIAL_READ_COMPLETE,
    INITIAL_READ_FAILED,
    INITIAL_READ_NONE,
    INITIAL_READ_PENDING,
    INITIAL_READ_SKIPPED,
)
from services.workspace_files.thread_sources import (
    is_vision_upload,
    log_source_state_error,
    on_file_failed,
    on_file_ready,
    parse_thread_uuid,
)

_SUBSYSTEM = "workspace_files"
_inflight: set[asyncio.Task] = set()

_INITIAL_READ_SYSTEM = (
    GLOBAL_CHAT_SYSTEM
    + " You are performing a first grounded read of one uploaded document. "
    "Use only the provided extracted text and metadata. "
    "If the pack is partial or pages need OCR, say so. "
    "Do not invent facts, geometry, or visual details that are not in the text. "
    "Do not claim a document type unless the extracted content or filename supports it."
)

_INITIAL_READ_INSTRUCTIONS = (
    "Write a concise acknowledgement of this document for the user who just uploaded it. Include:\n"
    "1. Document identity (type) only if supported by the extracted content or filename.\n"
    "2. A short overview of major content/categories actually present — not a generic filler.\n"
    "3. About 3–5 concrete findings traceable to the provided chunks (cite page numbers).\n"
    "4. Reliable metadata (filename, page count) when given.\n"
    "Keep it brief. Do not ask the user to restate the filename."
)


async def _set_org(session, org_id: uuid.UUID) -> None:
    await session.execute(
        text("SELECT set_config('app.current_org_id', :oid, true)"), {"oid": str(org_id)}
    )


def sqlite_has_initial_read(thread_id: uuid.UUID | str, file_id: uuid.UUID | str) -> bool:
    fid = str(file_id)
    try:
        rows = list_thread_messages(str(thread_id))
    except Exception:
        return False
    for row in rows:
        if row.role != "assistant":
            continue
        decoded = decode_message(row.role, row.content)
        if decoded.get("source_event") != FILE_INITIAL_READ_EVENT:
            continue
        if str(decoded.get("source_file_id") or "") == fid:
            return True
    return False


async def claim_initial_read(
    org_id: uuid.UUID, file_id: uuid.UUID
) -> WorkspaceFile | None:
    async with get_db_session() as session:
        await _set_org(session, org_id)
        stmt = (
            select(WorkspaceFile)
            .where(WorkspaceFile.id == file_id, WorkspaceFile.org_id == org_id)
            .with_for_update()
        )
        row = (await session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        if is_vision_upload(media_type=row.media_type, filename=row.original_filename):
            row.initial_read_status = INITIAL_READ_SKIPPED
            await session.commit()
            return None
        if not (row.source_chat_id or "").strip():
            row.initial_read_status = INITIAL_READ_SKIPPED
            await session.commit()
            return None
        thread_id = parse_thread_uuid(row.source_chat_id)
        if thread_id is None:
            row.initial_read_status = INITIAL_READ_SKIPPED
            await session.commit()
            return None
        if getattr(row, "status", None) != "ready":
            return None
        if sqlite_has_initial_read(thread_id, file_id):
            row.initial_read_status = INITIAL_READ_COMPLETE
            row.initial_read_at = datetime.now(timezone.utc)
            await session.commit()
            return None
        status = str(getattr(row, "initial_read_status", None) or INITIAL_READ_NONE)
        if status in {INITIAL_READ_PENDING, INITIAL_READ_COMPLETE, INITIAL_READ_SKIPPED}:
            return None
        row.initial_read_status = INITIAL_READ_PENDING
        await session.commit()
        await session.refresh(row)
        return row


async def _mark_initial_read(
    org_id: uuid.UUID,
    file_id: uuid.UUID,
    status: str,
) -> None:
    async with get_db_session() as session:
        await _set_org(session, org_id)
        values: dict[str, Any] = {"initial_read_status": status}
        if status in {INITIAL_READ_COMPLETE, INITIAL_READ_FAILED, INITIAL_READ_SKIPPED}:
            values["initial_read_at"] = datetime.now(timezone.utc)
        await session.execute(
            update(WorkspaceFile)
            .where(WorkspaceFile.id == file_id, WorkspaceFile.org_id == org_id)
            .values(**values)
        )
        await session.commit()


async def load_pack_chunks(org_id: uuid.UUID, workspace_id: uuid.UUID, file_id: uuid.UUID) -> list[PackChunk]:
    async with get_db_session() as session:
        await _set_org(session, org_id)
        page_chars: dict[int, int] = {}
        page_rows = (
            await session.execute(
                select(WorkspaceFilePage.page_number, WorkspaceFilePage.char_count).where(
                    WorkspaceFilePage.org_id == org_id,
                    WorkspaceFilePage.workspace_id == workspace_id,
                    WorkspaceFilePage.file_id == file_id,
                )
            )
        ).all()
        for pn, cc in page_rows:
            page_chars[int(pn)] = int(cc or 0)
        rows = (
            await session.execute(
                select(WorkspaceFileChunk)
                .where(
                    WorkspaceFileChunk.org_id == org_id,
                    WorkspaceFileChunk.workspace_id == workspace_id,
                    WorkspaceFileChunk.file_id == file_id,
                )
                .order_by(WorkspaceFileChunk.document_chunk_index.asc())
            )
        ).scalars().all()
        out: list[PackChunk] = []
        for row in rows:
            pn = int(row.page_number or 0)
            out.append(
                PackChunk(
                    file_id=row.file_id,
                    chunk_id=row.id,
                    page_number=pn,
                    document_chunk_index=int(row.document_chunk_index),
                    page_chunk_index=int(row.page_chunk_index or 0),
                    text=row.text or "",
                    char_count=int(row.char_count or 0),
                    page_char_count=page_chars.get(pn, 0),
                )
            )
        return out


async def page_coverage(
    org_id: uuid.UUID, workspace_id: uuid.UUID, file_id: uuid.UUID
) -> tuple[int, int]:
    async with get_db_session() as session:
        await _set_org(session, org_id)
        extracted = await session.scalar(
            select(func.count())
            .select_from(WorkspaceFilePage)
            .where(
                WorkspaceFilePage.org_id == org_id,
                WorkspaceFilePage.workspace_id == workspace_id,
                WorkspaceFilePage.file_id == file_id,
                WorkspaceFilePage.extraction_status == "extracted",
            )
        )
        needs = await session.scalar(
            select(func.count())
            .select_from(WorkspaceFilePage)
            .where(
                WorkspaceFilePage.org_id == org_id,
                WorkspaceFilePage.workspace_id == workspace_id,
                WorkspaceFilePage.file_id == file_id,
                WorkspaceFilePage.extraction_status == "needs_ocr",
            )
        )
        return int(extracted or 0), int(needs or 0)


def _fallback_prefix_text(extracted_text: str | None, max_chars: int = 4000) -> str:
    body = (extracted_text or "").strip()
    if not body:
        return ""
    if len(body) > max_chars:
        return body[:max_chars]
    return body


async def run_initial_read(org_id: uuid.UUID, workspace_id: uuid.UUID, file_id: uuid.UUID) -> dict[str, Any]:
    """Generate at most one grounded overview. Safe to call concurrently."""
    claimed = await claim_initial_read(org_id, file_id)
    if claimed is None:
        return {"outcome": "skipped"}

    thread_id = parse_thread_uuid(claimed.source_chat_id)
    if thread_id is None:
        await _mark_initial_read(org_id, file_id, INITIAL_READ_SKIPPED)
        return {"outcome": "skipped"}

    if sqlite_has_initial_read(thread_id, file_id):
        await _mark_initial_read(org_id, file_id, INITIAL_READ_COMPLETE)
        return {"outcome": "already_present"}

    try:
        chunks = await load_pack_chunks(org_id, workspace_id, file_id)
        selected = select_representative_chunks(chunks)
        extracted, needs_ocr = await page_coverage(org_id, workspace_id, file_id)
        if selected:
            evidence = render_pack_evidence(
                display_name=claimed.display_name or claimed.original_filename,
                file_id=claimed.id,
                page_count=claimed.page_count,
                extraction_status=claimed.extraction_status or "",
                pages_extracted=extracted,
                pages_needs_ocr=needs_ocr,
                chunks=selected,
            )
        else:
            prefix = _fallback_prefix_text(claimed.extracted_text)
            evidence = render_pack_evidence(
                display_name=claimed.display_name or claimed.original_filename,
                file_id=claimed.id,
                page_count=claimed.page_count,
                extraction_status=claimed.extraction_status or "legacy",
                pages_extracted=extracted,
                pages_needs_ocr=needs_ocr,
                chunks=[],
            )
            if prefix:
                evidence += f"\n[legacy_prefix]\n{prefix}\n[/legacy_prefix]"
            else:
                evidence += (
                    "\nNo usable extracted text was available. "
                    "Acknowledge the upload using filename and page metadata only."
                )

        user_message = f"{_INITIAL_READ_INSTRUCTIONS}\n\n{evidence}"
        result = await route_request(
            user_message,
            tenant_id=str(org_id),
            tier="free",
            system=_INITIAL_READ_SYSTEM,
        )
        content = str((result or {}).get("content") or "").strip()
        if not content:
            await _mark_initial_read(org_id, file_id, INITIAL_READ_FAILED)
            return {"outcome": "failed", "reason": "empty_model"}

        used = [
            {
                "id": str(claimed.id),
                "name": claimed.display_name or claimed.original_filename or "file",
            }
        ]
        encoded = encode_chat_assistant(
            content,
            model_used=str((result or {}).get("model_used") or ""),
            cost_usd=float((result or {}).get("cost_usd") or 0),
            provider_id="",
            provider_used=str((result or {}).get("provider_used") or ""),
            used_files=used,
            source_event=FILE_INITIAL_READ_EVENT,
            source_file_id=str(claimed.id),
        )
        persist_assistant_message_sqlite(
            thread_id,
            encoded_content=encoded,
            provider=str((result or {}).get("provider_used") or None),
        )
        async with get_db_session() as session:
            await _set_org(session, org_id)
            session.add(
                Message(
                    org_id=org_id,
                    thread_id=thread_id,
                    role="assistant",
                    content=encoded,
                )
            )
            await session.commit()
        await _mark_initial_read(org_id, file_id, INITIAL_READ_COMPLETE)
        log_info(
            "file initial read complete",
            subsystem=_SUBSYSTEM,
            operation="file_initial_read",
            outcome="ok",
            file_id=str(file_id),
        )
        return {"outcome": "ok"}
    except Exception as exc:  # noqa: BLE001
        await _mark_initial_read(org_id, file_id, INITIAL_READ_FAILED)
        log_warning(
            "file initial read failed",
            subsystem=_SUBSYSTEM,
            operation="file_initial_read",
            outcome="error",
            file_id=str(file_id),
            category=classify_failure(exc),
            error_class=type(exc).__name__,
        )
        return {"outcome": "failed", "reason": type(exc).__name__}


async def _run_scheduled(org_id: uuid.UUID, workspace_id: uuid.UUID, file_id: uuid.UUID) -> None:
    try:
        await run_initial_read(org_id, workspace_id, file_id)
    except Exception as exc:  # noqa: BLE001
        log_warning(
            "file initial read schedule leaked",
            subsystem=_SUBSYSTEM,
            operation="file_initial_read",
            outcome="error",
            file_id=str(file_id),
            error_class=type(exc).__name__,
        )


def schedule_initial_read(org_id: Any, workspace_id: Any, file_id: Any) -> bool:
    """Fire-and-forget. Never raises. Never awaited from upload or drain extract."""
    try:
        oid = uuid.UUID(str(org_id))
        wid = uuid.UUID(str(workspace_id))
        fid = uuid.UUID(str(file_id))
    except (TypeError, ValueError, AttributeError):
        return False
    try:
        task = asyncio.create_task(
            _run_scheduled(oid, wid, fid),
            name=f"initial-read-{fid}",
        )
    except RuntimeError:
        return False
    _inflight.add(task)
    task.add_done_callback(_inflight.discard)
    return True


def reset_initial_read_schedule_for_tests() -> None:
    _inflight.clear()


async def notify_file_processed(
    *,
    org_id: uuid.UUID,
    workspace_id: uuid.UUID,
    file_id: uuid.UUID,
    ready: bool,
) -> None:
    """Source-state + schedule Initial Read. Drain/process_file must not await the LLM."""
    try:
        if ready:
            await on_file_ready(org_id=org_id, file_id=file_id)
            schedule_initial_read(org_id, workspace_id, file_id)
        else:
            await on_file_failed(org_id=org_id, file_id=file_id)
    except Exception as exc:  # noqa: BLE001
        log_source_state_error(exc, operation="notify_file_processed", file_id=str(file_id))
