"""Append-only execution event persistence for Measurement Foundation V1.

Takes an explicit AsyncSession. Does not read an ambient database URL, open a
global session, write InferenceCallRecord rows, or consult the product registry.

call_id is an optional correlation string. When present it may equal
InferenceCallRecord.call_id / inference_call_records.id. There is no FK.
"""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import ExecutionEventRow
from services.inference.measurement_contracts import (
    CANONICALIZATION_VERSION,
    FINGERPRINT_ALGORITHM,
    BenchmarkAttachment,
    CostObservation,
    DecisionContext,
    DispatchInput,
    EffectiveConfiguration,
    ExecutionConditionsManifest,
    ExecutionEvent,
    ExperimentProtocolRef,
    FrozenInput,
    MeasurementConflict,
    Missingness,
    ModelIdentity,
    ProvenanceConflict,
    RequestedConfiguration,
    ResultRef,
    TimingObservation,
    UsageObservation,
    _json_ready,
    fingerprint,
)

ENVELOPE_VERSION = "1"


@dataclass(frozen=True)
class PersistedExecutionEvent:
    event: ExecutionEvent
    recorded_at: datetime
    replayed: bool


async def bind_measurement_tenant(session: AsyncSession, org_id: uuid.UUID) -> None:
    """Set FORCE-RLS org context local to the current transaction."""
    await session.execute(
        text("SELECT set_config('app.current_org_id', :v, true)"),
        {"v": str(org_id)},
    )


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _as_utc(value)
    return _as_utc(datetime.fromisoformat(str(value)))


def _iter_exception_chain(exc: BaseException):
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = getattr(current, "orig", None) or current.__cause__ or current.__context__


def integrity_is_origin_conflict(exc: IntegrityError) -> bool:
    if "execution origin conflict" in str(exc):
        return True
    return any("execution origin conflict" in str(item) for item in _iter_exception_chain(exc))


def _missingness(items: Any) -> tuple[Missingness, ...]:
    if not items:
        return ()
    return tuple(Missingness(field=m["field"], reason=m["reason"]) for m in items)


def _model(data: Mapping[str, Any] | None) -> ModelIdentity | None:
    if not data:
        return None
    return ModelIdentity(
        namespace=data["namespace"],
        identifier=data["identifier"],
        provider=data.get("provider"),
        version=data.get("version"),
        unknown_reason=data.get("unknown_reason"),
        product_registry_ref=data.get("product_registry_ref"),
        product_registry_version=data.get("product_registry_version"),
    )


def _frozen(data: Mapping[str, Any] | None) -> FrozenInput | None:
    if not data:
        return None
    return FrozenInput(
        contract_version=data["contract_version"],
        digest=data["digest"],
        hash_algorithm=data["hash_algorithm"],
        canonicalization_version=data["canonicalization_version"],
        content_ref=data.get("content_ref"),
        manifest_ref=data.get("manifest_ref"),
        logical_fixture_id=data.get("logical_fixture_id"),
        immutable_revision=data.get("immutable_revision"),
        referenced_artifact_versions=tuple(data.get("referenced_artifact_versions") or ()),
        replayability=data.get("replayability") or "incomplete",
        missingness=_missingness(data.get("missingness")),
    )


def _conditions(data: Mapping[str, Any] | None) -> ExecutionConditionsManifest | None:
    if not data:
        return None
    return ExecutionConditionsManifest(
        contract_version=data["contract_version"],
        digest=data["digest"],
        hash_algorithm=data["hash_algorithm"],
        canonicalization_version=data["canonicalization_version"],
        snapshot_ref=data.get("snapshot_ref"),
        tools=tuple(data["tools"]) if data.get("tools") is not None else None,
        capabilities=tuple(data["capabilities"]) if data.get("capabilities") is not None else None,
        permissions=tuple(data["permissions"]) if data.get("permissions") is not None else None,
        runtime_versions=data.get("runtime_versions"),
        resource_limits=data.get("resource_limits"),
        timeout_policy=data.get("timeout_policy"),
        output_requirements=data.get("output_requirements"),
        evaluation_protocol_ref=data.get("evaluation_protocol_ref"),
        validator_refs=tuple(data["validator_refs"]) if data.get("validator_refs") is not None else None,
        rubric_ref=data.get("rubric_ref"),
        external_resource_mode=data.get("external_resource_mode"),
        time_window=data.get("time_window"),
        constant_factors=tuple(data["constant_factors"]) if data.get("constant_factors") is not None else None,
        experimental_factors=tuple(data["experimental_factors"]) if data.get("experimental_factors") is not None else None,
        missingness=_missingness(data.get("missingness")),
    )


