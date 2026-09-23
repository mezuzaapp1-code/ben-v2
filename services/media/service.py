"""One BEN media lifecycle: single submission, durable reconciliation and publication."""
import asyncio
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta, timezone
import hashlib
import logging
import os
import uuid

from fastapi import HTTPException

from services.media.access import pilot_principals, enabled_media_models
from services.media.accounting import account
from services.media.contracts import ImageRequest, MediaProviderError, MAX_IMAGE_BYTES, BFL_IMAGE_MODEL, MEDIA_PROVIDERS, VEO_VIDEO_MODEL, VideoRequest, MAX_VIDEO_BYTES
from services.media.bfl_image import BflImageAdapter
from services.media.gemini_image import GeminiImageAdapter
from services.media.image_storage import image_path, ingest_png
from services.media.journal import journal_path, load_result, save_result
from services.media.metadata import request_snapshot, request_fingerprint, result_observation
from services.media.repository import MediaRepository
from services.media.veo_video import VeoVideoAdapter
from services.media.video_storage import ingest_mp4, video_path

log = logging.getLogger(__name__)


def public_execution(row):
    # Explicit allowlist: no provider operation reference, prompt or storage path.
    return {"execution_id": str(row["execution_id"]), "status": row["state"],
            "provider": row["provider"], "model": row["model"], "error_code": row["error_code"],
            "created_at": row["created_at"].isoformat(),
            "mime_type": row.get("mime_type"), "operation": row.get("operation", "image_generation"),
            "resource_id": str(row["resource_id"]) if row["state"] == "succeeded" else None,
            "training_status": "not_approved", "usage": row["usage_dimensions"],
            "estimated_cost": str(row["estimated_cost"]) if row["estimated_cost"] is not None else None,
            "pricing_version": row["pricing_version"], "actual_charge": None}


