"""Append-only validation persistence for Measurement Foundation V1.

Regrading inserts a new validation_id. Original rows are never updated.
Takes an explicit AsyncSession. Does not consult the product registry.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import ValidationRecordRow
from services.inference.execution_events import (
    _benchmark,
    _parse_dt,
    _result,
    bind_measurement_tenant,
)
from services.inference.measurement_contracts import (
    MeasurementConflict,
    ValidationRecord,
    _json_ready,
)

ENVELOPE_VERSION = "1"


@dataclass(frozen=True)
class PersistedValidationRecord:
    record: ValidationRecord
    recorded_at: datetime
    replayed: bool


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def validation_record_from_immutable(data: Mapping[str, Any]) -> ValidationRecord:
    return ValidationRecord(
        validation_id=uuid.UUID(str(data["validation_id"])),
        org_id=uuid.UUID(str(data["org_id"])),
        workspace_id=uuid.UUID(str(data["workspace_id"])),
        execution_id=data["execution_id"],
        validator_type=data["validator_type"],
        validator_version=data["validator_version"],
        outcome=data["outcome"],
        payload_version=data["payload_version"],
        payload=dict(data.get("payload") or {}),
        result=_result(data.get("result")),
        evaluated_at=_parse_dt(data.get("evaluated_at")),
        evaluated_at_missing_reason=data.get("evaluated_at_missing_reason"),
        score=data.get("score"),
        score_meaning=data.get("score_meaning"),
        judge_execution_id=data.get("judge_execution_id"),
        benchmark=_benchmark(data.get("benchmark")),
    )


def _envelope(record: ValidationRecord) -> dict[str, Any]:
    return {
        "measurement_envelope_version": ENVELOPE_VERSION,
        "immutable": _json_ready(record.immutable_content()),
    }


def _from_row(row: ValidationRecordRow) -> PersistedValidationRecord:
    payload = row.payload or {}
    immutable = payload.get("immutable")
    if not isinstance(immutable, Mapping):
        raise MeasurementConflict("validation envelope is missing immutable content")
    record = validation_record_from_immutable(immutable)
    if record.content_fingerprint() != row.content_fingerprint:
        raise MeasurementConflict("stored validation content fingerprint mismatch")
    return PersistedValidationRecord(record=record, recorded_at=row.recorded_at, replayed=False)


def _row_from_record(record: ValidationRecord) -> ValidationRecordRow:
    result = record.result
    benchmark = record.benchmark
    return ValidationRecordRow(
        validation_id=record.validation_id,
        org_id=record.org_id,
        workspace_id=record.workspace_id,
        execution_id=record.execution_id,
        result_id=result.result_id if result else None,
        result_integrity_digest=result.integrity_digest if result else None,
        validator_type=record.validator_type,
        validator_version=record.validator_version,
        evaluated_at=_as_utc(record.evaluated_at),
        evaluated_at_missing_reason=record.evaluated_at_missing_reason,
        outcome=record.outcome,
        score=record.score,
        score_meaning=record.score_meaning,
        payload_version=record.payload_version,
        judge_execution_id=record.judge_execution_id,
        test_run_id=benchmark.test_run_id if benchmark else None,
        question_id=benchmark.question_id if benchmark else None,
        content_fingerprint=record.content_fingerprint(),
        payload=_envelope(record),
    )


async def insert_validation_record(
    session: AsyncSession,
    record: ValidationRecord,
) -> PersistedValidationRecord:
    """Insert one evaluation. Same id + same content replays; different content conflicts."""
    record.validate()
    await bind_measurement_tenant(session, record.org_id)
    row = _row_from_record(record)
    values = {
        col.key: getattr(row, col.key)
        for col in ValidationRecordRow.__table__.columns
        if col.key != "recorded_at"
    }
    stmt = (
        pg_insert(ValidationRecordRow)
        .values(**values)
        .on_conflict_do_nothing(index_elements=["validation_id"])
        .returning(ValidationRecordRow)
    )
    inserted = (await session.execute(stmt)).scalars().first()
    if inserted is not None:
        loaded = _from_row(inserted)
        return PersistedValidationRecord(
            record=loaded.record,
            recorded_at=loaded.recorded_at,
            replayed=False,
        )
    stored = await session.get(ValidationRecordRow, record.validation_id)
    if stored is None:
        raise MeasurementConflict(
            f"validation_id {record.validation_id} already exists outside this tenant"
        )
    if stored.content_fingerprint != record.content_fingerprint():
        raise MeasurementConflict(
            f"validation_id {record.validation_id} already exists with different content"
        )
    loaded = _from_row(stored)
    return PersistedValidationRecord(
        record=loaded.record,
        recorded_at=loaded.recorded_at,
        replayed=True,
    )


async def get_validation_record(
    session: AsyncSession,
    org_id: uuid.UUID,
    validation_id: uuid.UUID,
) -> PersistedValidationRecord | None:
    await bind_measurement_tenant(session, org_id)
    row = await session.get(ValidationRecordRow, validation_id)
    if row is None or row.org_id != org_id:
        return None
    return _from_row(row)


async def list_validation_records(
    session: AsyncSession,
    org_id: uuid.UUID,
    execution_id: str,
) -> list[PersistedValidationRecord]:
    await bind_measurement_tenant(session, org_id)
    result = await session.execute(
        select(ValidationRecordRow)
        .where(
            ValidationRecordRow.org_id == org_id,
            ValidationRecordRow.execution_id == execution_id,
        )
        .order_by(
            ValidationRecordRow.recorded_at.asc(),
            ValidationRecordRow.validation_id.asc(),
        )
    )
    return [_from_row(row) for row in result.scalars().all()]
