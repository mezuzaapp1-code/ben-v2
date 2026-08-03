"""Local authenticated file storage for Workspace File Library."""
from __future__ import annotations

import hashlib
import re
import uuid
from pathlib import Path

from services.workspace_files.types import MAX_UPLOAD_BYTES, STREAM_CHUNK_BYTES

_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._\- ()\[\]]+")


def sanitize_filename(name: str | None) -> str:
    raw = (name or "upload.bin").strip().replace("\\", "/").split("/")[-1]
    cleaned = _SAFE_FILENAME_RE.sub("_", raw).strip("._")
    return cleaned[:240] or "upload.bin"


def files_root() -> Path:
    from services.project_tools import projects_root

    root = (projects_root().resolve() / "_workspace_files").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def resolve_workspace_dir(org_id: uuid.UUID, workspace_id: uuid.UUID) -> Path:
    root = files_root()
    path = (root / str(org_id) / str(workspace_id)).resolve()
    if root not in path.parents and path != root:
        # ensure under root
        if not str(path).startswith(str(root)):
            raise ValueError("invalid storage path")
    path.mkdir(parents=True, exist_ok=True)
    return path


def storage_key_for(org_id: uuid.UUID, workspace_id: uuid.UUID, file_id: uuid.UUID, filename: str) -> str:
    safe = sanitize_filename(filename)
    return f"{org_id}/{workspace_id}/{file_id}/{safe}"


def absolute_path_for_key(storage_key: str) -> Path:
    root = files_root()
    path = (root / storage_key).resolve()
    if not str(path).startswith(str(root)):
        raise ValueError("invalid storage key")
    return path


async def write_upload(
    *,
    org_id: uuid.UUID,
    workspace_id: uuid.UUID,
    file_id: uuid.UUID,
    filename: str,
    upload,
) -> tuple[str, int, str]:
    """Stream upload to disk. Returns (storage_key, byte_size, sha256)."""
    key = storage_key_for(org_id, workspace_id, file_id, filename)
    dest = absolute_path_for_key(key)
    dest.parent.mkdir(parents=True, exist_ok=True)
    hasher = hashlib.sha256()
    total = 0
    completed = False
    try:
        with dest.open("wb") as handle:
            while True:
                chunk = await upload.read(STREAM_CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise ValueError(f"upload exceeds {MAX_UPLOAD_BYTES} byte limit")
                handle.write(chunk)
                hasher.update(chunk)
        if total == 0:
            raise ValueError("empty upload")
        completed = True
        return key, total, hasher.hexdigest()
    finally:
        if not completed and dest.exists():
            dest.unlink(missing_ok=True)


def delete_storage(storage_key: str) -> None:
    path = absolute_path_for_key(storage_key)
    if path.exists():
        path.unlink(missing_ok=True)
    parent = path.parent
    if parent.exists() and not any(parent.iterdir()):
        parent.rmdir()
