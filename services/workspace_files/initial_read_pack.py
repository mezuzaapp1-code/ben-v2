"""Bounded representative chunk pack for Initial Read.

Deterministic. No LLM, no OCR, no second extract. Provenance preserved.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

from services.workspace_files.chunk_retriever import MAX_CHUNKS_SELECTED, MAX_EVIDENCE_CHARS
from services.workspace_files.file_resolver import significant_tokens_in_order

_HEADING_NUM = re.compile(r"^\s*\d+(?:\.\d+){0,5}\s+\S")
_MULTI_SPACE = re.compile(r" {2,}")
_DEDUP_JACCARD = 0.7
MAX_PACK_CHUNKS = MAX_CHUNKS_SELECTED  # 8
MAX_PACK_CHARS = MAX_EVIDENCE_CHARS  # 6000
MAX_CHUNKS_PER_PAGE = 2


@dataclass(frozen=True)
class PackChunk:
    file_id: Any
    chunk_id: Any
    page_number: int
    document_chunk_index: int
    page_chunk_index: int
    text: str
    char_count: int
    page_char_count: int = 0


def select_representative_chunks(
    chunks: Iterable[PackChunk],
    *,
    max_chunks: int = MAX_PACK_CHUNKS,
    max_chars: int = MAX_PACK_CHARS,
) -> list[PackChunk]:
    """Score + greedy select with first/last, page diversity, and dedup."""
    eligible = [
        c
        for c in chunks
        if c is not None and (c.text or "").strip() and int(c.char_count or 0) > 0
    ]
    if not eligible:
        return []
    eligible.sort(key=lambda c: (int(c.document_chunk_index), int(c.page_number), str(c.chunk_id)))
    first = eligible[0]
    last_page = max(int(c.page_number) for c in eligible)
    last_page_chunks = [c for c in eligible if int(c.page_number) == last_page]
    last = last_page_chunks[-1] if last_page_chunks else eligible[-1]

    scored = sorted(eligible, key=lambda c: (-_chunk_score(c), c.document_chunk_index))
    chosen: list[PackChunk] = []
    chosen_ids: set[str] = set()
    per_page: dict[int, int] = {}
    total = 0

    def _try_add(chunk: PackChunk) -> bool:
        nonlocal total
        key = str(chunk.chunk_id)
        if key in chosen_ids:
            return False
        body = (chunk.text or "").strip()
        if not body:
            return False
        if per_page.get(int(chunk.page_number), 0) >= MAX_CHUNKS_PER_PAGE:
            return False
        if _too_similar(chunk, chosen):
            return False
        take = min(len(body), max_chars - total)
        if take <= 0:
            return False
        if take < len(body):
            body = body[:take]
            chunk = PackChunk(
                file_id=chunk.file_id,
                chunk_id=chunk.chunk_id,
                page_number=chunk.page_number,
                document_chunk_index=chunk.document_chunk_index,
                page_chunk_index=chunk.page_chunk_index,
                text=body,
                char_count=len(body),
                page_char_count=chunk.page_char_count,
            )
        chosen.append(chunk)
        chosen_ids.add(key)
        per_page[int(chunk.page_number)] = per_page.get(int(chunk.page_number), 0) + 1
        total += len(chunk.text)
        return True

    _try_add(first)
    if last is not first:
        _try_add(last)
    for chunk in scored:
        if len(chosen) >= max_chunks or total >= max_chars:
            break
        _try_add(chunk)

    chosen.sort(key=lambda c: (int(c.document_chunk_index), int(c.page_number)))
    return chosen


def render_pack_evidence(
    *,
    display_name: str,
    file_id: Any,
    page_count: int | None,
    extraction_status: str,
    pages_extracted: int | None,
    pages_needs_ocr: int | None,
    chunks: list[PackChunk],
) -> str:
    name = " ".join(str(display_name or "file").split()).replace('"', "'")[:256] or "file"
    meta_bits = [f'name="{name}"', f'file_id="{file_id}"']
    if page_count is not None:
        meta_bits.append(f'pages="{int(page_count)}"')
    if extraction_status:
        meta_bits.append(f'extraction="{extraction_status}"')
    if pages_extracted is not None:
        meta_bits.append(f'pages_extracted="{int(pages_extracted)}"')
    if pages_needs_ocr is not None:
        meta_bits.append(f'pages_needs_ocr="{int(pages_needs_ocr)}"')
    parts = [f"[file {' '.join(meta_bits)}]"]
    if pages_needs_ocr:
        parts.append(
            "Coverage note: some pages were not extracted as text (needs_ocr). "
            "Do not invent visual or geometric content from those pages."
        )
    for hit in chunks:
        parts.append(
            f'[chunk page="{hit.page_number}" index="{hit.document_chunk_index}" id="{hit.chunk_id}"]'
        )
        parts.append(hit.text)
        parts.append("[/chunk]")
    parts.append("[/file]")
    return "\n".join(parts)


def _chunk_score(chunk: PackChunk) -> int:
    text = chunk.text or ""
    first_line = text.splitlines()[0].strip() if text.strip() else ""
    score = 0
    if chunk.page_chunk_index == 0:
        score += 40
    if _HEADING_NUM.match(first_line):
        score += 80
    if first_line and len(first_line) <= 80:
        letters = [c for c in first_line if c.isalpha()]
        if letters and sum(1 for c in letters if c.isupper()) / len(letters) >= 0.6:
            score += 50
        elif first_line == first_line.title() and len(first_line.split()) <= 12:
            score += 25
    if "|" in text or "\t" in text or _MULTI_SPACE.search(text):
        score += 45
    digits = sum(1 for c in text if c.isdigit())
    if text and digits / max(len(text), 1) >= 0.12:
        score += 20
    tokens = significant_tokens_in_order(text, limit=80)
    density = len(tokens) / max(len(text), 1)
    score += int(min(density * 8000, 60))
    if chunk.page_char_count:
        score += min(int(chunk.page_char_count / 200), 30)
    if len(text.strip()) < 40:
        score -= 40
    return score


def _too_similar(chunk: PackChunk, chosen: list[PackChunk]) -> bool:
    tokens = set(significant_tokens_in_order(chunk.text, limit=40))
    if not tokens:
        return False
    for other in chosen:
        other_tokens = set(significant_tokens_in_order(other.text, limit=40))
        if not other_tokens:
            continue
        inter = len(tokens & other_tokens)
        union = len(tokens | other_tokens)
        if union and inter / union >= _DEDUP_JACCARD:
            return True
    return False
