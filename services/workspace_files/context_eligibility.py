"""Context Eligibility — consume Resource Access decisions. No I/O.

Does not retrieve, persist, or independently authorize. Security denial
always wins. Ordinary conversation answers are not source-bound.
"""
from __future__ import annotations

from typing import Any

from services.workspace_files.resource_access import AccessDecision
from services.workspace_files.source_policy import FILE_INITIAL_READ_EVENT


def source_bound_message_eligible(decoded: dict[str, Any], access: AccessDecision | None) -> bool:
    """Initial Read stays source-bound. Unknown source identity fails closed."""
    event = str((decoded or {}).get("source_event") or "").strip()
    if event != FILE_INITIAL_READ_EVENT:
        return True
    file_id = str((decoded or {}).get("source_file_id") or "").strip()
    if not file_id:
        return False
    return access is not None and access.allowed
