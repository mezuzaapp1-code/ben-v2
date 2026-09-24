"""Versioned media JSON envelopes; no schema or ownership changes.

Request snapshots belong in media_executions.request_payload. Observations belong
in execution_events when its existing workspace ownership contract applies.
Projectless executions must not invent a workspace just to emit an event.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime

from services.inference.measurement_contracts import ExecutionEvent
from services.media.contracts import ImageRequest, ImageResult, MEDIA_PROVIDERS, BFL_IMAGE_MODEL, VEO_VIDEO_MODEL, KLING_VIDEO_MODEL

VERSION = "media-v1"


def request_snapshot(request: ImageRequest, *, conversation_id: str | None,
                     workspace_id: uuid.UUID | None, experiment_id: uuid.UUID | None = None) -> dict:
    """Caller must authorize these selectors before creating an execution.

    Rights are server-created and cannot be upgraded through client metadata.
    This is a persistence envelope, never a public API response.
    """
    if not workspace_id and (not conversation_id or not conversation_id.strip()):
        raise ValueError("media destination required")
    normalized = request.normalized()
    return {
        "schema_version": VERSION, "normalization_version": "media-json-v1",
        **normalized,
        "destination": {"conversation_id": conversation_id,
                        "workspace_id": str(workspace_id) if workspace_id else None},
        "input_resource_refs": [],
        "experiment_id": str(experiment_id) if experiment_id else None,
        "provenance": {"output_origin": "provider_generated",
                       "model_snapshot": None, "snapshot_missing_reason": "not_reported",
                       **({"upstream_provider": "kling"} if request.model == KLING_VIDEO_MODEL else {}),
                       "terms_reference": "https://fal.ai/legal/api-services" if request.model == KLING_VIDEO_MODEL else "https://bfl.ai/legal/flux-api-service-terms" if request.model == BFL_IMAGE_MODEL else "https://ai.google.dev/gemini-api/terms",
                       "terms_reviewed_on": None if request.model in (BFL_IMAGE_MODEL, KLING_VIDEO_MODEL) else "2026-09-22"},
        "rights": {"schema_version": "media-rights-v1", "training_status": "not_approved",
                   "fine_tuning_status": "not_approved", "distillation_status": "not_approved",
                   "evaluation_status": "requires_applicable_clearance",
                   "routing_research_status": "requires_applicable_clearance",
                   "clearance_reference": None},
    }


def request_fingerprint(snapshot: dict) -> str:
    """Hash intent, destination and cohort, excluding mutable policy observations."""
    intent = {key: snapshot[key] for key in (
        "normalization_version", "provider", "model", "operation", "prompt",
        "parameters", "destination", "input_resource_refs", "experiment_id",
    )}
    return hashlib.sha256(json.dumps(intent, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def result_observation(result: ImageResult) -> dict:
    """No prompt, credential, operation ID, URL, bytes or storage key in telemetry."""
    return {"schema_version": VERSION, "provider": MEDIA_PROVIDERS[result.returned_model], "gateway": "fal" if result.returned_model == KLING_VIDEO_MODEL else None,
            **({"upstream_provider": "kling"} if result.returned_model == KLING_VIDEO_MODEL else {}),
            "model_identity_source": "exact_dispatch_endpoint" if result.returned_model in (BFL_IMAGE_MODEL, VEO_VIDEO_MODEL, KLING_VIDEO_MODEL) else "provider_response",
            "provider_operation_ref_reported": result.operation_ref is not None,
            "provider_operation_ref_missing_reason": None if result.operation_ref else "not_reported_stateless_response",
            "upstream_model": result.returned_model, "model_snapshot": None,
            "snapshot_missing_reason": "not_reported",
            "provider_duration_ms": result.duration_ms,
            "duration_source": "not_reported" if result.duration_ms is None else "client_observed", "usage_dimensions": result.usage,
            "estimated_cost": None, "pricing_version": None,
            "estimated_cost_missing_reason": "not_priced",
            "actual_charge": None, "actual_charge_missing_reason": "not_reported",
            "currency": "USD", "training_status": "not_approved"}


def evaluation_event(*, event_id: uuid.UUID, org_id: uuid.UUID, workspace_id: uuid.UUID,
                     execution_id: uuid.UUID, evaluator_id: str, observed_at: datetime,
                     acceptance: str, rubric_version: str,
                     labels: dict[str, str], experiment_id: uuid.UUID | None = None,
                     correction_resource_id: uuid.UUID | None = None) -> ExecutionEvent:
    """Build an observation for the EXISTING event writer, after authorization.

    No model training or data export path is introduced. Corrections remain
    restricted derivatives until separately cleared, rather than clean-room data.
    """
    if not isinstance(workspace_id, uuid.UUID):
        raise ValueError("existing event contract requires a workspace")
    if acceptance not in ("accepted", "rejected", "unrated"):
        raise ValueError("invalid acceptance")
    if not evaluator_id or len(evaluator_id) > 256 or not rubric_version or len(rubric_version) > 128:
        raise ValueError("invalid evaluator or rubric")
    allowed_labels = {"adherence", "fidelity", "typography", "visual_defects", "edit_preservation"}
    if not isinstance(labels, dict) or set(labels) - allowed_labels or any(
            value not in ("pass", "fail", "not_assessed") for value in labels.values()):
        raise ValueError("invalid evaluation labels")
    event = ExecutionEvent(
        event_id=event_id, org_id=org_id, workspace_id=workspace_id,
        execution_id=str(execution_id), event_type="observation",
        # A cohort tag alone does not establish a controlled experiment. Those
        # require the existing frozen-input/conditions/protocol contracts.
        origin="operational",
        payload_version="1", observed_at=observed_at,
        payload={"schema_version": "media-evaluation-v1", "evaluator_id": evaluator_id,
                 "acceptance": acceptance, "rubric_version": rubric_version, "labels": dict(labels),
                 "experiment_id": str(experiment_id) if experiment_id else None,
                 "correction_resource_id": str(correction_resource_id) if correction_resource_id else None,
                 "training_status": "not_approved"},
    )
    event.validate()
    return event
