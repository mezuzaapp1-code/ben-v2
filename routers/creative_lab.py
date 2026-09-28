"""Internal Gate 1 previews from existing authorized WorkspaceFile originals."""
import asyncio
import base64
import hashlib
import os
import uuid

from fastapi import APIRouter, Depends, HTTPException, Response

from services.media.access import require_pilot
from services.media.creative_lab import canonicalize, LabInputError, MAX_SOURCE_BYTES
from services.workspace_files.service import open_file_bytes, get_file

router = APIRouter(prefix="/creative-lab", tags=["internal-creative-lab"])


def lab_enabled():
    return os.getenv("BEN_CREATIVE_LAB_ENABLED") == "1"


@router.get("/canonical")
async def canonical_preview(workspace_id: uuid.UUID, file_id: uuid.UUID,
                            response: Response, identity=Depends(require_pilot)):
    if not lab_enabled():
        raise HTTPException(404, "Creative Lab unavailable")
    org, _user = identity
    row = await get_file(org_id=org, workspace_id=workspace_id, file_id=file_id)
    path, _mime, _name = await open_file_bytes(org_id=org, workspace_id=workspace_id, file_id=file_id)

    def read():
        with path.open("rb") as handle:
            return handle.read(MAX_SOURCE_BYTES + 1)

    try:
        original = await asyncio.to_thread(read)
        if len(original) > MAX_SOURCE_BYTES:
            raise LabInputError("lab_source_size_limit")
        if len(original) != row["byte_size"] or hashlib.sha256(original).hexdigest() != row["checksum"]:
            raise LabInputError("lab_source_identity_mismatch")
        canonical = await asyncio.to_thread(canonicalize, original)
    except LabInputError as exc:
        raise HTTPException(422, str(exc)) from None
    except OSError:
        raise HTTPException(404, "Source unavailable") from None
    # Recheck ownership/deletion and checksum after processing, before returning bytes.
    current = await get_file(org_id=org, workspace_id=workspace_id, file_id=file_id)
    if current["checksum"] != row["checksum"]:
        raise HTTPException(409, "Source changed")
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return {"source": {"workspace_id": str(workspace_id), "file_id": str(file_id)},
            "metadata": canonical.metadata,
            "canonical_png_base64": base64.b64encode(canonical.png).decode("ascii")}
