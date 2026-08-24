"""Authorize and load current-turn composer images for Vision.

File lifecycle (upload → stored → processing → ready/failed) is independent.
This path must not wait for extraction, indexing, Gate 3D, Gate 4A, OCR, or Ready.
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import HTTPException

from services.message_format import parse_user_turn_parts
from services.providers.vision_input import (
    ProviderUserPart,
    UserTextPart,
    VisionImage,
    is_vision_media_type,
    normalize_vision_media_type,
)
from services.workspace_files.service import open_file_bytes

VISION_TURN_ERROR_CODE = "vision_turn_error"
VISION_MAX_BYTES = 20 * 1024 * 1024


class VisionTurnError(Exception):
    """User-visible current-turn Vision failure. Never a silent text fallback."""

    def __init__(self, message: str, *, code: str = VISION_TURN_ERROR_CODE):
        super().__init__(message)
        self.message = message
        self.code = code


def user_turn_has_image_refs(content: str) -> bool:
    parts = parse_user_turn_parts(content)
    if not parts:
        return False
    return any(part.get("type") == "file_ref" for part in parts)


def user_turn_file_ref_ids(content: str) -> list[str]:
    """Ordered file_id values from the current user turn. Client paths are ignored."""
    parts = parse_user_turn_parts(content)
    if not parts:
        return []
    out: list[str] = []
    for part in parts:
        if part.get("type") != "file_ref":
            continue
        fid = str(part.get("file_id") or "").strip()
        if fid:
            out.append(fid)
    return out


def build_provider_user_content(
    raw_message: str,
    images: list[VisionImage],
) -> list[ProviderUserPart]:
    """Preserve composer part order: text / large_paste / image."""
    queues: dict[str, list[VisionImage]] = {}
    for image in images:
        queues.setdefault(image.file_id, []).append(image)

    parts = parse_user_turn_parts(raw_message)
    out: list[ProviderUserPart] = []
    if parts is None:
        text = str(raw_message or "")
        if text:
            out.append(UserTextPart(text))
        out.extend(images)
        return out

    for part in parts:
        kind = part.get("type")
        if kind in {"text", "large_paste"}:
            text = str(part.get("text") or "")
            if text:
                out.append(UserTextPart(text))
            continue
        if kind != "file_ref":
            continue
        fid = str(part.get("file_id") or "").strip()
        queued = queues.get(fid) or []
        if queued:
            out.append(queued.pop(0))
    for leftover in queues.values():
        out.extend(leftover)
    return out


def _parse_file_uuid(raw: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(raw).strip())
    except (TypeError, ValueError) as exc:
        raise VisionTurnError(
            "This image reference is not valid. Re-attach the file and try again."
        ) from exc


async def load_current_turn_vision_images(
    *,
    org_id: uuid.UUID,
    workspace_id: uuid.UUID | None,
    file_ids: list[str],
) -> list[VisionImage]:
    """Resolve file_id → authorized WorkspaceFile → bytes. Never trust client paths."""
    if not file_ids:
        return []
    if workspace_id is None:
        raise VisionTurnError(
            "Select an active workspace before sending an image."
        )

    loaded: list[VisionImage] = []
    for raw_id in file_ids:
        file_id = _parse_file_uuid(raw_id)
        try:
            path, media_type, _display_name = await open_file_bytes(
                org_id=org_id,
                workspace_id=workspace_id,
                file_id=file_id,
            )
        except HTTPException as exc:
            detail = str(getattr(exc, "detail", "") or "")
            if "bytes missing" in detail.lower():
                raise VisionTurnError(
                    "Image bytes are missing. Re-attach the file and try again."
                ) from exc
            raise VisionTurnError(
                "This image is not available in the current workspace."
            ) from exc

        normalized = normalize_vision_media_type(media_type)
        if normalized is None or not is_vision_media_type(media_type):
            raise VisionTurnError(
                "Current-turn Vision only accepts PNG, JPEG, GIF, or WEBP images."
            )

        try:
            data = path.read_bytes()
        except OSError as exc:
            raise VisionTurnError(
                "Image bytes are missing. Re-attach the file and try again."
            ) from exc

        if not data:
            raise VisionTurnError(
                "Image bytes are missing. Re-attach the file and try again."
            )
        if len(data) > VISION_MAX_BYTES:
            raise VisionTurnError(
                "This image is too large for current-turn Vision (max 20 MB)."
            )

        loaded.append(
            VisionImage(
                file_id=str(file_id),
                media_type=normalized,
                data=data,
            )
        )
    return loaded


def vision_payload_leak_haystack(payload: Any) -> str:
    """JSON-ish string used by tests to prove internals never leave BEN."""
    return str(payload)
