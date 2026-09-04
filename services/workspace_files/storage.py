"""Local authenticated file storage for Workspace File Library."""
from __future__ import annotations

import hashlib
import os
import re
import uuid
from pathlib import Path

from services.ops.structured_log import log_error
from services.workspace_files.types import MAX_UPLOAD_BYTES, STREAM_CHUNK_BYTES

_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._\- ()\[\]]+")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_DISPLAY_FILENAME_MAX = 512
_DURABLE_FLAG = "BEN_REQUIRE_DURABLE_FILE_ROOT"
_DURABLE_ROOT_ENV = "BEN_PROJECTS_DATA_DIR"
_DURABLE_MOUNT_ENV = "BEN_DURABLE_FILE_MOUNT"
_PLATFORM_MOUNT_ENV = "RAILWAY_VOLUME_MOUNT_PATH"


class DurableStorageUnavailable(Exception):
    """Durable file root is required but missing, misconfigured, or unverified."""


def _flag_enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def require_durable_file_root() -> bool:
    return _flag_enabled(_DURABLE_FLAG)


def configured_projects_root() -> Path | None:
    raw = (os.getenv(_DURABLE_ROOT_ENV) or "").strip()
    return Path(raw) if raw else None


def durable_mount_path() -> Path | None:
    """Optional mount constraint. Generic env wins; platform mount is accepted if set."""
    raw = (os.getenv(_DURABLE_MOUNT_ENV) or os.getenv(_PLATFORM_MOUNT_ENV) or "").strip()
    return Path(raw) if raw else None


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _fail_durable(reason: str, **fields: object) -> None:
    log_error(
        "durable file storage unavailable",
        subsystem="workspace_files",
        category="durable_storage",
        operation="assert_durable_root",
        outcome="error",
        reason=reason,
        **fields,
    )
    raise DurableStorageUnavailable(reason)


def assert_durable_root_ready() -> Path | None:
    """Fail closed when durable storage is required but the configured root is not usable.

    When BEN_REQUIRE_DURABLE_FILE_ROOT is off, this is a no-op (local/test default).
    When on:
    - BEN_PROJECTS_DATA_DIR must be set
    - files_root() must resolve under that directory
    - if BEN_DURABLE_FILE_MOUNT or RAILWAY_VOLUME_MOUNT_PATH is set, that mount
      must already exist and the configured root must resolve under it
    - otherwise the configured root itself must already exist (do not create an
      ephemeral stand-in)
    """
    if not require_durable_file_root():
        return configured_projects_root()

    configured = configured_projects_root()
    if configured is None:
        _fail_durable(
            f"{_DURABLE_ROOT_ENV} is required when {_DURABLE_FLAG} is enabled",
        )

    mount = durable_mount_path()
    if mount is not None:
        mount_res = mount.resolve()
        if not mount_res.is_dir():
            _fail_durable(
                "durable file mount is not an existing directory",
                mount=str(mount),
            )
        configured_res = configured.resolve()
        if configured_res != mount_res and not _is_under(configured_res, mount_res):
            _fail_durable(
                f"{_DURABLE_ROOT_ENV} is not under the durable mount",
                configured_root=str(configured_res),
                mount=str(mount_res),
            )
        try:
            configured_res.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            _fail_durable(
                "cannot create projects root on durable mount",
                configured_root=str(configured_res),
                error_type=type(exc).__name__,
            )
    else:
        configured_res = configured.resolve()
        if not configured_res.is_dir():
            _fail_durable(
                f"{_DURABLE_ROOT_ENV} must be an existing directory when "
                f"{_DURABLE_FLAG} is enabled",
                configured_root=str(configured_res),
            )

    expected_files_root = (configured_res / "_workspace_files").resolve()
    if expected_files_root != configured_res and not _is_under(expected_files_root, configured_res):
        _fail_durable(
            "files_root is not under the configured durable root",
            files_root=str(expected_files_root),
            configured_root=str(configured_res),
        )
    try:
        expected_files_root.mkdir(parents=True, exist_ok=True)
        probe = expected_files_root / f".durable_write_probe_{os.getpid()}_{uuid.uuid4().hex[:8]}"
        probe.write_bytes(b"ok")
        probe.unlink()
    except OSError as exc:
        _fail_durable(
            "durable files_root is not writable",
            files_root=str(expected_files_root),
            error_type=type(exc).__name__,
        )
    return configured_res


def sanitize_filename(name: str | None) -> str:
    """ASCII-safe basename for disk / storage_key only. Not a display name."""
    raw = (name or "upload.bin").strip().replace("\\", "/").split("/")[-1]
    cleaned = _SAFE_FILENAME_RE.sub("_", raw).strip("._")
    return cleaned[:240] or "upload.bin"


def preserve_original_filename(name: str | None) -> str:
    """User-visible basename: Unicode preserved, path and controls stripped.

    The 512-character cap keeps the original suffix so type validation still
    sees ``.pdf`` (and similar) after a long stem is shortened.
    """
    raw = (name or "").strip().replace("\\", "/").split("/")[-1]
    cleaned = _CONTROL_RE.sub("", raw).strip()
    if not cleaned:
        return "upload.bin"
    if len(cleaned) > _DISPLAY_FILENAME_MAX:
        suffix = Path(cleaned).suffix
        if suffix and len(suffix) < _DISPLAY_FILENAME_MAX:
            stem = cleaned[: -len(suffix)]
            cleaned = stem[: _DISPLAY_FILENAME_MAX - len(suffix)] + suffix
        else:
            cleaned = cleaned[:_DISPLAY_FILENAME_MAX]
        cleaned = cleaned.strip()
    return cleaned or "upload.bin"


def files_root() -> Path:
    from services.project_tools import projects_root

    assert_durable_root_ready()
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


def _fsync_file_and_dir(handle, directory: Path) -> None:
    handle.flush()
    os.fsync(handle.fileno())
    try:
        dir_fd = os.open(str(directory), os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        # Directory fsync is best-effort; file fsync already completed.
        pass


def _verify_persisted_file(dest: Path, *, expected_size: int, expected_checksum: str) -> None:
    if not dest.exists() or not dest.is_file():
        raise DurableStorageUnavailable("persisted file bytes were not found after write")
    actual_size = dest.stat().st_size
    if actual_size != expected_size:
        raise DurableStorageUnavailable(
            f"persisted file size mismatch: expected {expected_size}, got {actual_size}"
        )
    hasher = hashlib.sha256()
    with dest.open("rb") as handle:
        while True:
            chunk = handle.read(STREAM_CHUNK_BYTES)
            if not chunk:
                break
            hasher.update(chunk)
    actual_checksum = hasher.hexdigest()
    if actual_checksum != expected_checksum:
        raise DurableStorageUnavailable("persisted file checksum mismatch after write")


async def write_upload(
    *,
    org_id: uuid.UUID,
    workspace_id: uuid.UUID,
    file_id: uuid.UUID,
    filename: str,
    upload,
) -> tuple[str, int, str]:
    """Stream upload to disk. Returns (storage_key, byte_size, sha256)."""
    assert_durable_root_ready()
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
            _fsync_file_and_dir(handle, dest.parent)
        if total == 0:
            raise ValueError("empty upload")
        checksum = hasher.hexdigest()
        try:
            _verify_persisted_file(dest, expected_size=total, expected_checksum=checksum)
        except DurableStorageUnavailable as exc:
            log_error(
                "durable file persistence verification failed",
                subsystem="workspace_files",
                category="durable_storage",
                operation="write_upload",
                outcome="error",
                reason=str(exc),
            )
            raise
        completed = True
        return key, total, checksum
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
