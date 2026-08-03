"""File Library V1 — allowed types and limits."""
from __future__ import annotations

MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB
STREAM_CHUNK_BYTES = 1024 * 1024

# Executable / dangerous extensions — reject even if MIME is spoofed.
REJECTED_EXTENSIONS = frozenset(
    {
        ".exe",
        ".bat",
        ".cmd",
        ".com",
        ".msi",
        ".dll",
        ".scr",
        ".ps1",
        ".sh",
        ".bash",
        ".vbs",
        ".js",
        ".jar",
        ".apk",
        ".dmg",
        ".app",
    }
)

# extension -> (media_type, processable)
SUPPORTED_TYPES: dict[str, tuple[str, bool]] = {
    ".pdf": ("application/pdf", True),
    ".docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        True,
    ),
    ".doc": ("application/msword", False),  # store only — no safe parser in V1
    ".txt": ("text/plain", True),
    ".md": ("text/markdown", True),
    ".markdown": ("text/markdown", True),
    ".csv": ("text/csv", True),
    ".xlsx": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        True,
    ),
    ".pptx": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        False,
    ),  # metadata/store; no pptx dep in V1
    ".png": ("image/png", False),
    ".jpg": ("image/jpeg", False),
    ".jpeg": ("image/jpeg", False),
    ".gif": ("image/gif", False),
    ".webp": ("image/webp", False),
    ".json": ("application/json", True),
}

STATUSES = ("uploaded", "queued", "processing", "ready", "failed")
