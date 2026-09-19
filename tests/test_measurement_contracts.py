"""Measurement Foundation V1 contract tests. No database."""
from __future__ import annotations

import inspect
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from services.inference.measurement_contracts import (
    MEASUREMENT_CONTRACT_VERSION,
    BenchmarkAttachment,
    CostObservation,
    ExecutionConditionsManifest,
    ExecutionEvent,
    ExperimentProtocolRef,
    FrozenInput,
    MeasurementRejected,
    ModelIdentity,
    RequestedConfiguration,
    ResultRef,
    TimingObservation,
    UsageObservation,
    ValidationRecord,
    fingerprint,
    semantic_json_equal,
)

ORG = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
WS = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
NOW = datetime(2026, 9, 19, 20, 0, tzinfo=timezone.utc)
ROOT = Path("/workspace")


def _digest(label: str) -> str:
    return fingerprint({"label": label})


def _frozen() -> FrozenInput:
    return FrozenInput(
        contract_version=MEASUREMENT_CONTRACT_VERSION,
        digest=_digest("input"),
        hash_algorithm="sha256",
        canonicalization_version="json-v1",
        content_ref="artifact:q001",
        logical_fixture_id="q001",
        immutable_revision="1",
        replayability="replayable",
    )


def _conditions() -> ExecutionConditionsManifest:
    return ExecutionConditionsManifest(
        contract_version=MEASUREMENT_CONTRACT_VERSION,
        digest=_digest("conditions"),
        hash_algorithm="sha256",
        canonicalization_version="json-v1",
        snapshot_ref="conditions:v1",
        constant_factors=("tools",),
        experimental_factors=("model",),
    )


def _requested(model: ModelIdentity | None = None) -> RequestedConfiguration:
    return RequestedConfiguration(
        contract_version=MEASUREMENT_CONTRACT_VERSION,
        digest=_digest("requested"),
        hash_algorithm="sha256",
        canonicalization_version="json-v1",
        requested_model=model,
    )


def _protocol() -> ExperimentProtocolRef:
    return ExperimentProtocolRef(
        contract_version=MEASUREMENT_CONTRACT_VERSION,
        digest=_digest("protocol"),
        hash_algorithm="sha256",
        canonicalization_version="json-v1",
        manifest_ref="ben-model-test-v1",
    )


def _event(**overrides) -> ExecutionEvent:
    base = dict(
        event_id=uuid4(),
        org_id=ORG,
        workspace_id=WS,
        execution_id="exec-1",
        event_type="execution_completed",
        origin="operational",
        payload_version="1",
        payload={"answer": "42"},
        observed_at=NOW,
        requested_model=ModelIdentity(namespace="local", identifier="local-gguf-v1"),
    )
    base.update(overrides)
    return ExecutionEvent(**base)


def test_fingerprint_is_order_independent_and_excludes_nothing_but_recorded_at():
    event = _event()
    content = event.immutable_content()
    assert "recorded_at" not in content
    assert semantic_json_equal({"b": 1, "a": 2}, {"a": 2, "b": 1})
    assert event.content_fingerprint().startswith("sha256:")
    changed = _event(event_id=event.event_id, payload={"answer": "43"})
    assert changed.content_fingerprint() != event.content_fingerprint()


def test_unknown_is_not_zero_for_usage_and_cost():
    UsageObservation(usage_status="missing").validate()
    with pytest.raises(MeasurementRejected, match="missing usage must not be stored as zero"):
        UsageObservation(usage_status="missing", input_tokens=0).validate()
    CostObservation(cost_status="unknown").validate()
    with pytest.raises(MeasurementRejected, match="unknown cost must not be stored as zero"):
        CostObservation(cost_status="unknown", amount_usd=0).validate()
    CostObservation(cost_status="zero", amount_usd=0).validate()


def test_raw_usage_and_normalized_usage_are_distinct_fields():
    usage = UsageObservation(
        usage_status="exact",
        raw_provider_usage={"prompt_tokens": 9, "completion_tokens": 4},
        input_tokens=9,
        output_tokens=4,
        normalized_input_tokens=10,
        usage_source="provider_reported",
    )
    usage.validate()
    assert usage.raw_provider_usage != {
        "input_tokens": usage.input_tokens,
        "normalized_input_tokens": usage.normalized_input_tokens,
    }


def test_requested_effective_and_returned_models_are_not_collapsed():
    event = _event(
        requested_model=ModelIdentity(namespace="requested", identifier="astra-alias"),
        effective_model=ModelIdentity(
            namespace="effective", identifier="gpt-6-astra", provider="openai"
        ),
        returned_model=ModelIdentity(
            namespace="provider_returned", identifier="gpt-6-astra-2026-09-01"
        ),
    )
    event.validate()
    ids = {
        event.requested_model.identifier,
        event.effective_model.identifier,
        event.returned_model.identifier,
    }
    assert len(ids) == 3