def _requested(data: Mapping[str, Any] | None) -> RequestedConfiguration | None:
    if not data:
        return None
    return RequestedConfiguration(
        contract_version=data["contract_version"],
        digest=data["digest"],
        hash_algorithm=data["hash_algorithm"],
        canonicalization_version=data["canonicalization_version"],
        snapshot_ref=data.get("snapshot_ref"),
        requested_model=_model(data.get("requested_model")),
        strategy_ref=data.get("strategy_ref"),
        reasoning=data.get("reasoning"),
        tools=data.get("tools"),
        token_limits=data.get("token_limits"),
        timeout=data.get("timeout"),
        attempts=data.get("attempts"),
        extra=dict(data.get("extra") or {}),
    )


def _effective(data: Mapping[str, Any] | None) -> EffectiveConfiguration | None:
    if not data:
        return None
    return EffectiveConfiguration(
        contract_version=data["contract_version"],
        digest=data["digest"],
        hash_algorithm=data["hash_algorithm"],
        canonicalization_version=data["canonicalization_version"],
        snapshot_ref=data.get("snapshot_ref"),
        effective_model=_model(data.get("effective_model")),
        known_values=dict(data.get("known_values") or {}),
        omitted_unknown=tuple(data.get("omitted_unknown") or ()),
    )


def _decision(data: Mapping[str, Any] | None) -> DecisionContext | None:
    if not data:
        return None
    return DecisionContext(
        contract_version=data["contract_version"],
        digest=data["digest"],
        hash_algorithm=data["hash_algorithm"],
        canonicalization_version=data["canonicalization_version"],
        frozen_input_digest=data.get("frozen_input_digest"),
        conditions_digest=data.get("conditions_digest"),
        known_facts=dict(data.get("known_facts") or {}),
    )


def _dispatch(data: Mapping[str, Any] | None) -> DispatchInput | None:
    if not data:
        return None
    return DispatchInput(
        contract_version=data["contract_version"],
        digest=data["digest"],
        hash_algorithm=data["hash_algorithm"],
        canonicalization_version=data["canonicalization_version"],
        frozen_input_digest=data.get("frozen_input_digest"),
        conditions_digest=data.get("conditions_digest"),
        effective_configuration_digest=data.get("effective_configuration_digest"),
        payload_digest=data.get("payload_digest"),
        api_route=data.get("api_route"),
        adapter_serialization_version=data.get("adapter_serialization_version"),
        transformations=tuple(data.get("transformations") or ()),
    )


def _protocol(data: Mapping[str, Any] | None) -> ExperimentProtocolRef | None:
    if not data:
        return None
    return ExperimentProtocolRef(
        contract_version=data["contract_version"],
        digest=data["digest"],
        hash_algorithm=data["hash_algorithm"],
        canonicalization_version=data["canonicalization_version"],
        manifest_ref=data.get("manifest_ref"),
    )


def _result(data: Mapping[str, Any] | None) -> ResultRef | None:
    if not data:
        return None
    return ResultRef(
        result_id=data["result_id"],
        kind=data["kind"],
        call_id=data.get("call_id"),
        integrity_digest=data.get("integrity_digest"),
    )


def _benchmark(data: Mapping[str, Any] | None) -> BenchmarkAttachment | None:
    if not data:
        return None
    return BenchmarkAttachment(
        test_id=data.get("test_id"),
        test_version=data.get("test_version"),
        test_run_id=data.get("test_run_id"),
        question_id=data.get("question_id"),
        section_id=data.get("section_id"),
    )


def _timing(data: Mapping[str, Any] | None) -> TimingObservation | None:
    if not data:
        return None
    return TimingObservation(
        started_at=_parse_dt(data.get("started_at")),
        first_meaningful_output_at=_parse_dt(data.get("first_meaningful_output_at")),
        completed_at=_parse_dt(data.get("completed_at")),
        ttft_ms=data.get("ttft_ms"),
        duration_ms=data.get("duration_ms"),
        timing_scope=data.get("timing_scope") or "execution",
        timing_source=data.get("timing_source") or "unknown",
        missingness=_missingness(data.get("missingness")),
    )


