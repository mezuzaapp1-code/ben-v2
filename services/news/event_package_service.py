"""Persist and serve EventPackage v1 — the only product-consumer read path for News events."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select

from database.connection import get_db_session
from database.models import NewsEvent, NewsEventPackage
from services.news.event_package import (
    EventPackage,
    event_package_to_dict,
    parse_event_package,
)
from services.ops.request_context import attach_request_id

DEFAULT_LIMIT = 20
MIN_LIMIT = 1
MAX_LIMIT = 100


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


async def publish_event_package(package: EventPackage | dict[str, Any]) -> dict[str, Any]:
    """Validate and persist a new EventPackage version (pipeline / admin write path)."""
    parsed = parse_event_package(package)
    event_id = uuid.UUID(parsed.event_id)
    payload = event_package_to_dict(parsed)

    try:
        async with get_db_session() as session:
            event = await session.get(NewsEvent, event_id)
            if event is None:
                event = NewsEvent(
                    id=event_id,
                    lifecycle=parsed.lifecycle,
                    headline=parsed.headline,
                    happened_at=_coerce_dt(parsed.happened_at),
                    material_updated_at=_coerce_dt(parsed.updated_at) or _utc_now(),
                    current_package_version=0,
                )
                session.add(event)
                await session.flush()

            next_version = int(event.current_package_version) + 1
            payload["package_version"] = next_version
            payload["event_id"] = str(event_id)
            parsed = parse_event_package(payload)
            payload = event_package_to_dict(parsed)

            row = NewsEventPackage(
                event_id=event_id,
                package_version=next_version,
                payload=payload,
                generated_at=_coerce_dt(parsed.provenance.generated_at) or _utc_now(),
            )
            session.add(row)
            event.lifecycle = parsed.lifecycle
            event.headline = parsed.headline
            event.happened_at = _coerce_dt(parsed.happened_at)
            event.material_updated_at = _coerce_dt(parsed.updated_at) or _utc_now()
            event.current_package_version = next_version
            await session.commit()
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to publish event package",
        ) from exc

    return attach_request_id({"package": payload})


async def get_event_package(event_id: uuid.UUID) -> dict[str, Any]:
    """Product-consumer read: current EventPackage only."""
    try:
        async with get_db_session() as session:
            event = await session.get(NewsEvent, event_id)
            if event is None or event.current_package_version < 1:
                raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Event package not found")
            q = select(NewsEventPackage).where(
                NewsEventPackage.event_id == event_id,
                NewsEventPackage.package_version == event.current_package_version,
            )
            row = (await session.execute(q)).scalar_one_or_none()
            if row is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Event package not found")
            package = parse_event_package(row.payload)
            payload = event_package_to_dict(package)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load event package",
        ) from exc
    return attach_request_id({"package": payload})


async def list_event_packages(
    *,
    limit: int = DEFAULT_LIMIT,
    lifecycle: str | None = None,
    brief_eligible: bool | None = None,
    alert_worthy: bool | None = None,
) -> dict[str, Any]:
    """Product-consumer list: current packages only (no raw articles)."""
    if limit < MIN_LIMIT or limit > MAX_LIMIT:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"limit must be between {MIN_LIMIT} and {MAX_LIMIT}",
        )
    try:
        async with get_db_session() as session:
            q = (
                select(NewsEvent, NewsEventPackage)
                .join(
                    NewsEventPackage,
                    (NewsEventPackage.event_id == NewsEvent.id)
                    & (NewsEventPackage.package_version == NewsEvent.current_package_version),
                )
                .where(NewsEvent.current_package_version >= 1)
                .order_by(NewsEvent.material_updated_at.desc(), NewsEvent.id.desc())
            )
            if lifecycle is not None:
                q = q.where(NewsEvent.lifecycle == lifecycle.strip())
            if brief_eligible is not None:
                q = q.where(
                    NewsEventPackage.payload["consumer_hints"]["brief_eligible"].as_boolean()
                    == brief_eligible
                )
            if alert_worthy is not None:
                q = q.where(
                    NewsEventPackage.payload["consumer_hints"]["alert_worthy"].as_boolean()
                    == alert_worthy
                )
            q = q.limit(limit)
            rows = (await session.execute(q)).all()
            items = [
                event_package_to_dict(parse_event_package(pkg_row.payload))
                for _event, pkg_row in rows
            ]
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list event packages",
        ) from exc
    return attach_request_id({"items": items, "next_cursor": None})


def _coerce_dt(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt
