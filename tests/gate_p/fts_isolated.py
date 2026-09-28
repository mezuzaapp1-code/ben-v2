"""Isolated Gate 4A lexical FTS measurement.

Does not set production flags, does not enable chunk retrieval globally, and
does not open a live Postgres session. Uses the committed tokenizer, OR-query
atoms, chunker, and apply_chunk_budget.
"""
from __future__ import annotations

import re
import time
import uuid
from typing import Any

from services.workspace_files.chunk_retriever import (
    MAX_CHUNKS_CONSIDERED,
    ChunkHit,
    ReadyFile,
    apply_chunk_budget,
    build_or_tsquery,
    normalize_query_tokens,
    render_chunk_group,
    render_evidence_block,
)
from services.workspace_files.chunking import CHUNKING_VERSION, CHUNK_MAX_CHARS, chunk_structured_document
from services.workspace_files.document_parser import (
    EXTRACTION_VERSION,
    PAGE_EXTRACTED,
    PageResult,
    StructuredDocument,
)
from tests.gate_m.gold_document import GOLD_FILENAME
from tests.gate_m.gold_questions import QUESTIONS
from tests.gate_m.measure import decisive_in_source
from tests.gate_p.score import score_extractive_row, summarize_rows

FILE_ID = uuid.UUID("cccccccc-cccc-cccc-cccc-ccccccccccc1")


def structured_from_pages(pages: list[Any]) -> StructuredDocument:
    results = []
    for p in pages:
        text = getattr(p, "text", None)
        if text is None and isinstance(p, dict):
            text = p.get("text") or ""
            number = int(p.get("page") or 0)
            status = p.get("status") or PAGE_EXTRACTED
        else:
            number = int(getattr(p, "page", 0) or getattr(p, "page_number", 0))
            status = getattr(p, "status", PAGE_EXTRACTED) or PAGE_EXTRACTED
        results.append(
            PageResult(
                page_number=number,
                status=status if status else PAGE_EXTRACTED,
                text=text or "",
                char_count=len(text or ""),
            )
        )
    return StructuredDocument(
        source_page_count=len(results),
        pages=tuple(results),
        extraction_version=EXTRACTION_VERSION,
        parser_id="gate_p_isolated",
        parser_version="1",
    )


def _token_rank(text: str, tokens: list[str]) -> float:
    """Whole-token overlap, closer to Postgres ``simple`` FTS than substring match."""
    if not tokens:
        return 0.0
    blob = (text or "").lower()
    hits = 0
    for tok in tokens:
        atom = (tok or "").strip().lower()
        if not atom:
            continue
        if re.search(rf"(?<![0-9a-z\u0590-\u05ff]){re.escape(atom)}(?![0-9a-z\u0590-\u05ff])", blob):
            hits += 1
    return float(hits) / float(len(tokens))


def search_chunks_lexical(chunks, *, tokens: list[str], file_id=FILE_ID) -> list[ChunkHit]:
    tsquery = build_or_tsquery(tokens)
    if not tsquery:
        return []
    atoms = [part.strip() for part in tsquery.split("|")]
    hits: list[ChunkHit] = []
    for chunk in chunks:
        rank = _token_rank(chunk.text, atoms)
        if rank <= 0:
            continue
        hits.append(
            ChunkHit(
                chunk_id=f"c{chunk.document_chunk_index}",
                file_id=file_id,
                page_number=chunk.page_number,
                document_chunk_index=chunk.document_chunk_index,
                text=chunk.text,
                char_count=chunk.char_count,
                rank=rank,
            )
        )
    hits.sort(key=lambda h: (-h.rank, h.page_number, h.document_chunk_index))
    return hits[:MAX_CHUNKS_CONSIDERED]


def retrieve_for_question(chunks, question_text: str) -> tuple[str, list[ChunkHit], list[str], float]:
    t0 = time.perf_counter()
    tokens = normalize_query_tokens(question_text)
    considered = search_chunks_lexical(chunks, tokens=tokens)
    selected = apply_chunk_budget(considered)
    ready = ReadyFile(
        id=FILE_ID,
        created_at=1,
        display_name=GOLD_FILENAME,
        original_filename=GOLD_FILENAME,
        text="",
        index_status="indexed",
        indexed_chunk_count=len(chunks),
        extraction_status="complete",
        extraction_truncated=False,
    )
    block = ""
    if selected:
        file_part = render_chunk_group(ready, selected)
        block = render_evidence_block(
            retrieval_mode="chunks",
            coverage="complete",
            file_parts=[file_part],
        )
    latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)
    return block, selected, tokens, latency_ms


def run_isolated_fts(extracted: dict[str, Any]) -> dict[str, Any]:
    doc = structured_from_pages(extracted["pages"])
    chunks = chunk_structured_document(doc)
    processing_ok = bool(extracted.get("legacy_status") == "ready" and chunks)
    rows: list[dict[str, Any]] = []
    for question in QUESTIONS:
        block, selected, tokens, latency_ms = retrieve_for_question(chunks, question["question"])
        in_source = decisive_in_source(question, extracted["extracted_text"], extracted["pages"])
        row = score_extractive_row(
            question,
            injected=block,
            in_source=in_source,
            processing_ok=processing_ok,
            latency_ms=latency_ms,
            extra={
                "retrieval_mode": "chunks_isolated_lexical",
                "query_tokens": tokens,
                "chunks_considered": len(search_chunks_lexical(chunks, tokens=tokens)),
                "chunks_selected": len(selected),
                "selected_pages": [h.page_number for h in selected],
                "chunking_version": CHUNKING_VERSION,
                "chunk_max_chars": CHUNK_MAX_CHARS,
                "note": (
                    "Isolated lexical OR-token simulation of Gate 4A. "
                    "Production BEN_WORKSPACE_CHUNK_RETRIEVAL remains unset/off. "
                    "Not live Postgres ts_rank."
                ),
            },
        )
        rows.append(row)
    summary = summarize_rows(rows, methodology="extractive_from_isolated_fts_chunks")
    summary.update(
        {
            "chunk_count": len(chunks),
            "chunking_version": CHUNKING_VERSION,
            "chunk_max_chars": CHUNK_MAX_CHARS,
            "postgres_fts": False,
            "production_flag": "off",
        }
    )
    return {
        "mode": "BEN_EXISTING_FTS",
        "status": "MEASURED_ISOLATED",
        "summary": summary,
        "rows": rows,
    }
