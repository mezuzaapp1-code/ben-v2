"""Gate 4A — local Postgres chunk FTS after Gate 3D eligibility.

Flag-gated. Does not change Gate 3D rank math. Isolation is org + workspace +
authorized file IDs. No embeddings, no provider calls, no reprocessing.
"""
from __future__ import annotations

import asyncio
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable

from sqlalchemy import func, select

from database.models import WorkspaceFileChunk
from services.ops.structured_log import log_warning
from services.workspace_files.file_resolver import (
    file_is_explicitly_named,
    significant_tokens_in_order,
)

MAX_CHUNKS_CONSIDERED = 40
MAX_CHUNKS_SELECTED = 8
MAX_CHUNKS_PER_FILE = 4
MAX_EVIDENCE_CHARS = 6000
MAX_CHARS_PER_FILE = 3000
FTS_TIMEOUT_S = 0.2
MAX_QUERY_TOKENS = 12

# Safe tsquery atoms only: latin/hebrew/digits already produced by the tokenizer.
_SAFE_TOKEN_RE = re.compile(r"^[0-9a-z\u0590-\u05ff]+$")

# Small Hebrew function-word list. Length-2 words are already dropped by the
# tokenizer (min length 3); they are listed for completeness.
HEBREW_STOPWORDS = frozenset(
    {
        "של",
        "את",
        "על",
        "זה",
        "זאת",
        "זו",
        "אלה",
        "מה",
        "מי",
        "איך",
        "האם",
        "יש",
        "אין",
        "עם",
        "כל",
        "בין",
        "עד",
        "רק",
        "עוד",
        "כמו",
        "אני",
        "אנחנו",
        "הוא",
        "היא",
        "הם",
        "הן",
        "אומר",
        "אם",
        "לא",
        "גם",
        "כי",
    }
)

_FLAG_ON = frozenset({"1", "true", "yes", "on"})


def chunk_retrieval_enabled(workspace_id: Any) -> bool:
    """Fail-safe OFF. Optional workspace allowlist when the flag is on."""
    raw = (os.getenv("BEN_WORKSPACE_CHUNK_RETRIEVAL") or "").strip().lower()
    if raw not in _FLAG_ON:
        return False
    allow = (os.getenv("BEN_WORKSPACE_CHUNK_RETRIEVAL_WORKSPACE_IDS") or "").strip()
    if not allow:
        return True
    allowed = {part.strip().lower() for part in allow.split(",") if part.strip()}
    return str(workspace_id).lower() in allowed


def normalize_query_tokens(user_query: str | None) -> list[str]:
    return significant_tokens_in_order(
        user_query or "",
        extra_stopwords=HEBREW_STOPWORDS,
        limit=MAX_QUERY_TOKENS,
    )


def build_or_tsquery(tokens: Iterable[str]) -> str | None:
    """OR-join safe tokens for ``to_tsquery('simple', ...)``.

    User text never reaches the operator language. Unsafe tokens are dropped.
    """
    atoms: list[str] = []
    seen: set[str] = set()
    for raw in tokens:
        token = (raw or "").strip()
        if not token or token in seen or not _SAFE_TOKEN_RE.fullmatch(token):
            continue
        seen.add(token)
        atoms.append(token)
        if len(atoms) >= MAX_QUERY_TOKENS:
            break
    if not atoms:
        return None
    return " | ".join(atoms)


@dataclass(frozen=True)
class ReadyFile:
    """Authorized READY file. Text may be empty (indexed-only rows)."""

    id: Any
    created_at: Any
    display_name: str
    original_filename: str
    text: str
    index_status: str
    indexed_chunk_count: int | None
    extraction_status: str
    extraction_truncated: bool


@dataclass(frozen=True)
class ChunkHit:
    chunk_id: Any
    file_id: Any
    page_number: int
    document_chunk_index: int
    text: str
    char_count: int
    rank: float


@dataclass
class RetrievalDiagnostics:
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
    index_chunk_mismatch_ids: tuple[str, ...] = ()


