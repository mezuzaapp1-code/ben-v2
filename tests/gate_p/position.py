"""Evidence-location bands for the 18-page Gate M gold PDF."""
from __future__ import annotations

from typing import Any

EARLY = "EARLY"
MIDDLE = "MIDDLE"
LATE = "LATE"
NONE = "NONE"
SPAN = "SPAN"

EARLY_PAGES = range(1, 7)
MIDDLE_PAGES = range(7, 13)
LATE_PAGES = range(13, 19)


def page_band(page: int | None) -> str:
    if page is None:
        return NONE
    n = int(page)
    if n in EARLY_PAGES:
        return EARLY
    if n in MIDDLE_PAGES:
        return MIDDLE
    if n in LATE_PAGES:
        return LATE
    return NONE


def question_pages(question: dict[str, Any]) -> list[int]:
    pages: list[int] = []
    for span in question.get("decisive_evidence") or []:
        page = span.get("page")
        if page is None:
            continue
        pages.append(int(page))
    return pages


def question_position(question: dict[str, Any]) -> str:
    pages = question_pages(question)
    if not pages:
        return NONE
    bands = {page_band(p) for p in pages}
    bands.discard(NONE)
    if not bands:
        return NONE
    if len(bands) == 1:
        return next(iter(bands))
    return SPAN


def has_band(question: dict[str, Any], band: str) -> bool:
    return any(page_band(p) == band for p in question_pages(question))
