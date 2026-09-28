"""Private immutable MP4 attempt paths; execution/resource identity stays in BEN.

Storage directories are application-controlled. No untrusted arbitrary path is
accepted. This module does not scan/adopt orphan files or delete completed files.
"""
import uuid

from services.media.image_storage import _resolved
from services.workspace_files.storage import files_root


def attempt_path(org, resource, execution, attempt):
    if not all(isinstance(value, uuid.UUID) for value in (org, resource, execution, attempt)):
        raise ValueError("trusted UUID identities required")
    key = f"_media/{org}/{resource}/attempts/{execution}/{attempt}.mp4"
    root = _resolved(files_root())
    lexical = root / key
    resolved = _resolved(lexical)
    # Reject symlink/junction redirection, including into another tenant in root.
    if resolved != lexical or not resolved.is_relative_to(root):
        raise ValueError("invalid media attempt path")
    return key, resolved


def resolve_attempt_key(org, resource, execution, key):
    if not isinstance(key, str):
        raise ValueError("invalid media attempt key")
    try:
        attempt = uuid.UUID(key.rsplit("/", 1)[-1].removesuffix(".mp4"))
    except (ValueError, AttributeError):
        raise ValueError("invalid media attempt key") from None
    expected, path = attempt_path(org, resource, execution, attempt)
    if key != expected:
        raise ValueError("invalid media attempt key")
    return path