def _usage(data: Mapping[str, Any] | None) -> UsageObservation | None:
    if not data:
        return None
    return UsageObservation(
        usage_status=data.get("usage_status") or "missing",
        raw_provider_usage=data.get("raw_provider_usage"),
        input_tokens=data.get("input_tokens"),
        output_tokens=data.get("output_tokens"),
        cached_input_tokens=data.get("cached_input_tokens"),
        reasoning_tokens=data.get("reasoning_tokens"),
        total_tokens=data.get("total_tokens"),
        normalized_input_tokens=data.get("normalized_input_tokens"),
        normalized_output_tokens=data.get("normalized_output_tokens"),
        usage_source=data.get("usage_source") or "missing",
    )


def _cost(data: Mapping[str, Any] | None) -> CostObservation | None:
    if not data:
        return None
    return CostObservation(
        amount_usd=data.get("amount_usd"),
        currency=data.get("currency") or "USD",
        cost_status=data.get("cost_status") or "unknown",
        cost_source=data.get("cost_source") or "unknown",
        pricing_version=data.get("pricing_version"),
        pricing_source=data.get("pricing_source"),
    )


def execution_event_from_immutable(data: Mapping[str, Any]) -> ExecutionEvent:
    return ExecutionEvent(
        event_id=uuid.UUID(str(data["event_id"])),
        org_id=uuid.UUID(str(data["org_id"])),
        workspace_id=uuid.UUID(str(data["workspace_id"])),
        execution_id=data["execution_id"],
        event_type=data["event_type"],
        origin=data["origin"],
        payload_version=data["payload_version"],
        payload=dict(data.get("payload") or {}),
        task_id=data.get("task_id"),
        request_id=data.get("request_id"),
        call_id=data.get("call_id"),
        result=_result(data.get("result")),
        benchmark=_benchmark(data.get("benchmark")),
        observed_at=_parse_dt(data.get("observed_at")),
        observed_at_missing_reason=data.get("observed_at_missing_reason"),
        sequence_in_execution=data.get("sequence_in_execution"),
        frozen_input=_frozen(data.get("frozen_input")),
        conditions=_conditions(data.get("conditions")),
        requested_configuration=_requested(data.get("requested_configuration")),
        effective_configuration=_effective(data.get("effective_configuration")),
        decision_context=_decision(data.get("decision_context")),
        dispatch_input=_dispatch(data.get("dispatch_input")),
        experiment_protocol=_protocol(data.get("experiment_protocol")),
        requested_model=_model(data.get("requested_model")),
        effective_model=_model(data.get("effective_model")),
        returned_model=_model(data.get("returned_model")),
        timing=_timing(data.get("timing")),
        usage=_usage(data.get("usage")),
        cost=_cost(data.get("cost")),
        execution_outcome=data.get("execution_outcome"),
        telemetry_completeness=data.get("telemetry_completeness"),
    )


def _object_fingerprint(obj: Any) -> str | None:
    if obj is None:
        return None
    return fingerprint(asdict(obj))


def _event_envelope(event: ExecutionEvent) -> dict[str, Any]:
    return {
        "measurement_envelope_version": ENVELOPE_VERSION,
        "immutable": _json_ready(event.immutable_content()),
    }


def _event_from_row(row: ExecutionEventRow) -> PersistedExecutionEvent:
    payload = row.payload or {}
    immutable = payload.get("immutable")
    if not isinstance(immutable, Mapping):
        raise MeasurementConflict("execution event envelope is missing immutable content")
    event = execution_event_from_immutable(immutable)
    if event.content_fingerprint() != row.content_fingerprint:
        raise MeasurementConflict("stored execution event content fingerprint mismatch")
    return PersistedExecutionEvent(event=event, recorded_at=row.recorded_at, replayed=False)


