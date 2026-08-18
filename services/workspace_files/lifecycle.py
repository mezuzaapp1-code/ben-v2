"""Truthful workspace-file processing stages for the API/UI contract.

Does not invent numeric processing percentages. READY is only returned when
the legacy upload ``status`` is already ``ready``.
"""
from __future__ import annotations

from typing import Any

STAGE_QUEUED = "queued"
STAGE_EXTRACTING = "extracting"
STAGE_INDEXING = "indexing"
STAGE_READY = "ready"
STAGE_FAILED = "failed"

_FAILED = frozenset({"failed"})
_READY = "ready"
_EXTRACTING = "extracting"
_INDEXING = "indexing"
_COMPLETE = frozenset({"complete", "partial"})
_PROCESSING = "processing"


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


def derive_processing_stage_from_fields(
    *,
    status: Any = None,
    extraction_status: Any = None,
    index_status: Any = None,
) -> str:
    """Map persisted lifecycle fields to a single UI stage.

    Order is fail-closed for READY: never report ready unless ``status`` is ready.
    """
    st = _norm(status)
    extraction = _norm(extraction_status) or "pending"
    index = _norm(index_status) or "not_indexed"

    if st in _FAILED or extraction in _FAILED or index in _FAILED:
        return STAGE_FAILED
    if st == _READY:
        return STAGE_READY
    if extraction == _EXTRACTING:
        return STAGE_EXTRACTING
    if index == _INDEXING:
        return STAGE_INDEXING
    if extraction in _COMPLETE and index != "indexed":
        return STAGE_INDEXING
    if st == _PROCESSING:
        return STAGE_EXTRACTING
    return STAGE_QUEUED


def derive_processing_stage(row: Any) -> str:
    return derive_processing_stage_from_fields(
        status=getattr(row, "status", None),
        extraction_status=getattr(row, "extraction_status", None),
        index_status=getattr(row, "index_status", None),
    )


def page_progress_from_fields(
    *,
    page_count: Any = None,
    pages_extracted: Any = None,
) -> tuple[int, int] | None:
    """Return ``(x, y)`` only when both sides are real, finite, and usable."""
    try:
        y = int(page_count)
        x = int(pages_extracted)
    except (TypeError, ValueError):
        return None
    if y <= 0 or x < 0:
        return None
    return x, y
