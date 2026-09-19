"""Measurement Foundation V1 contracts.

Value types only. Not imported by gateway/runtime execution paths.
Does not consult the product model registry.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal, Mapping

MEASUREMENT_CONTRACT_VERSION = "1"
PAYLOAD_VERSION_V1 = "1"
FINGERPRINT_ALGORITHM = "sha256"
CANONICALIZATION_VERSION = "json-v1"

EventType = Literal[
    "execution_started",
    "call_attempted",
    "call_completed",
    "first_output_observed",
    "execution_completed",
    "result_recorded",
    "observation",
    "telemetry_incomplete",
    "provenance_correction",
]
EVENT_TYPES: frozenset[str] = frozenset(EventType.__args__)  # type: ignore[attr-defined]

ExecutionOrigin = Literal["operational", "controlled_experiment", "unknown"]
ORIGINS: frozenset[str] = frozenset(ExecutionOrigin.__args__)  # type: ignore[attr-defined]

ValidationOutcome = Literal["pass", "fail", "invalid", "pending"]
VALIDATION_OUTCOMES: frozenset[str] = frozenset(ValidationOutcome.__args__)  # type: ignore[attr-defined]

ResultKind = Literal["complete", "partial", "aggregate"]
Replayability = Literal["replayable", "incomplete", "unavailable"]
ExternalResourceMode = Literal["frozen", "replay", "live"]
ModelIdentityNamespace = Literal[
    "requested",
    "effective",
    "provider_returned",
    "speaking_provider",
    "product_registry",
    "external",
    "local",
    "unknown",
]
UsageValueStatus = Literal["exact", "estimated", "missing"]
CostValueStatus = Literal["priced", "unknown", "unpriced", "zero"]


class MeasurementRejected(ValueError):
    """Caller payload/version/contract rejected before persistence."""


class MeasurementConflict(RuntimeError):
    """Same identity with different immutable content."""


class ProvenanceConflict(MeasurementConflict):
    """Conflicting origin/provenance for one execution."""


def _json_ready(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, dict):
        return {str(k): _json_ready(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [_json_ready(v) for v in value]
    if hasattr(value, "__dataclass_fields__"):
        return _json_ready(asdict(value))
    raise MeasurementRejected(f"unsupported measurement value type: {type(value).__name__}")


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        _json_ready(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def fingerprint(value: Any) -> str:
    digest = hashlib.sha256(canonical_json_bytes(value)).hexdigest()
    return f"{FINGERPRINT_ALGORITHM}:{digest}"


def semantic_json_equal(left: Any, right: Any) -> bool:
    return canonical_json_bytes(left) == canonical_json_bytes(right)


def require_nonempty_str(name: str, value: str | None) -> str:
    if not value or not str(value).strip():
        raise MeasurementRejected(f"{name} is required")
    text = str(value).strip()
    if len(text) > 128:
        raise MeasurementRejected(f"{name} exceeds 128 characters")
    return text


@dataclass(frozen=True)
class Missingness:
    field: str
    reason: str


@dataclass(frozen=True)
class ModelIdentity:
    """One model/provider identity in an explicit namespace. Registry membership is optional."""

    namespace: ModelIdentityNamespace
    identifier: str
    provider: str | None = None
    version: str | None = None
    unknown_reason: str | None = None
    product_registry_ref: str | None = None
    product_registry_version: str | None = None

    def validate(self) -> None:
        if self.namespace not in ModelIdentityNamespace.__args__:  # type: ignore[attr-defined]
            raise MeasurementRejected(f"unknown model identity namespace: {self.namespace}")
        require_nonempty_str("identifier", self.identifier)
        if self.namespace == "unknown" and not self.unknown_reason:
            raise MeasurementRejected("unknown model identity requires unknown_reason")
        if self.namespace in {"external", "local"} and self.product_registry_ref:
            raise MeasurementRejected(
                "do not invent product-registry identity for an external/local model"
            )


@dataclass(frozen=True)
class FrozenInput:
    """Immutable input identity. Not a table. Hash alone is not replayable."""

    contract_version: str
    digest: str
    hash_algorithm: str
    canonicalization_version: str
    content_ref: str | None = None
    manifest_ref: str | None = None
    logical_fixture_id: str | None = None
    immutable_revision: str | None = None
    referenced_artifact_versions: tuple[str, ...] = ()
    replayability: Replayability = "incomplete"
    missingness: tuple[Missingness, ...] = ()

    def validate(self) -> None:
        if self.contract_version != MEASUREMENT_CONTRACT_VERSION:
            raise MeasurementRejected("frozen input contract_version must be 1")
        require_nonempty_str("digest", self.digest)
        require_nonempty_str("hash_algorithm", self.hash_algorithm)
        require_nonempty_str("canonicalization_version", self.canonicalization_version)
        if not self.content_ref and not self.manifest_ref:
            raise MeasurementRejected("frozen input requires content_ref or manifest_ref")
        if self.replayability == "replayable":
            if self.missingness:
                raise MeasurementRejected("incomplete frozen input cannot be labelled replayable")
            if not (self.content_ref or self.manifest_ref) or not self.digest:
                raise MeasurementRejected("replayable frozen input lacks sufficient provenance")
            if not self.immutable_revision and not self.logical_fixture_id:
                raise MeasurementRejected(
                    "replayable frozen input needs logical_fixture_id or immutable_revision"
                )


@dataclass(frozen=True)
class ExecutionConditionsManifest:
    """Declared intended conditions. Not proof of enforcement. Not a table."""

    contract_version: str
    digest: str
    hash_algorithm: str
    canonicalization_version: str
    snapshot_ref: str | None = None
    tools: tuple[str, ...] | None = None
    capabilities: tuple[str, ...] | None = None
    permissions: tuple[str, ...] | None = None
    runtime_versions: Mapping[str, str] | None = None
    resource_limits: Mapping[str, Any] | None = None
    timeout_policy: Mapping[str, Any] | None = None
    output_requirements: Mapping[str, Any] | None = None
    evaluation_protocol_ref: str | None = None
    validator_refs: tuple[str, ...] | None = None
    rubric_ref: str | None = None
    external_resource_mode: ExternalResourceMode | None = None
    time_window: Mapping[str, Any] | None = None
    constant_factors: tuple[str, ...] | None = None
    experimental_factors: tuple[str, ...] | None = None
    missingness: tuple[Missingness, ...] = ()

    def validate(self) -> None:
        if self.contract_version != MEASUREMENT_CONTRACT_VERSION:
            raise MeasurementRejected("conditions contract_version must be 1")
        require_nonempty_str("digest", self.digest)
        require_nonempty_str("hash_algorithm", self.hash_algorithm)
        require_nonempty_str("canonicalization_version", self.canonicalization_version)
        if self.external_resource_mode is not None and self.external_resource_mode not in (
            "frozen",
            "replay",
            "live",
        ):
            raise MeasurementRejected("invalid external_resource_mode")
        constants = set(self.constant_factors or ())
        varied = set(self.experimental_factors or ())
        if constants & varied:
            raise MeasurementRejected("conditions cannot mark the same factor as constant and varied")


@dataclass(frozen=True)
class RequestedConfiguration:
    contract_version: str
    digest: str
    hash_algorithm: str
    canonicalization_version: str
    snapshot_ref: str | None = None
    requested_model: ModelIdentity | None = None
    strategy_ref: str | None = None
    reasoning: Mapping[str, Any] | None = None
    tools: Mapping[str, Any] | None = None
    token_limits: Mapping[str, Any] | None = None
    timeout: Mapping[str, Any] | None = None
    attempts: Mapping[str, Any] | None = None
    extra: Mapping[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if self.contract_version != MEASUREMENT_CONTRACT_VERSION:
            raise MeasurementRejected("requested configuration contract_version must be 1")
        require_nonempty_str("digest", self.digest)
        if self.requested_model is not None:
            self.requested_model.validate()


@dataclass(frozen=True)
class EffectiveConfiguration:
    """Known resolved/sent values only. Omitted keys are unknown, not provider defaults."""

    contract_version: str
    digest: str
    hash_algorithm: str
    canonicalization_version: str
    snapshot_ref: str | None = None
    effective_model: ModelIdentity | None = None
    known_values: Mapping[str, Any] = field(default_factory=dict)
    omitted_unknown: tuple[str, ...] = ()

    def validate(self) -> None:
        if self.contract_version != MEASUREMENT_CONTRACT_VERSION:
            raise MeasurementRejected("effective configuration contract_version must be 1")
        require_nonempty_str("digest", self.digest)
        if self.effective_model is not None:
            self.effective_model.validate()
        overlap = set(self.known_values) & set(self.omitted_unknown)
        if overlap:
            raise MeasurementRejected("effective configuration cannot treat omitted keys as known")


@dataclass(frozen=True)
class DecisionContext:
    """What was known when selection occurred. Not captured from live requests in Gate 1."""

    contract_version: str
    digest: str
    hash_algorithm: str
    canonicalization_version: str
    frozen_input_digest: str | None = None
    conditions_digest: str | None = None
    known_facts: Mapping[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if self.contract_version != MEASUREMENT_CONTRACT_VERSION:
            raise MeasurementRejected("decision context contract_version must be 1")
        require_nonempty_str("digest", self.digest)


@dataclass(frozen=True)
class DispatchInput:
    """What was prepared/sent for one attempt after provider-specific preparation."""

    contract_version: str
    digest: str
    hash_algorithm: str
    canonicalization_version: str
    frozen_input_digest: str | None = None
    conditions_digest: str | None = None
    effective_configuration_digest: str | None = None
    payload_digest: str | None = None
    api_route: str | None = None
    adapter_serialization_version: str | None = None
    transformations: tuple[str, ...] = ()

    def validate(self) -> None:
        if self.contract_version != MEASUREMENT_CONTRACT_VERSION:
            raise MeasurementRejected("dispatch input contract_version must be 1")
        require_nonempty_str("digest", self.digest)
        if self.frozen_input_digest and self.digest == self.frozen_input_digest:
            raise MeasurementRejected("dispatch digest must not replace the frozen input identity")


@dataclass(frozen=True)
class ExperimentProtocolRef:
    contract_version: str
    digest: str
    hash_algorithm: str
    canonicalization_version: str
    manifest_ref: str | None = None

    def validate(self) -> None:
        if self.contract_version != MEASUREMENT_CONTRACT_VERSION:
            raise MeasurementRejected("experiment protocol contract_version must be 1")
        require_nonempty_str("digest", self.digest)


@dataclass(frozen=True)
class BenchmarkAttachment:
    """Optional future BEN Model Test V1 attachment. Not a parallel execution identity."""

    test_id: str | None = None
    test_version: str | None = None
    test_run_id: str | None = None
    question_id: str | None = None
    section_id: str | None = None


@dataclass(frozen=True)
class TimingObservation:
    started_at: datetime | None = None
    first_meaningful_output_at: datetime | None = None
    completed_at: datetime | None = None
    ttft_ms: float | None = None
    duration_ms: float | None = None
    timing_scope: str = "execution"
    timing_source: str = "unknown"
    missingness: tuple[Missingness, ...] = ()

    def validate(self) -> None:
        if self.ttft_ms is not None and self.ttft_ms < 0:
            raise MeasurementRejected("ttft_ms cannot be negative")
        if self.duration_ms is not None and self.duration_ms < 0:
            raise MeasurementRejected("duration_ms cannot be negative")
        if self.timing_source not in {"client_observed", "provider_reported", "unknown"}:
            raise MeasurementRejected("invalid timing_source")


@dataclass(frozen=True)
class UsageObservation:
    """Unknown is None. Zero is an observed zero. Raw provider usage is preserved separately."""

    usage_status: UsageValueStatus = "missing"
    raw_provider_usage: Mapping[str, Any] | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    reasoning_tokens: int | None = None
    total_tokens: int | None = None
    normalized_input_tokens: int | None = None
    normalized_output_tokens: int | None = None
    usage_source: str = "missing"

    def validate(self) -> None:
        if self.usage_status not in UsageValueStatus.__args__:  # type: ignore[attr-defined]
            raise MeasurementRejected("invalid usage_status")
        if self.usage_status == "missing":
            for name in (
                "input_tokens",
                "output_tokens",
                "cached_input_tokens",
                "reasoning_tokens",
                "total_tokens",
            ):
                if getattr(self, name) == 0:
                    raise MeasurementRejected("missing usage must not be stored as zero")
        for name in (
            "input_tokens",
            "output_tokens",
            "cached_input_tokens",
            "reasoning_tokens",
            "total_tokens",
            "normalized_input_tokens",
            "normalized_output_tokens",
        ):
            val = getattr(self, name)
            if val is not None and val < 0:
                raise MeasurementRejected(f"{name} cannot be negative")


@dataclass(frozen=True)
class CostObservation:
    amount_usd: float | None = None
    currency: str = "USD"
    cost_status: CostValueStatus = "unknown"
    cost_source: str = "unknown"
    pricing_version: str | None = None
    pricing_source: str | None = None

    def validate(self) -> None:
        if self.cost_status not in CostValueStatus.__args__:  # type: ignore[attr-defined]
            raise MeasurementRejected("invalid cost_status")
        if self.cost_status == "zero":
            if self.amount_usd not in (0, 0.0):
                raise MeasurementRejected("cost_status zero requires amount_usd 0")
        if self.cost_status == "unknown" and self.amount_usd == 0:
            raise MeasurementRejected("unknown cost must not be stored as zero")
        if self.amount_usd is not None and self.amount_usd < 0:
            raise MeasurementRejected("amount_usd cannot be negative")


@dataclass(frozen=True)
class ResultRef:
    result_id: str
    kind: ResultKind
    call_id: str | None = None
    integrity_digest: str | None = None

    def validate(self) -> None:
        require_nonempty_str("result_id", self.result_id)
        if self.kind not in ResultKind.__args__:  # type: ignore[attr-defined]
            raise MeasurementRejected("invalid result kind")
        if self.kind == "aggregate" and self.call_id is not None:
            raise MeasurementRejected("aggregate results must not be forced onto one call")


def _require_timestamp_or_reason(value: datetime | None, reason: str | None, name: str) -> None:
    if value is None:
        if not reason:
            raise MeasurementRejected(f"missing {name} requires {name}_missing_reason")
    elif reason:
        raise MeasurementRejected(f"{name}_missing_reason must be null when {name} is set")


@dataclass(frozen=True)
class ExecutionEvent:
    event_id: uuid.UUID
    org_id: uuid.UUID
    workspace_id: uuid.UUID
    execution_id: str
    event_type: EventType
    origin: ExecutionOrigin
    payload_version: str
    payload: Mapping[str, Any]
    task_id: str | None = None
    request_id: str | None = None
    call_id: str | None = None
    result: ResultRef | None = None
    benchmark: BenchmarkAttachment | None = None
    observed_at: datetime | None = None
    observed_at_missing_reason: str | None = None
    sequence_in_execution: int | None = None
    frozen_input: FrozenInput | None = None
    conditions: ExecutionConditionsManifest | None = None
    requested_configuration: RequestedConfiguration | None = None
    effective_configuration: EffectiveConfiguration | None = None
    decision_context: DecisionContext | None = None
    dispatch_input: DispatchInput | None = None
    experiment_protocol: ExperimentProtocolRef | None = None
    requested_model: ModelIdentity | None = None
    effective_model: ModelIdentity | None = None
    returned_model: ModelIdentity | None = None
    timing: TimingObservation | None = None
    usage: UsageObservation | None = None
    cost: CostObservation | None = None
    execution_outcome: str | None = None
    telemetry_completeness: str | None = None

    def validate(self) -> None:
        require_nonempty_str("execution_id", self.execution_id)
        if self.event_type not in EVENT_TYPES:
            raise MeasurementRejected(f"invalid event_type: {self.event_type}")
        if self.origin not in ORIGINS:
            raise MeasurementRejected(f"invalid origin: {self.origin}")
        if self.payload_version != PAYLOAD_VERSION_V1:
            raise MeasurementRejected("payload_version must be 1")
        if not isinstance(self.payload, Mapping):
            raise MeasurementRejected("payload must be a mapping")
        _forbid_secrets(self.payload)
        _require_timestamp_or_reason(self.observed_at, self.observed_at_missing_reason, "observed_at")
        if self.sequence_in_execution is not None and self.sequence_in_execution < 0:
            raise MeasurementRejected("sequence_in_execution is scoped per execution and must be >= 0")
        if self.result is not None:
            self.result.validate()
        for obj in (
            self.frozen_input,
            self.conditions,
            self.requested_configuration,
            self.effective_configuration,
            self.decision_context,
            self.dispatch_input,
            self.experiment_protocol,
            self.requested_model,
            self.effective_model,
            self.returned_model,
            self.timing,
            self.usage,
            self.cost,
        ):
            if obj is not None:
                obj.validate()  # type: ignore[union-attr]
        if self.origin == "controlled_experiment":
            missing = [
                name
                for name, val in (
                    ("frozen_input", self.frozen_input),
                    ("conditions", self.conditions),
                    ("requested_configuration", self.requested_configuration),
                    ("experiment_protocol", self.experiment_protocol),
                )
                if val is None
            ]
            if missing:
                raise MeasurementRejected(
                    "controlled_experiment origin requires frozen_input, conditions, "
                    f"requested_configuration, and experiment_protocol; missing {missing}"
                )
        if self.event_type == "provenance_correction":
            if "original_origin" not in self.payload or "stated_origin" not in self.payload:
                raise MeasurementRejected(
                    "provenance_correction payload must include original_origin and stated_origin"
                )

    def content_fingerprint(self) -> str:
        return fingerprint(self.immutable_content())

    def immutable_content(self) -> dict[str, Any]:
        return {
            "event_id": str(self.event_id),
            "org_id": str(self.org_id),
            "workspace_id": str(self.workspace_id),
            "execution_id": self.execution_id,
            "event_type": self.event_type,
            "origin": self.origin,
            "payload_version": self.payload_version,
            "payload": dict(self.payload),
            "task_id": self.task_id,
            "request_id": self.request_id,
            "call_id": self.call_id,
            "result": asdict(self.result) if self.result else None,
            "benchmark": asdict(self.benchmark) if self.benchmark else None,
            "observed_at": self.observed_at,
            "observed_at_missing_reason": self.observed_at_missing_reason,
            "sequence_in_execution": self.sequence_in_execution,
            "frozen_input": asdict(self.frozen_input) if self.frozen_input else None,
            "conditions": _mapping_or_asdict(self.conditions),
            "requested_configuration": _mapping_or_asdict(self.requested_configuration),
            "effective_configuration": _mapping_or_asdict(self.effective_configuration),
            "decision_context": _mapping_or_asdict(self.decision_context),
            "dispatch_input": _mapping_or_asdict(self.dispatch_input),
            "experiment_protocol": _mapping_or_asdict(self.experiment_protocol),
            "requested_model": asdict(self.requested_model) if self.requested_model else None,
            "effective_model": asdict(self.effective_model) if self.effective_model else None,
            "returned_model": asdict(self.returned_model) if self.returned_model else None,
            "timing": asdict(self.timing) if self.timing else None,
            "usage": asdict(self.usage) if self.usage else None,
            "cost": asdict(self.cost) if self.cost else None,
            "execution_outcome": self.execution_outcome,
            "telemetry_completeness": self.telemetry_completeness,
        }


def _mapping_or_asdict(obj: Any) -> Any:
    if obj is None:
        return None
    data = asdict(obj)
    for key in ("runtime_versions", "resource_limits", "timeout_policy", "output_requirements",
                "time_window", "extra", "known_values", "known_facts", "reasoning", "tools",
                "token_limits", "timeout", "attempts"):
        val = data.get(key)
        if isinstance(val, dict):
            data[key] = dict(val)
    return data


@dataclass(frozen=True)
class ValidationRecord:
    validation_id: uuid.UUID
    org_id: uuid.UUID
    workspace_id: uuid.UUID
    execution_id: str
    validator_type: str
    validator_version: str
    outcome: ValidationOutcome
    payload_version: str
    payload: Mapping[str, Any]
    result: ResultRef | None = None
    evaluated_at: datetime | None = None
    evaluated_at_missing_reason: str | None = None
    score: float | None = None
    score_meaning: str | None = None
    judge_execution_id: str | None = None
    benchmark: BenchmarkAttachment | None = None

    def validate(self) -> None:
        require_nonempty_str("execution_id", self.execution_id)
        require_nonempty_str("validator_type", self.validator_type)
        require_nonempty_str("validator_version", self.validator_version)
        if self.outcome not in VALIDATION_OUTCOMES:
            raise MeasurementRejected(f"invalid validation outcome: {self.outcome}")
        if self.payload_version != PAYLOAD_VERSION_V1:
            raise MeasurementRejected("payload_version must be 1")
        _forbid_secrets(self.payload)
        _require_timestamp_or_reason(self.evaluated_at, self.evaluated_at_missing_reason, "evaluated_at")
        if self.score is not None and not self.score_meaning:
            raise MeasurementRejected("score requires score_meaning")
        if self.result is not None:
            self.result.validate()

    def content_fingerprint(self) -> str:
        return fingerprint(self.immutable_content())

    def immutable_content(self) -> dict[str, Any]:
        return {
            "validation_id": str(self.validation_id),
            "org_id": str(self.org_id),
            "workspace_id": str(self.workspace_id),
            "execution_id": self.execution_id,
            "validator_type": self.validator_type,
            "validator_version": self.validator_version,
            "outcome": self.outcome,
            "payload_version": self.payload_version,
            "payload": dict(self.payload),
            "result": asdict(self.result) if self.result else None,
            "evaluated_at": self.evaluated_at,
            "evaluated_at_missing_reason": self.evaluated_at_missing_reason,
            "score": self.score,
            "score_meaning": self.score_meaning,
            "judge_execution_id": self.judge_execution_id,
            "benchmark": asdict(self.benchmark) if self.benchmark else None,
        }


_SECRET_KEYS = frozenset(
    {
        "password",
        "secret",
        "token",
        "api_key",
        "apikey",
        "authorization",
        "credential",
        "private_key",
    }
)


def _forbid_secrets(payload: Mapping[str, Any] | None) -> None:
    if not payload:
        return

    def walk(obj: Any, path: str) -> None:
        if isinstance(obj, Mapping):
            for k, v in obj.items():
                key = str(k).lower()
                here = f"{path}.{key}" if path else key
                if key in _SECRET_KEYS:
                    raise MeasurementRejected(f"measurement payload must not contain secrets ({here})")
                walk(v, here)
        elif isinstance(obj, (list, tuple)):
            for i, v in enumerate(obj):
                walk(v, f"{path}[{i}]")

    walk(payload, "")
