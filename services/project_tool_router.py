"""Conditional LLM tool injection — project workspace threads only."""
from __future__ import annotations

import uuid
from typing import Any

from database.thread_store import get_thread_session_type, resolve_thread_db_path
from services.project_tools import project_tools_for_provider


def is_project_workspace_thread(thread_id: uuid.UUID | str | None) -> bool:
    if thread_id is None:
        return False
    return get_thread_session_type(str(thread_id)) == "project_setup"


def resolve_project_thread_db_path(thread_id: uuid.UUID | str) -> str:
    """Expose portable SQLite resolution for project_setup threads."""
    return str(resolve_thread_db_path(str(thread_id)))


def conditional_project_tools(
    *,
    thread_id: uuid.UUID | str | None,
    provider_id: str | None = "openai",
) -> list[dict[str, Any]] | None:
    """Return filesystem tool schemas for project onboarding, or None for lean regular chat."""
    if not is_project_workspace_thread(thread_id):
        return None
    return project_tools_for_provider(provider_id or "openai")