def test_local_unregistered_model_does_not_require_registry_membership():
    model = ModelIdentity(namespace="local", identifier="not-in-product-registry")
    model.validate()
    event = _event(requested_model=model)
    event.validate()
    with pytest.raises(MeasurementRejected, match="product-registry"):
        ModelIdentity(
            namespace="local",
            identifier="x",
            product_registry_ref="invented",
        ).validate()


def test_aggregate_result_must_not_force_a_call_id():
    ResultRef(result_id="r1", kind="complete", call_id="c1").validate()
    ResultRef(result_id="r2", kind="complete").validate()
    with pytest.raises(MeasurementRejected, match="aggregate"):
        ResultRef(result_id="r3", kind="aggregate", call_id="c1").validate()


def test_execution_need_not_reference_a_call():
    event = _event(call_id=None, event_type="execution_started")
    event.validate()
    assert event.call_id is None


def test_controlled_experiment_requires_frozen_refs():
    with pytest.raises(MeasurementRejected, match="controlled_experiment"):
        _event(origin="controlled_experiment").validate()
    event = _event(
        origin="controlled_experiment",
        frozen_input=_frozen(),
        conditions=_conditions(),
        requested_configuration=_requested(),
        experiment_protocol=_protocol(),
    )
    event.validate()


def test_secrets_are_rejected():
    with pytest.raises(MeasurementRejected, match="secrets"):
        _event(payload={"api_key": "sk-live"}).validate()


def test_observed_at_and_evaluated_at_require_explicit_missing_reason():
    with pytest.raises(MeasurementRejected, match="observed_at"):
        _event(observed_at=None, observed_at_missing_reason=None).validate()
    _event(observed_at=None, observed_at_missing_reason="not_captured").validate()
    with pytest.raises(MeasurementRejected, match="evaluated_at"):
        ValidationRecord(
            validation_id=uuid4(),
            org_id=ORG,
            workspace_id=WS,
            execution_id="exec-1",
            validator_type="exact_match",
            validator_version="1",
            outcome="pending",
            payload_version="1",
            payload={},
        ).validate()


def test_timing_scope_and_source_are_explicit():
    TimingObservation(
        started_at=NOW,
        ttft_ms=12.5,
        duration_ms=40.0,
        timing_scope="execution",
        timing_source="client_observed",
    ).validate()
    with pytest.raises(MeasurementRejected, match="timing_source"):
        TimingObservation(timing_source="guessed").validate()
    with pytest.raises(MeasurementRejected, match="negative"):
        TimingObservation(ttft_ms=-1, timing_source="unknown").validate()


def test_validation_is_separate_from_execution_and_allows_regrade_identity():
    first = ValidationRecord(
        validation_id=uuid4(),
        org_id=ORG,
        workspace_id=WS,
        execution_id="exec-1",
        validator_type="exact_match",
        validator_version="1",
        outcome="fail",
        payload_version="1",
        payload={"reason": "mismatch"},
        evaluated_at=NOW,
        result=ResultRef(result_id="r1", kind="complete"),
        benchmark=BenchmarkAttachment(
            test_id="ben-model-test",
            test_version="v1",
            test_run_id="run-1",
            question_id="Q001",
        ),
    )
    first.validate()
    regrade = ValidationRecord(
        validation_id=uuid4(),
        org_id=ORG,
        workspace_id=WS,
        execution_id="exec-1",
        validator_type="exact_match",
        validator_version="2",
        outcome="pass",
        payload_version="1",
        payload={"reason": "rubric restated"},
        evaluated_at=NOW,
        result=ResultRef(result_id="r1", kind="complete"),
        score=1.0,
        score_meaning="binary_pass",
    )
    regrade.validate()
    assert first.validation_id != regrade.validation_id
    assert first.content_fingerprint() != regrade.content_fingerprint()


def test_provenance_correction_requires_origin_fields():
    with pytest.raises(MeasurementRejected, match="provenance_correction"):
        _event(event_type="provenance_correction", payload={"note": "x"}).validate()
    _event(
        event_type="provenance_correction",
        payload={"original_origin": "unknown", "stated_origin": "operational"},
    ).validate()


def test_hash_alone_does_not_claim_replayability():
    with pytest.raises(MeasurementRejected, match="content_ref or manifest_ref"):
        FrozenInput(
            contract_version="1",
            digest="abc",
            hash_algorithm="sha256",
            canonicalization_version="json-v1",
            replayability="replayable",
        ).validate()


def test_writers_do_not_import_registry_or_ambient_database():
    for rel in (
        "services/inference/measurement_contracts.py",
        "services/inference/execution_events.py",
        "services/inference/validation_records.py",
    ):
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert "assert_model_registered" not in src
        assert "get_db_session" not in src
        assert "os.environ" not in src
        assert "upsert" not in src.lower()
        assert "session.merge" not in src


def test_insert_functions_take_explicit_session():
    from services.inference.execution_events import insert_execution_event
    from services.inference.validation_records import insert_validation_record

    assert "session" in inspect.signature(insert_execution_event).parameters
    assert "session" in inspect.signature(insert_validation_record).parameters