class MediaService:
    def __init__(self, repository=None, adapter=None, bfl_adapter=None, veo_adapter=None):
        self.repo = repository or MediaRepository()
        self.adapter = adapter or GeminiImageAdapter(os.getenv("GOOGLE_API_KEY", ""))
        self.bfl_adapter = bfl_adapter or BflImageAdapter(os.getenv("BFL_API_KEY", ""))
        self.veo_adapter = veo_adapter or VeoVideoAdapter(os.getenv("GOOGLE_API_KEY", ""))

    async def create(self, org, user, key, conversation, request):
        if request.model not in enabled_media_models():
            raise HTTPException(404, "Media unavailable")
        snapshot = request_snapshot(request, conversation_id=str(conversation), workspace_id=None)
        if isinstance(request, VideoRequest):
            source = await self.repo.read(org, user, resource=uuid.UUID(request.source_resource_id))
            if source["state"] != "succeeded" or source["mime_type"] != "image/png":
                raise HTTPException(422, "Ready BEN image required")
            snapshot["input_resource_refs"] = [{"resource_id": str(source["resource_id"]),
                "checksum": source["checksum"], "mime_type": "image/png", "role": "first_frame"}]
        return await self.repo.create(org, user, key, snapshot, request_fingerprint(snapshot))

    async def tick(self, org):
        row = await self.repo.claim(org, uuid.uuid4().hex)
        if not row:
            return False
        now = datetime.now(timezone.utc)
        if row["deadline_at"] <= now:
            await self.repo.change(row, state="expired", error_code="media_deadline_exceeded")
            return True
        if (org, row["created_by"]) not in pilot_principals():
            await self.repo.change(row, state="failed", error_code="media_access_revoked")
            return True
        if MEDIA_PROVIDERS.get(row["model"]) != row["provider"]:
            await self.repo.change(row, state="failed", error_code="media_provider_identity_mismatch")
            return True
        try:
            if row["model"] in (BFL_IMAGE_MODEL, VEO_VIDEO_MODEL):
                resolved = await self._async_result(row)
                if resolved is None:
                    return True
                row, result = resolved
                if row["model"] == VEO_VIDEO_MODEL:
                    # Persist bytes while DB still retains the Ready download reference.
                    # A crash here can resume GET/ingestion, never another submission.
                    await asyncio.to_thread(save_result, row, result)
                row = await self.repo.record_result(row, result, result_observation(result))
                if row is None:
                    return True
                await asyncio.to_thread(save_result, row, result)
            elif row["state"] == "pending":
                row = await self.repo.mark_submitting(row)
                if row is None:
                    return True
                p = row["request_payload"]
                request = ImageRequest(row["model"], p["prompt"], p["parameters"]["aspect_ratio"],
                                       p["parameters"]["image_size"])
                try:
                    result = await self.adapter.generate(request)
                except MediaProviderError as exc:
                    await self.repo.change(row, state="submission_unknown" if exc.submission_unknown else "failed",
                                           error_code=exc.code, next_reconcile_at=row["deadline_at"])
                    return True
                # A cancellation/crash before this journal is durable is uncertain.
                # No code path retries the provider, even if the disk is unavailable.
                row = await self.repo.record_result(row, result, result_observation(result))
                if row is None:
                    return True
                await asyncio.to_thread(save_result, row, result)
            else:
                result = await asyncio.to_thread(load_result, row)
                if result is None:
                    await self.repo.change(row, state="submission_unknown", error_code="media_submission_uncertain",
                                           next_reconcile_at=row["deadline_at"])
                    return True
                row = await self.repo.record_result(row, result, result_observation(result))
                if row is None:
                    return True
            dimensions = result.usage
            if row["model"] == VEO_VIDEO_MODEL:
                params = row["request_payload"]["parameters"]
                stored = await asyncio.to_thread(ingest_mp4, result.data, org_id=org,
                    resource_id=row["resource_id"], aspect_ratio=params["aspect_ratio"],
                    duration_seconds=params["duration_seconds"])
                dimensions = {**dimensions, "video_count": 1, "video_count_source": "observed_output",
                    "duration_seconds": stored.duration_seconds, "duration_source": "decoded_output",
                    "audio_present": stored.audio_present, "requested_audio": "native"}
            else:
                stored = await asyncio.to_thread(ingest_png, result.data, org_id=org, resource_id=row["resource_id"])
            costs = account(dimensions, width=stored.width, height=stored.height, model=row["model"])
            observation = result_observation(result)
            observation.update({"usage_dimensions": costs["usage_dimensions"],
                                "estimated_cost": str(costs["estimated_cost"]) if costs["estimated_cost"] is not None else None,
                                "pricing_version": costs["pricing_version"],
                                "estimated_cost_missing_reason": costs["usage_dimensions"]["cost_estimate"]["missing_reason"]})
            if row["deadline_at"] <= datetime.now(timezone.utc):
                await self.repo.change(row, state="expired", error_code="media_deadline_exceeded", **costs)
                return True
            completed = await self.repo.change(row, state="succeeded", error_code=None,
                provider_operation_ref=result.operation_ref, provider_output=observation,
                storage_key=stored.storage_key, mime_type=stored.mime_type, byte_size=stored.byte_size,
                checksum=stored.checksum, published_at=datetime.now(timezone.utc),
                ingest_attempts=row["ingest_attempts"]+1, **costs)
            if completed:
                # DB success already durably retains usage/provenance. A crash
                # before cleanup leaves private redundant bytes, never another job.
                try:
                    await asyncio.to_thread(journal_path(row).unlink, missing_ok=True)
                except OSError:
                    log.warning("media_journal_cleanup_pending")
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
            await self.repo.change(row, state="failed", error_code="media_destination_missing")
        except (OSError, ValueError, TypeError, KeyError):
            # Retry only private journal ingestion, never submission; bounded by
            # both attempt count and deadline. Raw exception text is never logged.
            attempts = row["ingest_attempts"] + 1
            retry_state = ("running" if row["model"] == VEO_VIDEO_MODEL and row["state"] == "running"
                           and row["provider_state"] == "Ready" else "ingesting")
            await self.repo.change(row, state="failed" if attempts >= 3 else retry_state,
                                   ingest_attempts=attempts, error_code="media_ingestion_failed",
                                   next_reconcile_at=now + timedelta(seconds=10))
        return True

    async def _async_result(self, row):
        """Use the existing row/lease/deadline for one submit and bounded GET reconciliation."""
        now = datetime.now(timezone.utc)
        video = row["model"] == VEO_VIDEO_MODEL
        adapter = self.veo_adapter if video else self.bfl_adapter
        if row["model"] not in enabled_media_models():
            await self.repo.change(row, state="failed", error_code="media_access_revoked")
            return None
        if row["state"] == "pending":
            p = row["request_payload"]
            if video:
                source = p["input_resource_refs"][0]
                try:
                    image = await self.resource_bytes(row["org_id"], row["created_by"], uuid.UUID(source["resource_id"]))
                except HTTPException:
                    await self.repo.change(row, state="failed", error_code="media_source_unavailable")
                    return None
                if hashlib.sha256(image).hexdigest() != source["checksum"]:
                    await self.repo.change(row, state="failed", error_code="media_source_changed")
                    return None
                request = VideoRequest(row["model"], p["prompt"], source["resource_id"],
                    p["parameters"]["aspect_ratio"], p["parameters"]["duration_seconds"], p["parameters"]["resolution"])
            row = await self.repo.mark_submitting(row)
            if row is None:
                return None
            try:
                if video:
                    submitted = await adapter.submit(request, image)
                else:
                    submitted = await adapter.submit(ImageRequest(row["model"], p["prompt"],
                        p["parameters"]["aspect_ratio"], p["parameters"]["image_size"]))
            except MediaProviderError as exc:
                await self.repo.change(row, state="submission_unknown" if exc.submission_unknown else "failed",
                    error_code=exc.code, next_reconcile_at=row["deadline_at"])
                return None
            await self.repo.change(row, state="submitted", provider_operation_ref=submitted.operation_ref,
                provider_state="Pending", usage_dimensions=submitted.usage,
                provider_output={"schema_version": "media-veo-reconcile-v1" if video else "media-bfl-reconcile-v1", "polling_url": submitted.polling_url},
                next_reconcile_at=now + timedelta(seconds=2))
            return None
        if row["state"] not in ("submitted", "running"):
            result = await asyncio.to_thread(load_result, row)
            if result is None:
                await self.repo.change(row, state="submission_unknown", error_code="media_submission_uncertain",
                    next_reconcile_at=row["deadline_at"])
                return None
            return row, result
        if row["poll_attempts"] >= 180:
            await self.repo.change(row, state="expired", error_code="media_poll_limit")
            return None
        private = dict(row["provider_output"] or {})
        try:
            if row["provider_state"] != "Ready":
                status, sample, usage = await adapter.poll(row["provider_operation_ref"], private.get("polling_url"))
                if sample:
                    private["sample"] = sample
                row = await self.repo.record_poll(row, status=status, output=private, usage=usage)
                if row is None:
                    return None
                if status in ("Error", "Failed", "Request Moderated", "Content Moderated", "Task not found"):
                    await self.repo.change(row, state="failed", error_code={"Request Moderated": "media_request_moderated",
                        "Content Moderated": "media_content_moderated", "Task not found": "media_provider_not_found"}.get(status, "media_provider_failed"))
                    return None
                if status != "Ready":
                    await self.repo.change(row, next_reconcile_at=now + timedelta(seconds=10 if video else 5), error_code=None)
                    return None
            result = await adapter.download(row["provider_operation_ref"], private.get("sample"), row["usage_dimensions"])
            return row, result
        except MediaProviderError as exc:
            if video and exc.code == "media_provider_expired":
                await self.repo.change(row, state="expired", error_code=exc.code)
                return None
            # GET-only retry of a known operation/immutable output. Never another POST.
            downloading = row["provider_state"] == "Ready"
            attempts = row["ingest_attempts"] + 1 if downloading else row["ingest_attempts"]
            polls = row["poll_attempts"] + (0 if downloading else 1)
            await self.repo.change(row, state="failed" if attempts >= 3 else row["state"],
                error_code=exc.code, ingest_attempts=attempts, poll_attempts=polls,
                next_reconcile_at=now + timedelta(seconds=10))
            return None

    async def resource_bytes(self, org, user, resource):
        row = await self.repo.read(org, user, resource=resource)
        if row["state"] != "succeeded":
            raise HTTPException(404, "Media not ready")
        video = row["mime_type"] == "video/mp4"
        key, path = (video_path if video else image_path)(org, row["resource_id"])
        if key != row["storage_key"]:
            raise HTTPException(503, "Media unavailable")
        def read():
            with path.open("rb") as handle:
                return handle.read((MAX_VIDEO_BYTES if video else MAX_IMAGE_BYTES) + 1)
        try:
            data = await asyncio.to_thread(read)
        except OSError:
            raise HTTPException(503, "Media unavailable") from None
        if len(data) != row["byte_size"] or hashlib.sha256(data).hexdigest() != row["checksum"]:
            raise HTTPException(503, "Media unavailable")
        # Recheck authorization after disk I/O, including concurrent deletion.
        await self.repo.read(org, user, resource=resource)
        return data


@asynccontextmanager
async def media_worker():
    async def run():
        service = MediaService()
        while True:
            for org in sorted({org for org, _ in pilot_principals()}, key=str):
                try:
                    await service.tick(org)
                except Exception:
                    # Never log exception objects, SQL parameters, prompts or credentials.
                    log.warning("media_reconcile_unavailable")
            await asyncio.sleep(2)
    task = asyncio.create_task(run()) if pilot_principals() else None
    try:
        yield
    finally:
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
