"""Workspace File Library V1 — domain-isolated from News.

See ``domain_boundary`` for the non-negotiable News ↔ Files separation.
Canonical persistence: ``WorkspaceFile`` / ``ben.workspace_files`` only.
Never reuse ``SourceDocumentVersion`` or other News-owned tables for uploads.
"""

from services.workspace_files.service import (
    delete_file,
    get_file,
    list_files,
    open_file_bytes,
    process_file,
    upload_file,
)

__all__ = [
    "delete_file",
    "get_file",
    "list_files",
    "open_file_bytes",
    "process_file",
    "upload_file",
]