def ready_file_from_row(row: Any, org_id: Any, workspace_id: Any) -> ReadyFile | None:
    if str(row.org_id) != str(org_id) or str(row.workspace_id) != str(workspace_id):
        return None
    if getattr(row, "status", None) != "ready":
        return None
    count = getattr(row, "indexed_chunk_count", None)
    try:
        count_i = int(count) if count is not None else None
    except (TypeError, ValueError):
        count_i = None
    return ReadyFile(
        id=row.id,
        created_at=getattr(row, "created_at", None),
        display_name=str(getattr(row, "display_name", None) or ""),
        original_filename=str(getattr(row, "original_filename", None) or ""),
        text=(getattr(row, "extracted_text", None) or ""),
        index_status=str(getattr(row, "index_status", None) or "not_indexed"),
        indexed_chunk_count=count_i,
        extraction_status=str(getattr(row, "extraction_status", None) or "pending"),
        extraction_truncated=bool(getattr(row, "extraction_truncated", False)),
    )


def named_ready_files(files: list[ReadyFile], user_query: str | None) -> list[ReadyFile]:
    query = user_query or ""
    return [
        f
        for f in files
        if file_is_explicitly_named(query, f.display_name, f.original_filename)
    ]


def claimed_indexed_ids(files: list[ReadyFile]) -> tuple[list[Any], list[Any]]:
    """Split files that claim ``index_status=indexed``.

    ``indexed_chunk_count <= 0`` is an immediate mismatch. Positive/unknown
    counts still require a real chunk-row proof.
    """
    to_prove: list[Any] = []
    mismatch: list[Any] = []
    for f in files:
        if f.index_status != "indexed":
            continue
        if f.indexed_chunk_count is not None and f.indexed_chunk_count <= 0:
            mismatch.append(f.id)
            continue
        to_prove.append(f.id)
    return to_prove, mismatch


async def prove_chunk_rows(
    session,
    *,
    org_id: uuid.UUID,
    workspace_id: uuid.UUID,
    file_ids: list[Any],
) -> dict[Any, int]:
    if not file_ids:
        return {}
    stmt = (
        select(WorkspaceFileChunk.file_id, func.count())
        .where(
            WorkspaceFileChunk.org_id == org_id,
            WorkspaceFileChunk.workspace_id == workspace_id,
            WorkspaceFileChunk.file_id.in_(file_ids),
        )
        .group_by(WorkspaceFileChunk.file_id)
    )
    rows = (await session.execute(stmt)).all()
    return {row[0]: int(row[1]) for row in rows}


def qualify_indexed_ids(
    claimed: list[Any],
    mismatch: list[Any],
    row_counts: dict[Any, int],
) -> tuple[list[Any], list[Any]]:
    qualified: list[Any] = []
    mismatched = list(mismatch)
    counts = {str(key): int(value) for key, value in row_counts.items()}
    for fid in claimed:
        if counts.get(str(fid), 0) > 0:
            qualified.append(fid)
        else:
            mismatched.append(fid)
    return qualified, mismatched


def apply_chunk_budget(hits: list[ChunkHit]) -> list[ChunkHit]:
    """Deterministic caps: K, per-file, chars, no duplicate chunk ids."""
    selected: list[ChunkHit] = []
    seen: set[str] = set()
    per_file: dict[str, int] = {}
    per_file_chars: dict[str, int] = {}
    total = 0
    for hit in hits:
        if hit.page_number is None:
            continue
        key = str(hit.chunk_id)
        if key in seen:
            continue
        fid = str(hit.file_id)
        if per_file.get(fid, 0) >= MAX_CHUNKS_PER_FILE:
            continue
        body = hit.text or ""
        if not body:
            continue
        allowed_file = MAX_CHARS_PER_FILE - per_file_chars.get(fid, 0)
        allowed_total = MAX_EVIDENCE_CHARS - total
        allowed = min(len(body), allowed_file, allowed_total)
        if allowed <= 0:
            continue
        if allowed < len(body):
            body = body[:allowed]
            hit = ChunkHit(
                chunk_id=hit.chunk_id,
                file_id=hit.file_id,
                page_number=hit.page_number,
                document_chunk_index=hit.document_chunk_index,
                text=body,
                char_count=len(body),
                rank=hit.rank,
            )
        seen.add(key)
        per_file[fid] = per_file.get(fid, 0) + 1
        per_file_chars[fid] = per_file_chars.get(fid, 0) + len(body)
        total += len(body)
        selected.append(hit)
        if len(selected) >= MAX_CHUNKS_SELECTED or total >= MAX_EVIDENCE_CHARS:
            break
    return selected


