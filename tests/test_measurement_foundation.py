"""Disposable-Postgres tests for Measurement Foundation V1 persistence."""
from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from services.inference.execution_events import (
    insert_execution_event,
    list_execution_events,
)
from services.inference.measurement_contracts import (
    MEASUREMENT_CONTRACT_VERSION,
    BenchmarkAttachment,
    CostObservation,
    ExecutionConditionsManifest,
    ExecutionEvent,
    ExperimentProtocolRef,
    FrozenInput,
    MeasurementConflict,
    ModelIdentity,
    ProvenanceConflict,
    RequestedConfiguration,
    ResultRef,
    TimingObservation,
    UsageObservation,
    ValidationRecord,
    fingerprint,
)
from services.inference.validation_records import (
    insert_validation_record,
    list_validation_records,
)
from tests.disposable_postgres import DisposablePostgres

ROOT = Path("/workspace")
DB = "mf_persist"
ORG_A = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
ORG_B = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
WS = uuid.UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
NOW = datetime(2026, 9, 19, 21, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def pg() -> Iterator[DisposablePostgres]:
    cluster = DisposablePostgres()
    cluster.start()
    try:
        cluster.create_database(DB)
        cluster.alembic(DB, "upgrade", "head")
        yield cluster
    finally:
        cluster.cleanup()


@asynccontextmanager
async def opened_engine(pg: DisposablePostgres):
    eng = create_async_engine(
        pg.alembic_url(DB),
        echo=False,
        poolclass=NullPool,
        connect_args={"timeout": 10},
    )
    try:
        yield eng
    finally:
        await eng.dispose()


@asynccontextmanager
async def session_for(engine, _org_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
        await session.commit()


def _digest(label: str) -> str:
    return fingerprint({"label": label})


def _event(**overrides) -> ExecutionEvent:
    base = dict(
        event_id=uuid.uuid4(),
        org_id=ORG_A,
        workspace_id=WS,
        execution_id="exec-lab-1",
        event_type="execution_completed",
        origin="operational",
        payload_version="1",
        payload={"raw_result": "hello"},
        observed_at=NOW,
        task_id="task-1",
        request_id="req-1",
        requested_model=ModelIdentity(namespace="local", identifier="unregistered-lab-model"),
        timing=TimingObservation(
            started_at=NOW,
            first_meaningful_output_at=NOW,
            completed_at=NOW,
            ttft_ms=11.0,
            duration_ms=40.0,
            timing_scope="execution",
            timing_source="client_observed",
        ),
        usage=UsageObservation(usage_status="missing", usage_source="missing"),
        cost=CostObservation(cost_status="unknown", cost_source="unknown"),
    )
    base.update(overrides)
    return ExecutionEvent(**base)


def _validation(**overrides) -> ValidationRecord:
    base = dict(
        validation_id=uuid.uuid4(),
        org_id=ORG_A,
        workspace_id=WS,
        execution_id="exec-lab-1",
        validator_type="exact_match",
        validator_version="1",
        outcome="pending",
        payload_version="1",
        payload={"evidence": "not yet scored"},
        evaluated_at=NOW,
    )
    base.update(overrides)
    return ValidationRecord(**base)


def _run_with_engine(pg: DisposablePostgres, fn) -> None:
    async def _main():
        async with opened_engine(pg) as engine:
            await fn(engine)

    asyncio.run(_main())


def test_persistence_writers_never_touch_ambient_database_url():
    for rel in (
        "services/inference/execution_events.py",
        "services/inference/validation_records.py",
    ):
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert "get_db_session" not in src
        assert "os.environ" not in src
        assert "assert_model_registered" not in src
        assert "session.merge" not in src
        assert "on_conflict_do_update" not in src


def test_insert_roundtrip_without_model_call_or_registry(pg: DisposablePostgres) -> None:
    event = _event(call_id=None)

    async def go(engine):
        async with session_for(engine, ORG_A) as session:
            persisted = await insert_execution_event(session, event)
            assert persisted.replayed is False
            assert persisted.recorded_at.tzinfo is not None
            rows = await list_execution_events(session, ORG_A, event.execution_id)
            assert len(rows) == 1
            loaded = rows[0].event
            assert loaded.event_id == event.event_id
            assert loaded.call_id is None
            assert loaded.requested_model.identifier == "unregistered-lab-model"
            assert loaded.usage.usage_status == "missing"
            assert loaded.usage.input_tokens is None
            assert loaded.cost.cost_status == "unknown"
            assert loaded.cost.amount_usd is None
            assert loaded.content_fingerprint() == event.content_fingerprint()
            assert loaded.timing.timing_source == "client_observed"
            assert loaded.timing.ttft_ms == 11.0

    _run_with_engine(pg, go)


def test_optional_call_id_matches_inference_record_without_fk(pg: DisposablePostgres) -> None:
    call_id = str(uuid.uuid4())
    event = _event(
        execution_id="exec-with-call",
        call_id=call_id,
        result=ResultRef(result_id="res-1", kind="complete", call_id=call_id),
        event_type="result_recorded",
    )
    orphan = _event(
        execution_id="exec-orphan-call",
        call_id=str(uuid.uuid4()),
        event_type="call_attempted",
        observed_at=None,
        observed_at_missing_reason="synthetic",
    )

    async def go(engine):
        import asyncpg

        conn = await asyncpg.connect(**pg.asyncpg_kwargs(DB))
        try:
            await conn.execute(
                """
                INSERT INTO ben.inference_call_records (
                  id, execution_id, pipeline, provider, model, outcome,
                  usage_status, cost_status, pricing_version, started_at, finished_at
                ) VALUES (
                  $1::uuid, $2, 'chat', 'openai', 'gpt-4o', 'success',
                  'missing', 'unknown', 'unknown', now(), now()
                )
                """,
                call_id,
                event.execution_id,
            )
        finally:
            await conn.close()
        async with session_for(engine, ORG_A) as session:
            await insert_execution_event(session, event)
            await insert_execution_event(session, orphan)
            matched = await list_execution_events(session, ORG_A, event.execution_id)
            assert matched[0].event.call_id == call_id
            assert matched[0].event.result.call_id == call_id

    _run_with_engine(pg, go)


def test_idempotent_replay_and_conflict(pg: DisposablePostgres) -> None:
    event = _event(execution_id="exec-idemp")
    conflict = _event(
        event_id=event.event_id,
        execution_id="exec-idemp",
        payload={"raw_result": "different"},
    )

    async def go(engine):
        async with session_for(engine, ORG_A) as session:
            first = await insert_execution_event(session, event)
            replay = await insert_execution_event(session, event)
            assert replay.replayed is True
            assert replay.recorded_at == first.recorded_at
            assert replay.event.content_fingerprint() == event.content_fingerprint()
            with pytest.raises(MeasurementConflict, match="different content"):
                await insert_execution_event(session, conflict)
            rows = await list_execution_events(session, ORG_A, "exec-idemp")
            assert len(rows) == 1
            assert rows[0].event.payload["raw_result"] == "hello"

    _run_with_engine(pg, go)


def test_origin_conflict_is_rejected(pg: DisposablePostgres) -> None:
    first = _event(execution_id="exec-origin", origin="operational")
    second = _event(execution_id="exec-origin", origin="unknown")

    async def go(engine):
        async with session_for(engine, ORG_A) as session:
            await insert_execution_event(session, first)
            with pytest.raises(ProvenanceConflict):
                await insert_execution_event(session, second)

    _run_with_engine(pg, go)


def test_controlled_experiment_persists_when_refs_present(pg: DisposablePostgres) -> None:
    event = _event(
        execution_id="exec-lab-controlled",
        origin="controlled_experiment",
        frozen_input=FrozenInput(
            contract_version=MEASUREMENT_CONTRACT_VERSION,
            digest=_digest("q001-input"),
            hash_algorithm="sha256",
            canonicalization_version="json-v1",
            content_ref="artifact:q001",
            logical_fixture_id="Q001",
            immutable_revision="1",
            replayability="replayable",
        ),
        conditions=ExecutionConditionsManifest(
            contract_version=MEASUREMENT_CONTRACT_VERSION,
            digest=_digest("q001-conditions"),
            hash_algorithm="sha256",
            canonicalization_version="json-v1",
            snapshot_ref="cond:1",
        ),
        requested_configuration=RequestedConfiguration(
            contract_version=MEASUREMENT_CONTRACT_VERSION,
            digest=_digest("q001-req"),
            hash_algorithm="sha256",
            canonicalization_version="json-v1",
            requested_model=ModelIdentity(namespace="external", identifier="lab-only-model"),
        ),
        experiment_protocol=ExperimentProtocolRef(
            contract_version=MEASUREMENT_CONTRACT_VERSION,
            digest=_digest("protocol"),
            hash_algorithm="sha256",
            canonicalization_version="json-v1",
            manifest_ref="ben-model-test-v1",
        ),
        benchmark=BenchmarkAttachment(
            test_id="ben-model-test",
            test_version="v1",
            test_run_id="run-77",
            question_id="Q001",
            section_id="core",
        ),
    )

    async def go(engine):
        async with session_for(engine, ORG_A) as session:
            persisted = await insert_execution_event(session, event)
            loaded = persisted.event
            assert loaded.origin == "controlled_experiment"
            assert loaded.benchmark.test_run_id == "run-77"
            assert loaded.benchmark.question_id == "Q001"
            assert loaded.frozen_input.logical_fixture_id == "Q001"
            assert loaded.requested_configuration.requested_model.namespace == "external"

    _run_with_engine(pg, go)


def test_repeated_trials_are_distinct_executions(pg: DisposablePostgres) -> None:
    a = _event(execution_id="trial-a", payload={"trial": 1})
    b = _event(execution_id="trial-b", payload={"trial": 2})

    async def go(engine):
        async with session_for(engine, ORG_A) as session:
            await insert_execution_event(session, a)
            await insert_execution_event(session, b)
            assert len(await list_execution_events(session, ORG_A, "trial-a")) == 1
            assert len(await list_execution_events(session, ORG_A, "trial-b")) == 1

    _run_with_engine(pg, go)


def test_regrade_appends_validation_without_overwriting(pg: DisposablePostgres) -> None:
    event = _event(execution_id="exec-regrade", result=ResultRef(result_id="res-9", kind="complete"))
    first = _validation(
        execution_id="exec-regrade",
        outcome="fail",
        payload={"reason": "mismatch"},
        result=ResultRef(result_id="res-9", kind="complete"),
        benchmark=BenchmarkAttachment(test_run_id="run-77", question_id="Q002"),
    )
    second = _validation(
        execution_id="exec-regrade",
        validator_version="2",
        outcome="pass",
        payload={"reason": "rubric restated"},
        result=ResultRef(result_id="res-9", kind="complete"),
        score=1.0,
        score_meaning="binary_pass",
        benchmark=BenchmarkAttachment(test_run_id="run-77", question_id="Q002"),
    )
    replay_conflict = _validation(
        validation_id=first.validation_id,
        execution_id="exec-regrade",
        outcome="pass",
        payload={"reason": "should conflict"},
    )

    async def go(engine):
        async with session_for(engine, ORG_A) as session:
            await insert_execution_event(session, event)
            a = await insert_validation_record(session, first)
            replay = await insert_validation_record(session, first)
            assert replay.replayed is True
            assert replay.recorded_at == a.recorded_at
            await insert_validation_record(session, second)
            with pytest.raises(MeasurementConflict, match="different content"):
                await insert_validation_record(session, replay_conflict)
            rows = await list_validation_records(session, ORG_A, "exec-regrade")
            by_id = {r.record.validation_id: r.record for r in rows}
            assert set(by_id) == {first.validation_id, second.validation_id}
            assert by_id[first.validation_id].outcome == "fail"
            assert by_id[second.validation_id].outcome == "pass"

    _run_with_engine(pg, go)


def test_tenant_isolation(pg: DisposablePostgres) -> None:
    event = _event(execution_id="exec-tenant", org_id=ORG_A)

    async def go(engine):
        async with session_for(engine, ORG_A) as session:
            await insert_execution_event(session, event)
        async with session_for(engine, ORG_B) as session:
            assert await list_execution_events(session, ORG_B, "exec-tenant") == []
        async with session_for(engine, ORG_A) as session:
            assert len(await list_execution_events(session, ORG_A, "exec-tenant")) == 1

    _run_with_engine(pg, go)


def test_concurrent_same_id_replay(pg: DisposablePostgres) -> None:
    event = _event(execution_id="exec-concurrent")

    async def go(engine):
        async def one() -> object:
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                try:
                    persisted = await insert_execution_event(session, event)
                    await session.commit()
                    return persisted
                except Exception as exc:  # noqa: BLE001
                    await session.rollback()
                    return exc

        results = await asyncio.gather(one(), one())
        ok = [r for r in results if not isinstance(r, Exception)]
        assert len(ok) == 2
        assert sum(1 for r in ok if r.replayed) == 1
        assert sum(1 for r in ok if not r.replayed) == 1
        async with session_for(engine, ORG_A) as session:
            rows = await list_execution_events(session, ORG_A, "exec-concurrent")
            assert len(rows) == 1

    _run_with_engine(pg, go)


def test_concurrent_origin_conflict(pg: DisposablePostgres) -> None:
    operational = _event(execution_id="exec-race-origin", origin="operational")
    unknown = _event(execution_id="exec-race-origin", origin="unknown")

    async def go(engine):
        async def one(event: ExecutionEvent) -> object:
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                try:
                    persisted = await insert_execution_event(session, event)
                    await session.commit()
                    return persisted
                except Exception as exc:  # noqa: BLE001
                    await session.rollback()
                    return exc

        results = await asyncio.gather(one(operational), one(unknown))
        successes = [r for r in results if not isinstance(r, Exception)]
        conflicts = [r for r in results if isinstance(r, ProvenanceConflict)]
        assert len(successes) == 1
        assert len(conflicts) == 1

    _run_with_engine(pg, go)


def test_usage_and_cost_provenance_roundtrip(pg: DisposablePostgres) -> None:
    event = _event(
        execution_id="exec-cost",
        usage=UsageObservation(
            usage_status="exact",
            raw_provider_usage={"prompt_tokens": 8, "completion_tokens": 2, "reasoning_tokens": 5},
            input_tokens=8,
            output_tokens=2,
            reasoning_tokens=5,
            total_tokens=15,
            normalized_input_tokens=8,
            normalized_output_tokens=2,
            usage_source="provider_reported",
        ),
        cost=CostObservation(
            amount_usd=0.0123,
            cost_status="priced",
            cost_source="pricing_table",
            pricing_version="lab-not-authoritative",
            pricing_source="local_fixture",
        ),
        telemetry_completeness="complete",
        execution_outcome="completed",
    )

    async def go(engine):
        async with session_for(engine, ORG_A) as session:
            loaded = (await insert_execution_event(session, event)).event
            assert loaded.usage.raw_provider_usage["reasoning_tokens"] == 5
            assert loaded.usage.usage_source == "provider_reported"
            assert loaded.cost.pricing_source == "local_fixture"
            assert loaded.cost.cost_source == "pricing_table"
            assert loaded.cost.amount_usd == 0.0123

    _run_with_engine(pg, go)


def test_future_test_run_attachment_does_not_require_parallel_ledger(pg: DisposablePostgres) -> None:
    q1 = _event(
        execution_id="exec-q001",
        benchmark=BenchmarkAttachment(
            test_id="ben-model-test",
            test_version="v1",
            test_run_id="run-80",
            question_id="Q001",
        ),
    )
    q2 = _event(
        execution_id="exec-q002",
        benchmark=BenchmarkAttachment(
            test_id="ben-model-test",
            test_version="v1",
            test_run_id="run-80",
            question_id="Q002",
        ),
    )

    async def go(engine):
        async with session_for(engine, ORG_A) as session:
            await insert_execution_event(session, q1)
            await insert_execution_event(session, q2)
            a = await list_execution_events(session, ORG_A, "exec-q001")
            b = await list_execution_events(session, ORG_A, "exec-q002")
            assert a[0].event.benchmark.test_run_id == b[0].event.benchmark.test_run_id == "run-80"
            assert a[0].event.execution_id != b[0].event.execution_id
            assert a[0].event.benchmark.question_id == "Q001"
            assert b[0].event.benchmark.question_id == "Q002"

    _run_with_engine(pg, go)


def test_module_does_not_read_inherited_database_url(pg: DisposablePostgres, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://should-not-be-used.example/db")
    os.environ["DATABASE_URL"] = "postgresql+asyncpg://should-not-be-used.example/db"
    event = _event(execution_id="exec-no-ambient")

    async def go(engine):
        async with session_for(engine, ORG_A) as session:
            await insert_execution_event(session, event)

    _run_with_engine(pg, go)
