"""Append-only inference call ledger persistence.

Only model_gateway (and its account_* entrypoints) may call record_inference_call.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from database.connection import get_db_session
from database.models import InferenceCallRecordRow
from services.inference.contracts import InferenceCallRecord
from services.ops.structured_log import log_error, log_info


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_uuid(value: str | None):
    import uuid

    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


async def persist_inference_call(record: InferenceCallRecord) -> None:
    """Insert one immutable accounting row. Never updates."""
    row = InferenceCallRecordRow(
        id=_parse_uuid(record.call_id) or None,
        request_id=record.request_id,
        execution_id=record.execution_id,
        org_id=record.org_id,
        workspace_id=record.workspace_id,
        user_id=record.user_id,
        capability_key=record.capability_key,
        pipeline=record.pipeline,
        provider=record.provider,
        model=record.model,
        api_model=record.api_model,
        outcome=record.outcome,
        stream=bool(record.stream),
        input_tokens=int(record.usage.input_tokens),
        output_tokens=int(record.usage.output_tokens),
        cached_input_tokens=int(record.usage.cached_input_tokens),
        reasoning_tokens=int(record.usage.reasoning_tokens),
        total_tokens=int(record.usage.normalized_total()),
        usage_status=record.usage.usage_status,
        cost_usd=record.cost.amount_usd,
        cost_status=record.cost.cost_status,
        pricing_version=record.cost.pricing_version,
        currency=record.cost.currency,
        latency_ms=record.latency_ms,
        provider_request_id=record.provider_request_id,
        finish_reason=record.finish_reason,
        error_class=record.error_class,
        started_at=record.started_at,
        finished_at=record.finished_at,
        extras=dict(record.extras or {}),
    )
    async with get_db_session() as session:
        session.add(row)
        await session.commit()


async def record_inference_call(record: InferenceCallRecord) -> dict[str, Any]:
    """
    Persist accounting event. On DB failure, emit structured error with full
    record fields (no prompt content) so the event is not silently lost.
    """
    payload = {
        "call_id": record.call_id,
        "request_id": record.request_id,
        "execution_id": record.execution_id,
        "org_id": record.org_id,
        "workspace_id": record.workspace_id,
        "pipeline": record.pipeline,
        "provider": record.provider,
        "model": record.model,
        "api_model": record.api_model,
        "outcome": record.outcome,
        "stream": record.stream,
        "input_tokens": record.usage.input_tokens,
        "output_tokens": record.usage.output_tokens,
        "cached_input_tokens": record.usage.cached_input_tokens,
        "reasoning_tokens": record.usage.reasoning_tokens,
        "total_tokens": record.usage.normalized_total(),
        "usage_status": record.usage.usage_status,
        "cost_usd": record.cost.amount_usd,
        "cost_status": record.cost.cost_status,
        "pricing_version": record.cost.pricing_version,
        "latency_ms": record.latency_ms,
        "provider_request_id": record.provider_request_id,
        "finish_reason": record.finish_reason,
        "error_class": record.error_class,
    }
    # Avoid clobbering structured-log outcome with the call's accounting outcome.
    safe_fields = {
        k: v
        for k, v in payload.items()
        if v is not None and k not in {"outcome"}
    }
    try:
        await persist_inference_call(record)
        log_info(
            "inference call accounted",
            subsystem="inference_accounting",
            operation="record_inference_call",
            outcome="ok",
            call_outcome=record.outcome,
            **safe_fields,
        )
        return {"persisted": True, **payload}
    except Exception as exc:  # noqa: BLE001
        try:
            log_error(
                "inference call accounting persist failed",
                subsystem="inference_accounting",
                error_class=type(exc).__name__,
                call_outcome=record.outcome,
                persist_operation="record_inference_call",
                **safe_fields,
            )
        except Exception:  # noqa: BLE001
            pass
        return {"persisted": False, "error_class": type(exc).__name__, **payload}


def build_call_record(
    *,
    ctx,
    provider: str,
    model: str,
    api_model: str | None,
    outcome: str,
    usage,
    cost,
    latency_ms: float | None,
    stream: bool,
    provider_request_id: str | None = None,
    finish_reason: str | None = None,
    error_class: str | None = None,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    extras: dict[str, Any] | None = None,
) -> InferenceCallRecord:
    now = _utc_now()
    return InferenceCallRecord(
        call_id=InferenceCallRecord.new_call_id(),
        request_id=ctx.request_id,
        execution_id=ctx.execution_id,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        capability_key=ctx.capability_key,
        pipeline=ctx.pipeline,
        provider=provider,
        model=model,
        api_model=api_model,
        outcome=outcome,  # type: ignore[arg-type]
        usage=usage,
        cost=cost,
        latency_ms=latency_ms,
        stream=stream,
        provider_request_id=provider_request_id,
        finish_reason=finish_reason,
        error_class=error_class,
        started_at=started_at or now,
        finished_at=finished_at or now,
        extras=dict(extras or {}),
    )