async def search_chunks(
    session,
    *,
    org_id: uuid.UUID,
    workspace_id: uuid.UUID,
    file_ids: list[Any],
    tsquery: str,
    limit: int = MAX_CHUNKS_CONSIDERED,
) -> list[ChunkHit]:
    if not file_ids or not tsquery:
        return []
    tsq = func.to_tsquery("simple", tsquery)
    stmt = (
        select(
            WorkspaceFileChunk.id,
            WorkspaceFileChunk.file_id,
            WorkspaceFileChunk.page_number,
            WorkspaceFileChunk.document_chunk_index,
            WorkspaceFileChunk.text,
            WorkspaceFileChunk.char_count,
            func.ts_rank(WorkspaceFileChunk.text_tsv, tsq).label("rank"),
        )
        .where(
            WorkspaceFileChunk.org_id == org_id,
            WorkspaceFileChunk.workspace_id == workspace_id,
            WorkspaceFileChunk.file_id.in_(file_ids),
            WorkspaceFileChunk.text_tsv.op("@@")(tsq),
        )
        .order_by(
            func.ts_rank(WorkspaceFileChunk.text_tsv, tsq).desc(),
            WorkspaceFileChunk.page_number.asc().nulls_last(),
            WorkspaceFileChunk.document_chunk_index.asc(),
        )
        .limit(limit)
    )
    rows = (await session.execute(stmt)).all()
    hits: list[ChunkHit] = []
    for row in rows:
        page = row.page_number
        if page is None:
            continue
        text = row.text or ""
        hits.append(
            ChunkHit(
                chunk_id=row.id,
                file_id=row.file_id,
                page_number=int(page),
                document_chunk_index=int(row.document_chunk_index),
                text=text,
                char_count=int(row.char_count or len(text)),
                rank=float(row.rank or 0.0),
            )
        )
    return hits


async def search_chunks_bounded(
    session,
    *,
    org_id: uuid.UUID,
    workspace_id: uuid.UUID,
    file_ids: list[Any],
    tsquery: str,
    timeout_s: float = FTS_TIMEOUT_S,
) -> tuple[list[ChunkHit] | None, float, str | None]:
    """Return (hits, latency_ms, error_reason). hits is None on timeout/error."""
    started = time.perf_counter()
    try:
        hits = await asyncio.wait_for(
            search_chunks(
                session,
                org_id=org_id,
                workspace_id=workspace_id,
                file_ids=file_ids,
                tsquery=tsquery,
            ),
            timeout=timeout_s,
        )
        latency = round((time.perf_counter() - started) * 1000.0, 2)
        return hits, latency, None
    except asyncio.TimeoutError:
        latency = round((time.perf_counter() - started) * 1000.0, 2)
        return None, latency, "fts_timeout"
    except Exception:
        latency = round((time.perf_counter() - started) * 1000.0, 2)
        return None, latency, "fts_error"


def log_index_chunk_mismatch(file_ids: Iterable[Any]) -> None:
    ids = tuple(str(fid) for fid in file_ids)
    if not ids:
        return
    log_warning(
        "index_chunk_mismatch",
        subsystem="workspace_files",
        operation="chunk_retrieval",
        outcome="error",
        fallback_reason="index_chunk_mismatch",
        files_searched=len(ids),
    )


def _sanitize_name(name: str | None) -> str:
    cleaned = " ".join(str(name or "file").split())
    return cleaned.replace('"', "'")[:256] or "file"


