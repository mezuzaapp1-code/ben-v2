"""Persist EventUnderstanding artifacts (Phase 1b) — Intelligence-owned table only."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from database.connection import get_db_session
from database.models import EventUnderstandingRow
from services.intelligence.contracts import (
    EventUnderstanding,
    materialize_event_understanding,
    semantic_fingerprint,
)
from services.intelligence.taxonomy import CLASSIFIER_VERSION, TEMPLATE_VERSION
from services.news.event_package import EventPackage, parse_event_package
from services.ops.request_context import attach_request_id


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _coerce_dt(value: datetime | str | None) -> datetime:
    if value is None:
        return _utc_now()
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


async def get_event_understanding(
    event_id: uuid.UUID | str,
    *,
    package_version: int | None = None,
    classifier_version: str = CLASSIFIER_VERSION,
    template_version: str = TEMPLATE_VERSION,
) -> dict[str, Any]:
    try:
        eid = uuid.UUID(str(event_id))
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid event_id") from exc

    async with get_db_session() as session:
        q = select(EventUnderstandingRow).where(
            EventUnderstandingRow.event_id == eid,
            EventUnderstandingRow.classifier_version == classifier_version,
            EventUnderstandingRow.template_version == template_version,
        )
        if package_version is not None:
            q = q.where(EventUnderstandingRow.package_version == int(package_version))
        else:
            q = q.order_by(EventUnderstandingRow.package_version.desc())
        row = (await session.execute(q.limit(1))).scalar_one_or_none()

    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Event understanding not found")
    return attach_request_id({"understanding": row.payload})


async def upsert_event_understanding(
    understanding: EventUnderstanding | dict[str, Any],
) -> dict[str, Any]:
    """Idempotent insert by identity; returns stored payload (created or existing)."""
    parsed = (
        understanding
        if isinstance(understanding, EventUnderstanding)
        else EventUnderstanding.model_validate(understanding)
    )
    eid = uuid.UUID(str(parsed.event_id))
    payload = parsed.model_dump(mode="json")
    created_at = _coerce_dt(parsed.created_at)

    stmt = (
        pg_insert(EventUnderstandingRow)
        .values(
            event_id=eid,
            package_version=int(parsed.package_version),
            classifier_version=parsed.classifier_version,
            template_version=parsed.template_version,
            primary_event_type=parsed.primary_event_type,
            payload=payload,
            created_at=created_at,
        )
        .on_conflict_do_nothing(
            constraint="uq_event_understandings_identity",
        )
        .returning(EventUnderstandingRow.id)
    )

    async with get_db_session() as session:
        inserted = (await session.execute(stmt)).first()
        await session.commit()

        row = (
            await session.execute(
                select(EventUnderstandingRow).where(
                    EventUnderstandingRow.event_id == eid,
                    EventUnderstandingRow.package_version == int(parsed.package_version),
                    EventUnderstandingRow.classifier_version == parsed.classifier_version,
                    EventUnderstandingRow.template_version == parsed.template_version,
                )
            )
        ).scalar_one()

    return attach_request_id(
        {
            "understanding": row.payload,
            "created": inserted is not None,
            "identity": {
                "event_id": str(eid),
                "package_version": int(parsed.package_version),
                "classifier_version": parsed.classifier_version,
                "template_version": parsed.template_version,
            },
        }
    )


async def materialize_and_store(
    package: EventPackage | dict[str, Any],
    *,
    created_at: datetime | None = None,
) -> dict[str, Any]:
    """Materialize from package and persist idempotently. Does not mutate package."""
    if isinstance(package, dict):
        original = dict(package)
    else:
        original = package.model_dump(mode="json")
    understanding = materialize_event_understanding(package, created_at=created_at)
    # Prove EventPackage dict identity unchanged when dict input
    if isinstance(package, dict):
        assert package == original
    result = await upsert_event_understanding(understanding)
    result["semantic_fingerprint"] = semantic_fingerprint(understanding)
    return result


async def materialize_from_stored_package(
    event_id: uuid.UUID | str,
    *,
    package_version: int | None = None,
) -> dict[str, Any]:
    """Load EventPackage from News store, materialize, persist understanding."""
    from services.news.event_package_service import get_event_package

    envelope = await get_event_package(event_id)
    package = envelope.get("package")
    if not package:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Event package not found")
    parsed = parse_event_package(package)
    if package_version is not None and int(parsed.package_version) != int(package_version):
        # Explicit version request: only accept matching current payload for V1
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail="Requested package_version not available as current package",
        )
    return await materialize_and_store(parsed)
