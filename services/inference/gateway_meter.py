"""Gateway-only inference metering helpers (Pass 1).

Only model_gateway should import and call these writers.
"""
from __future__ import annotations

import asyncio
from contextvars import ContextVar
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

import httpx

from services.inference.contracts import CallOutcome, ExecutionContext, InferenceUsage
from services.inference.execution_context import require_execution_context
from services.inference.ledger import build_call_record, record_inference_call
from services.inference.pricing import calculate_cost, resolve_pricing_snapshot
from services.inference.usage_normalize import usage_missing
from services.ops.failure_classification import FAILURE_TIMEOUT, classify_failure

_last_accounted: ContextVar[dict[str, Any] | None] = ContextVar(
    "ben_last_accounted_inference", default=None
)


def get_last_accounted_call() -> dict[str, Any] | None:
    return _last_accounted.get()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def classify_call_outcome(exc: BaseException | None, *, streamed_any: bool = False) -> CallOutcome:
    if exc is None:
        return "success"
    if isinstance(exc, asyncio.CancelledError):
        return "client_disconnect" if not streamed_any else "stream_interrupted"
    if isinstance(exc, GeneratorExit):
        return "stream_interrupted" if streamed_any else "client_disconnect"
    if classify_failure(exc) == FAILURE_TIMEOUT or isinstance(exc, httpx.TimeoutException):
        return "timeout"
    return "error"


async def account_provider_attempt(
    *,
    provider: str,
    model: str,
    api_model: str | None,
    outcome: CallOutcome,
    usage: InferenceUsage | None,
    latency_ms: float | None,
    stream: bool,
    provider_request_id: str | None = None,
    finish_reason: str | None = None,
    error_class: str | None = None,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    pipeline: str | None = None,
    org_id: str | None = None,
    workspace_id: str | None = None,
    extras: dict[str, Any] | None = None,
    ctx: ExecutionContext | None = None,
) -> dict[str, Any]:
    """Create exactly one immutable accounting event for one provider attempt."""
    resolved_ctx = ctx or require_execution_context(
        pipeline=pipeline or "unknown",
        org_id=org_id,
        workspace_id=workspace_id,
    )
    if pipeline and pipeline != resolved_ctx.pipeline:
        resolved_ctx = replace(resolved_ctx, pipeline=pipeline)
    if org_id and not resolved_ctx.org_id:
        resolved_ctx = replace(resolved_ctx, org_id=org_id)
    if workspace_id and not resolved_ctx.workspace_id:
        resolved_ctx = replace(resolved_ctx, workspace_id=workspace_id)
    resolved_usage = usage or usage_missing()
    snapshot = resolve_pricing_snapshot(provider=provider, model=model, usage=resolved_usage)
    cost = calculate_cost(resolved_usage, snapshot)
    record = build_call_record(
        ctx=resolved_ctx,
        provider=provider,
        model=model,
        api_model=api_model,
        outcome=outcome,
        usage=resolved_usage,
        cost=cost,
        latency_ms=latency_ms,
        stream=stream,
        provider_request_id=provider_request_id,
        finish_reason=finish_reason,
        error_class=error_class,
        started_at=started_at or _utc_now(),
        finished_at=finished_at or _utc_now(),
        extras=extras,
    )
    result = await record_inference_call(record)
    summary = {
        "call_id": record.call_id,
        "request_id": record.request_id,
        "execution_id": record.execution_id,
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
        "persisted": result.get("persisted"),
    }
    _last_accounted.set(summary)
    return summary
