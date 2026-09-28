"""Internal-only media routes. Polling reads BEN state and never submits work."""
import uuid
import time
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

from services.media.access import require_pilot, require_narration_pilot, enabled_image_models, enabled_video_models
from services.media.narration import create_narration
from services.ops.structured_log import log_info
from services.media.contracts import GEMINI_IMAGE_MODEL, KLING_VIDEO_MODEL, ImageRequest, VideoRequest, MediaProviderError
from services.media.service import MediaService, public_execution
from routers.creative_lab import router as creative_lab_router, lab_enabled

router = APIRouter(prefix="/api/media", tags=["internal-media"])
router.include_router(creative_lab_router)


def media_service():
    return MediaService()


def narration_service(identity=Depends(require_narration_pilot)):
    return MediaService(local_narration=True)


class ExecutionResponse(BaseModel):
    """Closed top-level contract retaining existing public client metadata."""
    model_config = ConfigDict(extra="forbid")
    execution_id: uuid.UUID
    status: Literal["pending", "submitting", "submitted", "running", "ingesting",
                    "succeeded", "submission_unknown", "failed", "expired"]
    resource_id: uuid.UUID | None
    error_code: str | None
    provider: str
    model: str
    created_at: datetime
    mime_type: str | None
    operation: str
    training_status: Literal["not_approved"]
    usage: dict
    estimated_cost: str | None
    pricing_version: str | None
    actual_charge: None = None

    @model_validator(mode="after")
    def hide_unfinished_resource(self):
        if self.status != "succeeded":
            self.resource_id = None
        return self


class ReplaceNarration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: uuid.UUID
    idempotency_key: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")
    video_resource_id: uuid.UUID
    workspace_id: uuid.UUID
    music_file_id: uuid.UUID
    narration_file_id: uuid.UUID


@router.post("/narration-replacements", status_code=202, response_model=ExecutionResponse)
async def replace_narration(body: ReplaceNarration, identity=Depends(require_narration_pilot),
                            service=Depends(narration_service)):
    started, outcome = time.monotonic(), "failed"
    try:
        row = await create_narration(service, *identity, body.idempotency_key,
            body.conversation_id, video_resource_id=body.video_resource_id,
            workspace_id=body.workspace_id, music_file_id=body.music_file_id,
            narration_file_id=body.narration_file_id)
        outcome = "accepted"
        return public_execution(row)
    except ValueError:
        outcome = "rejected"
        raise HTTPException(422, detail={"code": "INVALID_NARRATION_INPUT",
                                        "message": "Invalid narration inputs"}) from None
    except HTTPException:
        outcome = "rejected"
        raise
    finally:
        log_info("Narration admission", subsystem="media", operation="narration_admission",
                 outcome=outcome, duration_ms=int((time.monotonic()-started)*1000))


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


@router.get("/executions/{execution_id}", response_model=ExecutionResponse)
async def execution(execution_id: uuid.UUID, identity=Depends(require_pilot), service=Depends(media_service)):
    return public_execution(await service.repo.read(*identity, execution=execution_id))


@router.get("/resources/{resource_id}/content")
async def resource(resource_id: uuid.UUID, identity=Depends(require_pilot), service=Depends(media_service)):
    data = await service.resource_bytes(*identity, resource_id)
    row = await service.repo.read(*identity, resource=resource_id)
    video = row["mime_type"] == "video/mp4"
    return Response(data, media_type="video/mp4" if video else "image/png", headers={"Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff", "Content-Disposition": 'inline; filename="ben-video.mp4"' if video else 'inline; filename="ben-image.png"'})