def _row_from_event(event: ExecutionEvent) -> ExecutionEventRow:
    benchmark = event.benchmark
    result = event.result
    dispatch = event.dispatch_input
    return ExecutionEventRow(
        event_id=event.event_id,
        org_id=event.org_id,
        workspace_id=event.workspace_id,
        execution_id=event.execution_id,
        task_id=event.task_id,
        request_id=event.request_id,
        call_id=event.call_id,
        result_id=result.result_id if result else None,
        test_id=benchmark.test_id if benchmark else None,
        test_version=benchmark.test_version if benchmark else None,
        test_run_id=benchmark.test_run_id if benchmark else None,
        question_id=benchmark.question_id if benchmark else None,
        section_id=benchmark.section_id if benchmark else None,
        event_type=event.event_type,
        origin=event.origin,
        observed_at=_as_utc(event.observed_at),
        observed_at_missing_reason=event.observed_at_missing_reason,
        payload_version=event.payload_version,
        sequence_in_execution=event.sequence_in_execution,
        frozen_input_fingerprint=_object_fingerprint(event.frozen_input),
        conditions_fingerprint=_object_fingerprint(event.conditions),
        requested_configuration_fingerprint=_object_fingerprint(event.requested_configuration),
        effective_configuration_fingerprint=_object_fingerprint(event.effective_configuration),
        dispatch_payload_digest=(
            (dispatch.payload_digest or dispatch.digest) if dispatch is not None else None
        ),
        fingerprint_algorithm=FINGERPRINT_ALGORITHM,
        canonicalization_version=CANONICALIZATION_VERSION,
        content_fingerprint=event.content_fingerprint(),
        payload=_event_envelope(event),
    )


def _insert_values(row: ExecutionEventRow) -> dict[str, Any]:
    skip = {"recorded_at"}
    return {col.key: getattr(row, col.key) for col in ExecutionEventRow.__table__.columns if col.key not in skip}


async def _stated_origin(session: AsyncSession, org_id: uuid.UUID, execution_id: str) -> str | None:
    result = await session.execute(
        select(ExecutionEventRow.origin)
        .where(
            ExecutionEventRow.org_id == org_id,
            ExecutionEventRow.execution_id == execution_id,
        )
        .limit(1)
    )
    return result.scalar_one_or_none()


async def insert_execution_event(
    session: AsyncSession,
    event: ExecutionEvent,
) -> PersistedExecutionEvent:
    """Insert one observation. Same id + same content replays; same id + different content conflicts."""
    event.validate()
    await bind_measurement_tenant(session, event.org_id)
    existing_origin = await _stated_origin(session, event.org_id, event.execution_id)
    if existing_origin is not None and existing_origin != event.origin:
        raise ProvenanceConflict(
            f"execution {event.execution_id} origin is {existing_origin!r}, not {event.origin!r}"
        )
    values = _insert_values(_row_from_event(event))
    stmt = (
        pg_insert(ExecutionEventRow)
        .values(**values)
        .on_conflict_do_nothing(index_elements=["event_id"])
        .returning(ExecutionEventRow)
    )
    try:
        async with session.begin_nested():
            inserted = (await session.execute(stmt)).scalars().first()
    except IntegrityError as exc:
        if integrity_is_origin_conflict(exc):
            raise ProvenanceConflict(
                f"execution {event.execution_id} origin conflict"
            ) from exc
        raise
    if inserted is not None:
        loaded = _event_from_row(inserted)
        return PersistedExecutionEvent(
            event=loaded.event,
            recorded_at=loaded.recorded_at,
            replayed=False,
        )
    stored = await session.get(ExecutionEventRow, event.event_id)
    if stored is None:
        raise MeasurementConflict(
            f"event_id {event.event_id} already exists outside this tenant"
        )
    if stored.content_fingerprint != event.content_fingerprint():
        raise MeasurementConflict(
            f"event_id {event.event_id} already exists with different content"
        )
    loaded = _event_from_row(stored)
    return PersistedExecutionEvent(
        event=loaded.event,
        recorded_at=loaded.recorded_at,
        replayed=True,
    )


async def get_execution_event(
    session: AsyncSession,
    org_id: uuid.UUID,
    event_id: uuid.UUID,
) -> PersistedExecutionEvent | None:
    await bind_measurement_tenant(session, org_id)
    row = await session.get(ExecutionEventRow, event_id)
    if row is None or row.org_id != org_id:
        return None
    return _event_from_row(row)


async def list_execution_events(
    session: AsyncSession,
    org_id: uuid.UUID,
    execution_id: str,
) -> list[PersistedExecutionEvent]:
    await bind_measurement_tenant(session, org_id)
    result = await session.execute(
        select(ExecutionEventRow)
        .where(
            ExecutionEventRow.org_id == org_id,
            ExecutionEventRow.execution_id == execution_id,
        )
        .order_by(
            ExecutionEventRow.sequence_in_execution.asc().nulls_last(),
            ExecutionEventRow.recorded_at.asc(),
            ExecutionEventRow.event_id.asc(),
        )
    )
    return [_event_from_row(row) for row in result.scalars().all()]
