"""Gate 0 — Legacy Knowledge Containment.

Disables externally reachable Legacy Knowledge access and prompt injection
without deleting, moving, or assigning ownership of existing data.
"""
from __future__ import annotations

from fastapi import HTTPException

LEGACY_KNOWLEDGE_CONTAINED_CODE = "legacy_knowledge_contained"
LEGACY_KNOWLEDGE_CONTAINED_STATUS = 410
LEGACY_KNOWLEDGE_CONTAINED_MESSAGE = (
    "Legacy Knowledge is unavailable. Existing data was not deleted."
)


def legacy_knowledge_contained_detail() -> dict[str, str]:
    return {
        "code": LEGACY_KNOWLEDGE_CONTAINED_CODE,
        "message": LEGACY_KNOWLEDGE_CONTAINED_MESSAGE,
    }


def raise_legacy_knowledge_contained() -> None:
    """Fail closed: no CRUD, list, upload, or attention against legacy stores."""
    raise HTTPException(
        status_code=LEGACY_KNOWLEDGE_CONTAINED_STATUS,
        detail=legacy_knowledge_contained_detail(),
    )


def contained_few_shot_block(message: str, context_id: str | None = None) -> str:
    """Prompt-assembly boundary: never read the global knowledge corpus."""
    del message, context_id
    return ""


def contained_portable_project_context(
    project_slug: str,
    query: str,
    *,
    limit_per_head: int = 3,
) -> str:
    """Prompt-assembly boundary: never inject slug-only project knowledge."""
    del project_slug, query, limit_per_head
    return ""
