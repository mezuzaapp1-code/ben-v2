"""Project data repositories — local file ingestion; global channel registry in system_main.db."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from services.global_service_store import (
    connect_global_channel,
    get_global_channel,
    list_global_channels,
    metadata_has_sensitive_tokens,
    scrub_sensitive_metadata,
    toggle_global_channel,
)
from services.knowledge_store import UploadReader, init_portable_context_store, resolve_project_db_path

SourceType = Literal["local", "google_drive", "external_library", "gmail", "sovereign_sonar"]
RepositoryStatus = Literal["active", "disconnected"]

SOURCE_TYPES: frozenset[str] = frozenset(
    {"local", "google_drive", "external_library", "gmail", "sovereign_sonar"}
)
REPOSITORY_STATUSES: frozenset[str] = frozenset({"active", "disconnected"})

_MAX_REPOSITORY_UPLOAD_BYTES = 500 * 1024 * 1024
_STREAM_CHUNK_BYTES = 1024 * 1024
_SAFE_FILENAME_RE = re.compile(r"[^a-zA-Z0-9._\-]+")
_DEFAULT_CATALOG_KEY_RE = re.compile(r"[^a-z0-9]+")


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _connect(project_slug: str) -> sqlite3.Connection:
    path = resolve_project_db_path(project_slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _default_catalog_key(source_type: str, name: str) -> str:
    slug = _DEFAULT_CATALOG_KEY_RE.sub("-", (name or "channel").lower()).strip("-")[:48] or "channel"
    token = str(source_type or "integration").strip().lower()
    return f"repo-{token}-{slug}"


def _ensure_catalog_key(source_type: str, name: str, metadata: dict[str, Any]) -> dict[str, Any]:
    payload = dict(metadata or {})
    if not str(payload.get("catalog_key") or "").strip():
        payload["catalog_key"] = _default_catalog_key(source_type, name)
    return payload


def init_project_repositories(project_slug: str) -> None:
    """Ensure per-project repository file tables exist inside project_context.db."""
    init_portable_context_store(project_slug)
    with _connect(project_slug) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS repository_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                repository_id INTEGER NOT NULL,
                filename TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                absolute_path TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                content_type TEXT,
                sha256 TEXT NOT NULL,
                uploaded_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'ready' CHECK (status IN ('ready', 'removed'))
            );

            CREATE INDEX IF NOT EXISTS idx_repository_files_repo
                ON repository_files(repository_id, uploaded_at DESC);
            """
        )
        conn.commit()


def _validate_source_type(source_type: str) -> SourceType:
    token = str(source_type or "").strip().lower()
    if token not in SOURCE_TYPES:
        raise ValueError(f"source_type must be one of: {', '.join(sorted(SOURCE_TYPES))}")
    return token  # type: ignore[return-value]


def _sanitize_filename(raw: str | None) -> str:
    name = Path(str(raw or "upload.bin")).name.strip()
    cleaned = _SAFE_FILENAME_RE.sub("_", name).strip("._")
    return cleaned[:240] or "upload.bin"


def _unique_destination(directory: Path, filename: str) -> Path:
    candidate = directory / filename
    if not candidate.exists():
        return candidate
    stem = Path(filename).stem[:200] or "upload"
    suffix = Path(filename).suffix[:20]
    token = uuid.uuid4().hex[:8]
    return directory / f"{stem}_{token}{suffix}"


def resolve_repository_storage_dir(project_slug: str, repository_id: int) -> Path:
    from services.project_tools import projects_root, slugify_project_name

    slug = slugify_project_name(project_slug)
    root = projects_root().resolve()
    storage_dir = (root / slug / "repositories" / str(int(repository_id))).resolve()
    if root not in storage_dir.parents:
        raise ValueError("invalid repository storage path")
    storage_dir.mkdir(parents=True, exist_ok=True)
    return storage_dir


def _row_to_repository_file(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "repository_id": int(row["repository_id"]),
        "filename": str(row["filename"]),
        "relative_path": str(row["relative_path"]),
        "size_bytes": int(row["size_bytes"]),
        "content_type": str(row["content_type"] or ""),
        "sha256": str(row["sha256"]),
        "uploaded_at": str(row["uploaded_at"]),
        "updated_at": str(row["updated_at"]),
        "status": str(row["status"]),
    }


