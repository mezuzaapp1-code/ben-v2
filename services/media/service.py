"""One-shot Gemini execution and bounded durable recovery; no chat integration."""
import asyncio
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta, timezone
import hashlib
import logging
import os
import uuid

from fastapi import HTTPException

from services.media.access import pilot_principals, enabled_image_models
from services.media.accounting import account
from services.media.contracts import ImageRequest, MediaProviderError, MAX_IMAGE_BYTES, BFL_IMAGE_MODEL, IMAGE_PROVIDERS
from services.media.bfl_image import BflImageAdapter
from services.media.gemini_image import GeminiImageAdapter
from services.media.image_storage import image_path, ingest_png
from services.media.journal import journal_path, load_result, save_result
from services.media.metadata import request_snapshot, request_fingerprint, result_observation
from services.media.repository import MediaRepository

log = logging.getLogger(__name__)


def public_execution(row):
    # Explicit allowlist: no provider operation reference, prompt or storage path.
    return {"execution_id": str(row["execution_id"]), "status": row["state"],
            "provider": row["provider"], "model": row["model"], "error_code": row["error_code"],
            "created_at": row["created_at"].isoformat(),
            "resource_id": str(row["resource_id"]) if row["state"] == "succeeded" else None,
            "training_status": "not_approved", "usage": row["usage_dimensions"],
            "estimated_cost": str(row["estimated_cost"]) if row["estimated_cost"] is not None else None,
            "pricing_version": row["pricing_version"], "actual_charge": None}


class MediaService:
    def __init__(self, repository=None, adapter=None, bfl_adapter=None):
        self.repo = repository or MediaRepository()
        self.adapter = adapter or GeminiImageAdapter(os.getenv("GOOGLE_API_KEY", ""))
        self.bfl_adapter = bfl_adapter or BflImageAdapter(os.getenv("BFL_API_KEY", ""))

    async def create(self, org, user, key, conversation, request):
        if request.model not in enabled_image_models():
            raise HTTPException(404, "Media unavailable")
        snapshot = request_snapshot(request, conversation_id=str(conversation), workspace_id=None)
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
        if IMAGE_PROVIDERS.get(row["model"]) != row["provider"]:
            await self.repo.change(row, state="failed", error_code="media_provider_identity_mismatch")
            return True
        try:
            if row["model"] == BFL_IMAGE_MODEL:
                resolved = await self._bfl_result(row)
                if resolved is None:
                    return True
                row, result = resolved
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
            stored = await asyncio.to_thread(ingest_png, result.data, org_id=org, resource_id=row["resource_id"])
            costs = account(result.usage, width=stored.width, height=stored.height, model=row["model"])
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
            await self.repo.change(row, state="failed" if attempts >= 3 else "ingesting",
                                   ingest_attempts=attempts, error_code="media_ingestion_failed",
                                   next_reconcile_at=now + timedelta(seconds=10))
        return True

    async def _bfl_result(self, row):
        """Use the existing row/lease/deadline for one submit and bounded GET reconciliation."""
        now = datetime.now(timezone.utc)
        if row["model"] not in enabled_image_models():
            await self.repo.change(row, state="failed", error_code="media_access_revoked")
            return None
        if row["state"] == "pending":
            row = await self.repo.mark_submitting(row)
            if row is None:
                return None
            p = row["request_payload"]
            try:
                submitted = await self.bfl_adapter.submit(ImageRequest(row["model"], p["prompt"],
                    p["parameters"]["aspect_ratio"], p["parameters"]["image_size"]))
            except MediaProviderError as exc:
                await self.repo.change(row, state="submission_unknown" if exc.submission_unknown else "failed",
                    error_code=exc.code, next_reconcile_at=row["deadline_at"])
                return None
            await self.repo.change(row, state="submitted", provider_operation_ref=submitted.operation_ref,
                provider_state="Pending", usage_dimensions=submitted.usage,
                provider_output={"schema_version": "media-bfl-reconcile-v1", "polling_url": submitted.polling_url},
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
                status, sample, usage = await self.bfl_adapter.poll(row["provider_operation_ref"], private.get("polling_url"))
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
                    await self.repo.change(row, next_reconcile_at=now + timedelta(seconds=5), error_code=None)
                    return None
            result = await self.bfl_adapter.download(row["provider_operation_ref"], private.get("sample"), row["usage_dimensions"])
            return row, result
        except MediaProviderError as exc:
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
        key, path = image_path(org, row["resource_id"])
        if key != row["storage_key"]:
            raise HTTPException(503, "Media unavailable")
        def read():
            with path.open("rb") as handle:
                return handle.read(MAX_IMAGE_BYTES + 1)
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