def _file_extraction_label(file: ReadyFile) -> str:
    if file.extraction_status == "partial" or file.extraction_truncated:
        return "partial"
    if file.extraction_status == "complete":
        return "complete"
    return file.extraction_status or "unknown"


def pack_coverage(files: list[ReadyFile], *, has_chunks: bool, has_legacy: bool) -> str:
    if has_chunks and has_legacy:
        return "mixed"
    if has_legacy and not has_chunks:
        return "legacy"
    if any(f.extraction_status == "partial" or f.extraction_truncated for f in files):
        return "partial"
    if has_chunks and files and all(
        f.extraction_status == "complete" and not f.extraction_truncated for f in files
    ):
        return "complete"
    if has_chunks:
        return "partial"
    return "legacy"


def coverage_warning(coverage: str, retrieval_mode: str) -> str | None:
    if coverage == "partial":
        return (
            "Some pages were not extracted (needs_ocr, failed, skipped, or truncated). "
            "This evidence is not a full-document read."
        )
    if retrieval_mode in {"prefix_fallback", "mixed"} or coverage == "legacy":
        return "This evidence is a clipped prefix of extracted text, not a full-document read."
    return None


def render_chunk_group(file: ReadyFile, chunks: list[ChunkHit]) -> str:
    name = _sanitize_name(file.display_name or file.original_filename)
    extraction = _file_extraction_label(file)
    parts = [
        f'[file name="{name}" file_id="{file.id}" retrieval="chunks" extraction="{extraction}"]'
    ]
    for hit in chunks:
        parts.append(
            f'[chunk page="{hit.page_number}" index="{hit.document_chunk_index}" id="{hit.chunk_id}"]'
        )
        parts.append(hit.text)
        parts.append("[/chunk]")
    parts.append("[/file]")
    return "\n".join(parts)


def render_legacy_group(name: str, text: str) -> str:
    return f'[file name="{name}" retrieval="legacy_prefix"]\n{text}\n[/file]'


def render_evidence_block(
    *,
    retrieval_mode: str,
    coverage: str,
    file_parts: list[str],
) -> str:
    warning = coverage_warning(coverage, retrieval_mode)
    inner = "\n".join(file_parts)
    head = f'<workspace_files retrieval_mode="{retrieval_mode}" coverage="{coverage}">'
    if warning:
        return f"{head}\n{inner}\n<coverage>{warning}</coverage>\n</workspace_files>"
    return f"{head}\n{inner}\n</workspace_files>"


def group_chunks_by_file(hits: list[ChunkHit]) -> list[tuple[Any, list[ChunkHit]]]:
    """Preserve first-seen file order (best rank first)."""
    order: list[Any] = []
    groups: dict[str, list[ChunkHit]] = {}
    keys: dict[str, Any] = {}
    for hit in hits:
        key = str(hit.file_id)
        if key not in groups:
            order.append(key)
            keys[key] = hit.file_id
            groups[key] = []
        groups[key].append(hit)
    for key in order:
        groups[key].sort(key=lambda h: (h.page_number, h.document_chunk_index))
    return [(keys[key], groups[key]) for key in order]


def diagnostics_from_pack(
    *,
    mode: str,
    eligible: int,
    searched_ids: list[Any],
    legacy_count: int,
    considered: int,
    selected: list[ChunkHit],
    evidence_chars: int,
    latency_ms: float | None,
    fallback_reason: str | None,
    coverage: str,
    mismatch_ids: list[Any] | tuple[Any, ...] = (),
) -> RetrievalDiagnostics:
    pages = tuple(h.page_number for h in selected)
    return RetrievalDiagnostics(
        retrieval_mode=mode,
        files_eligible=eligible,
        files_searched=len(searched_ids),
        files_searched_ids=tuple(str(i) for i in searched_ids),
        files_legacy=legacy_count,
        chunks_considered=considered,
        chunks_selected=len(selected),
        evidence_chars=evidence_chars,
        evidence_pages=pages,
        fts_latency_ms=latency_ms,
        fallback_reason=fallback_reason,
        extraction_coverage=coverage,
        index_chunk_mismatch_ids=tuple(str(i) for i in mismatch_ids),
    )