def get_repository(org_id: str, repository_id: int) -> dict[str, Any] | None:
    return get_global_channel(org_id, repository_id)


def list_repositories(org_id: str) -> list[dict[str, Any]]:
    return list_global_channels(org_id)


def connect_repository(
    org_id: str,
    project_slug: str,
    *,
    name: str,
    source_type: str,
    source_metadata: dict[str, Any],
    feature_flags: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Activate a global org-scoped channel; local storage dirs are created per project.
    """
    init_project_repositories(project_slug)
    _validate_source_type(source_type)
    metadata = _ensure_catalog_key(source_type, name, source_metadata)
    channel = connect_global_channel(
        org_id,
        name=name,
        source_type=source_type,
        source_metadata=metadata,
        feature_flags=feature_flags,
    )
    catalog_key = str(channel.get("catalog_key") or metadata.get("catalog_key") or "")
    if channel["source_type"] == "local" or catalog_key == "repo-local":
        resolve_repository_storage_dir(project_slug, int(channel["id"]))
    return channel


def toggle_repository(org_id: str, repository_id: int) -> dict[str, Any]:
    """Physical kill-switch at the global layer — disconnect and scrub credentials."""
    return toggle_global_channel(org_id, repository_id)


def register_repository_file_metadata(
    project_slug: str,
    *,
    repository_id: int,
    filename: str,
    relative_path: str,
    absolute_path: str,
    size_bytes: int,
    content_type: str | None,
    sha256: str,
) -> dict[str, Any]:
    init_project_repositories(project_slug)
    now = _now_iso()
    with _connect(project_slug) as conn:
        cur = conn.execute(
            """
            INSERT INTO repository_files (
                repository_id, filename, relative_path, absolute_path,
                size_bytes, content_type, sha256, uploaded_at, updated_at, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'ready')
            """,
            (
                int(repository_id),
                filename[:512],
                relative_path,
                absolute_path,
                int(size_bytes),
                (content_type or "")[:128] or None,
                sha256,
                now,
                now,
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM repository_files WHERE id = ?",
            (int(cur.lastrowid),),
        ).fetchone()
    if row is None:
        raise RuntimeError("repository file metadata persist failed")
    return _row_to_repository_file(row)


async def stream_repository_upload(
    org_id: str,
    project_slug: str,
    repository_id: int,
    upload: UploadReader,
) -> dict[str, Any]:
    """Chunked asynchronous ingestion for large repository files (<=500MB)."""
    init_project_repositories(project_slug)
    repo = get_global_channel(org_id, repository_id)
    if repo is None:
        raise ValueError("repository not found")
    if repo["status"] != "active":
        raise ValueError("repository is disconnected")

    storage_dir = resolve_repository_storage_dir(project_slug, repository_id)
    safe_name = _sanitize_filename(upload.filename)
    dest_path = _unique_destination(storage_dir, safe_name)
    hasher = hashlib.sha256()
    total_bytes = 0
    completed = False

    try:
        with dest_path.open("wb") as handle:
            while chunk := await upload.read(_STREAM_CHUNK_BYTES):
                if not chunk:
                    continue
                next_total = total_bytes + len(chunk)
                if next_total > _MAX_REPOSITORY_UPLOAD_BYTES:
                    raise ValueError("upload exceeds 500MB limit")
                handle.write(chunk)
                hasher.update(chunk)
                total_bytes = next_total

        if total_bytes == 0:
            raise ValueError("empty upload")

        slug_dir = storage_dir.parent.parent
        relative_path = dest_path.relative_to(slug_dir).as_posix()
        record = register_repository_file_metadata(
            project_slug,
            repository_id=repository_id,
            filename=safe_name,
            relative_path=relative_path,
            absolute_path=str(dest_path.resolve()),
            size_bytes=total_bytes,
            content_type=upload.content_type,
            sha256=hasher.hexdigest(),
        )
        completed = True
        return record
    finally:
        if not completed and dest_path.exists():
            dest_path.unlink(missing_ok=True)


__all__ = [
    "SOURCE_TYPES",
    "connect_repository",
    "get_repository",
    "init_project_repositories",
    "list_repositories",
    "metadata_has_sensitive_tokens",
    "resolve_repository_storage_dir",
    "scrub_sensitive_metadata",
    "stream_repository_upload",
    "toggle_repository",
]
