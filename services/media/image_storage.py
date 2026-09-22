"""Immutable PNG byte ingestion; media_executions owns identity/publication.

Reuses BEN's configured durable root, fsync and persisted-checksum primitives.
No WorkspaceFile rows, indexing, attachment mutation or public delivery here.
"""
from __future__ import annotations

import hashlib
import io
import os
import tempfile
import uuid
import warnings
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from services.media.contracts import MAX_IMAGE_BYTES
from services.workspace_files.storage import files_root, _fsync_file_and_dir, _verify_persisted_file


@dataclass(frozen=True)
class StoredImage:
    storage_key: str
    byte_size: int
    checksum: str
    width: int
    height: int
    mime_type: str = "image/png"


def _resolved(path: Path) -> Path:
    resolved = str(path.resolve())
    # Windows can retain an extended-path prefix when a path is created during
    # resolve(). Normalize both sides before the containment check, not bypass it.
    if os.name == "nt":
        if resolved.startswith("\\\\?\\UNC\\"):
            resolved = "\\\\" + resolved[8:]
        elif resolved.startswith("\\\\?\\"):
            resolved = resolved[4:]
    return Path(resolved)


def image_path(org_id: uuid.UUID, resource_id: uuid.UUID) -> tuple[str, Path]:
    if not isinstance(org_id, uuid.UUID) or not isinstance(resource_id, uuid.UUID):
        raise ValueError("trusted UUID identities required")
    root = _resolved(files_root())
    key = f"_media/{org_id}/{resource_id}/output.png"
    dest = _resolved(root / key)
    if not dest.is_relative_to(root):
        raise ValueError("invalid media storage path")
    return key, dest


def ingest_png(data: bytes, *, org_id: uuid.UUID, resource_id: uuid.UUID) -> StoredImage:
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ValueError("invalid media size")
    valid = False
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                width, height = image.size
                if image.format != "PNG" or width * height > 20_000_000 or getattr(image, "n_frames", 1) != 1:
                    raise ValueError("unsupported media image")
                image.verify()
            with Image.open(io.BytesIO(data)) as image:
                image.load()
            valid = True
    except (ValueError, OSError, SyntaxError, UnidentifiedImageError,
            Image.DecompressionBombWarning, Image.DecompressionBombError):
        pass
    if not valid:
        raise ValueError("invalid media image")
    key, dest = image_path(org_id, resource_id)
    checksum = hashlib.sha256(data).hexdigest()
    dest.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=dest.parent, prefix=".ingest-", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            _fsync_file_and_dir(handle, dest.parent)
        # A hard link publishes complete bytes atomically without replacing a
        # winner from another ingestion attempt. Same resource/different bytes
        # fails the checksum check below; retry never regenerates or overwrites.
        try:
            os.link(temporary, dest)
        except FileExistsError:
            pass
        _verify_persisted_file(dest, expected_size=len(data), expected_checksum=checksum)
        # Windows fsync requires a writable descriptor; r+b does not truncate.
        with dest.open("r+b") as handle:
            _fsync_file_and_dir(handle, dest.parent)
        return StoredImage(key, len(data), checksum, width, height)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
