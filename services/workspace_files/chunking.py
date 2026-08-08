"""Deterministic, page-aware chunking for Document Intelligence (Gate 2).

BEN owns the canonical chunk contract. Chunking is deterministic, reproducible,
provider-independent, and cost-free (no model/LLM, no embeddings). Identical
input + chunking_version always yields identical chunks.

Only pages with usable extracted text produce chunks; empty / needs_ocr / failed
/ skipped pages produce none (their coverage truth lives on WorkspaceFilePage).
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from services.workspace_files.document_parser import PAGE_EXTRACTED, StructuredDocument

# Bump when the chunking algorithm changes such that persisted chunks must be
# regenerated. Chunk identity uniqueness is (file_id, chunking_version,
# document_chunk_index).
CHUNKING_VERSION = 1

# Bounded, deterministic character window. No overlap in V1 (not needed until a
# retrieval/embedding gate justifies it).
CHUNK_MAX_CHARS = int(os.getenv("BEN_DOC_CHUNK_MAX_CHARS", "1500"))


@dataclass(frozen=True)
class Chunk:
    page_number: int
    page_chunk_index: int
    document_chunk_index: int
    text: str
    char_count: int


def _split_page(text: str, max_chars: int) -> list[str]:
    """Deterministic fixed-window split of a single page's text.

    Prefers to break on the last newline/space within the window for cleaner
    boundaries, but always makes forward progress, so output is fully reproducible.
    """
    body = (text or "").strip()
    if not body:
        return []
    if max_chars <= 0:
        return [body]
    out: list[str] = []
    i, n = 0, len(body)
    while i < n:
        end = min(i + max_chars, n)
        if end < n:
            window = body[i:end]
            brk = max(window.rfind("\n"), window.rfind(" "))
            # Only honor the break if it leaves a non-trivial slice (avoid tiny/no progress).
            if brk >= max_chars // 2:
                end = i + brk + 1
        piece = body[i:end].strip()
        if piece:
            out.append(piece)
        i = end
    return out


def chunk_structured_document(doc: StructuredDocument, *, max_chars: int = CHUNK_MAX_CHARS) -> list[Chunk]:
    """Produce deterministic page-aware chunks in stable global order."""
    chunks: list[Chunk] = []
    doc_idx = 0
    for page in doc.pages:
        if page.status != PAGE_EXTRACTED:
            continue
        pieces = _split_page(page.text, max_chars)
        for pci, piece in enumerate(pieces):
            chunks.append(
                Chunk(
                    page_number=page.page_number,
                    page_chunk_index=pci,
                    document_chunk_index=doc_idx,
                    text=piece,
                    char_count=len(piece),
                )
            )
            doc_idx += 1
    return chunks
