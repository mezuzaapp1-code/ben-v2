"""Internal-only media routes. Polling reads BEN state and never submits work."""
import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from services.media.access import require_pilot
from services.media.contracts import GEMINI_IMAGE_MODEL, ImageRequest, MediaProviderError
from services.media.service import MediaService, public_execution

router = APIRouter(prefix="/api/media", tags=["internal-media"])


def media_service():
    return MediaService()


class GenerateImage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: uuid.UUID
    idempotency_key: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")
    model: Literal["gemini-3.1-flash-image"] = GEMINI_IMAGE_MODEL
    prompt: str = Field(min_length=1, max_length=8000)
    aspect_ratio: Literal["1:1", "16:9", "9:16"] = "1:1"


class Evaluation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    acceptance: Literal["accepted", "rejected", "unrated"]
    rubric_version: str = Field(min_length=1, max_length=128)
    adherence: Literal["pass", "fail", "not_assessed"] = "not_assessed"
    visual_defects: Literal["pass", "fail", "not_assessed"] = "not_assessed"


@router.post("/executions/{execution_id}/evaluation")
async def evaluate(execution_id: uuid.UUID, body: Evaluation, identity=Depends(require_pilot), service=Depends(media_service)):
    # Projectless conversation observations stay with their operational record.
    # Do not invent a workspace to satisfy execution_events' ownership contract.
    observation = {"schema_version": "media-evaluation-v1", **body.model_dump(),
                   "evaluator_id": identity[1], "observed_at": datetime.now(timezone.utc).isoformat(),
                   "training_status": "not_approved", "origin": "operational"}
    await service.repo.evaluate(*identity, execution_id, observation)
    return {"recorded": True, "training_status": "not_approved"}


@router.get("/capabilities")
async def capabilities(identity=Depends(require_pilot)):
    return {"image": True, "models": [GEMINI_IMAGE_MODEL], "internal_only": True,
            "aspect_ratios": ["1:1", "16:9", "9:16"], "image_size": "1K"}


@router.post("/executions", status_code=202)
async def generate(body: GenerateImage, identity=Depends(require_pilot), service=Depends(media_service)):
    try:
        row = await service.create(*identity, body.idempotency_key, body.conversation_id,
                                   ImageRequest(body.model, body.prompt, body.aspect_ratio))
    except MediaProviderError:
        raise HTTPException(422, "Invalid media request") from None
    return public_execution(row)


@router.get("/executions")
async def executions(conversation_id: uuid.UUID, identity=Depends(require_pilot), service=Depends(media_service)):
    rows = await service.repo.read(*identity, conversation=str(conversation_id))
    return {"executions": [public_execution(r) for r in rows]}


@router.get("/executions/{execution_id}")
async def execution(execution_id: uuid.UUID, identity=Depends(require_pilot), service=Depends(media_service)):
    return public_execution(await service.repo.read(*identity, execution=execution_id))


@router.get("/resources/{resource_id}/content")
async def resource(resource_id: uuid.UUID, identity=Depends(require_pilot), service=Depends(media_service)):
    data = await service.resource_bytes(*identity, resource_id)
    return Response(data, media_type="image/png", headers={"Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff", "Content-Disposition": 'inline; filename="ben-image.png"'})
