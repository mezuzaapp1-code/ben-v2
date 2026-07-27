"""Provider-neutral inference accounting contracts (Pass 1).

Immutable value objects only. Gateway is the sole writer of ledger rows.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal
import uuid

UsageStatus = Literal["exact", "estimated", "missing"]
CostStatus = Literal["priced", "unknown", "unpriced", "zero"]
CallOutcome = Literal[
    "success",
    "error",
    "timeout",
    "client_disconnect",
    "stream_interrupted",
    "rejected",
]


@dataclass(frozen=True)
class InferenceUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0
    usage_status: UsageStatus = "missing"

    def normalized_total(self) -> int:
        if self.total_tokens > 0:
            return int(self.total_tokens)
        return int(self.input_tokens) + int(self.output_tokens) + int(self.reasoning_tokens)


@dataclass(frozen=True)
class InferencePricingSnapshot:
    pricing_version: str
    provider: str
    model: str
    input_usd_per_token: float | None
    output_usd_per_token: float | None
    cached_input_usd_per_token: float | None = None
    reasoning_usd_per_token: float | None = None
    currency: str = "USD"


@dataclass(frozen=True)
class InferenceCost:
    amount_usd: float | None
    currency: str
    cost_status: CostStatus
    pricing_version: str


@dataclass(frozen=True)
class ExecutionContext:
    """Minimal boarding pass passed to model_gateway (not the full ExecutionPlan)."""

    request_id: str | None
    execution_id: str
    org_id: str | None
    workspace_id: str | None
    user_id: str | None
    capability_key: str | None
    pipeline: str
    provider: str | None = None
    model: str | None = None
    budget_mode: str = "measure"


@dataclass(frozen=True)
class InferenceCallRecord:
    """Immutable accounting event — one provider call attempt."""

    call_id: str
    request_id: str | None
    execution_id: str
    org_id: str | None
    workspace_id: str | None
    user_id: str | None
    capability_key: str | None
    pipeline: str
    provider: str
    model: str
    api_model: str | None
    outcome: CallOutcome
    usage: InferenceUsage
    cost: InferenceCost
    latency_ms: float | None
    stream: bool
    provider_request_id: str | None
    finish_reason: str | None
    error_class: str | None
    started_at: datetime
    finished_at: datetime
    extras: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def new_call_id() -> str:
        return str(uuid.uuid4())
