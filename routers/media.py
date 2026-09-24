"""Internal-only media routes. Polling reads BEN state and never submits work."""
import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from services.media.access import require_pilot, enabled_image_models, enabled_video_models
from services.media.contracts import GEMINI_IMAGE_MODEL, KLING_VIDEO_MODEL, ImageRequest, VideoRequest, MediaProviderError
from services.media.service import MediaService, public_execution
from routers.creative_lab import router as creative_lab_router, lab_enabled

router = APIRouter(prefix="/api/media", tags=["internal-media"])
router.include_router(creative_lab_router)


def media_service():
    return MediaService()


class GenerateImage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: uuid.UUID
    idempotency_key: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")
    model: Literal["gemini-3.1-flash-image", "flux-2-pro"] = GEMINI_IMAGE_MODEL
    prompt: str = Field(min_length=1, max_length=8000)
    aspect_ratio: Literal["1:1", "16:9", "9:16"] = "1:1"


class GenerateVideo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: uuid.UUID
    idempotency_key: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")
    model: Literal["veo-3.1-fast-generate-preview", "fal-ai/kling-video/o3/standard/image-to-video"]
    prompt: str = Field(min_length=1, max_length=2000)
    source_resource_id: uuid.UUID
    aspect_ratio: Literal["16:9", "9:16"] = "16:9"
    duration_seconds: Literal[3, 4] = 4
    resolution: Literal["720p"] = "720p"


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
    return {"image": True, "models": enabled_image_models(), "internal_only": True,
            "creative_lab": lab_enabled(),
            "aspect_ratios": ["1:1", "16:9", "9:16"], "image_size": "1K",
            "video_models": enabled_video_models(), "video": bool(enabled_video_models()),
            "video_model_parameters": {model: {"duration_seconds": 3 if model == KLING_VIDEO_MODEL else 4,
                "resolution": "720p", "audio": "off" if model == KLING_VIDEO_MODEL else "native",
                "source_aspect_ratio_required": model == KLING_VIDEO_MODEL} for model in enabled_video_models()},
            "video_parameters": {"operation": "image_to_video", "duration_seconds": 4,
                                 "resolution": "720p", "aspect_ratios": ["16:9", "9:16"], "audio": "native"}}


@router.post("/executions", status_code=202)
async def generate(body: GenerateImage | GenerateVideo, identity=Depends(require_pilot), service=Depends(media_service)):
    try:
        request = (VideoRequest(body.model, body.prompt, str(body.source_resource_id), body.aspect_ratio,
                               body.duration_seconds, body.resolution) if isinstance(body, GenerateVideo)
                   else ImageRequest(body.model, body.prompt, body.aspect_ratio))
        row = await service.create(*identity, body.idempotency_key, body.conversation_id, request)
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
    row = await service.repo.read(*identity, resource=resource_id)
    video = row["mime_type"] == "video/mp4"
    return Response(data, media_type="video/mp4" if video else "image/png", headers={"Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff", "Content-Disposition": 'inline; filename="ben-video.mp4"' if video else 'inline; filename="ben-image.png"'})
