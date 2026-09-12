"""Document-position buckets for Gate P.

Uses the gold decisive page, not model output. EARLY/MIDDLE/LATE split the
18-page gold file into thirds so prefix cutoff (~page 8) is visible.
"""
from __future__ import annotations

from typing import Any

EARLY_MAX_PAGE = 6
MIDDLE_MAX_PAGE = 12


def decisive_pages(question: dict[str, Any]) -> list[int]:
    pages: list[int] = []
    for span in question.get("decisive_evidence") or []:
        page = span.get("page")
        if isinstance(page, int) and page >= 1:
            pages.append(page)
    return pages


def position_bucket(question: dict[str, Any]) -> str:
    pages = decisive_pages(question)
    if not pages:
        return "NONE"
    latest = max(pages)
    if latest <= EARLY_MAX_PAGE:
        return "EARLY"
    if latest <= MIDDLE_MAX_PAGE:
        return "MIDDLE"
    return "LATE"
