"""CRUD for system-managed NewsSource rows (no collector, no RLS org binding)."""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from database.connection import get_db_session
from database.models import NewsSource
from services.news.feed_url import normalize_and_validate_feed_url
from services.ops.request_context import attach_request_id


def _source_payload(row: NewsSource) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "name": row.name,
        "feed_url": row.feed_url,
        "category": row.category,
        "language": row.language,
        "enabled": bool(row.enabled),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _require_valid_feed_url(raw: str) -> str:
    normalized, errors = normalize_and_validate_feed_url(raw)
    if errors or not normalized:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "invalid_feed_url", "errors": errors},
        )
    return normalized


async def list_sources(
    *,
    enabled: bool | None = None,
    category: str | None = None,
    language: str | None = None,
) -> dict[str, Any]:
    async with get_db_session() as session:
        q = select(NewsSource).order_by(NewsSource.name.asc(), NewsSource.id.asc())
        if enabled is not None:
            q = q.where(NewsSource.enabled.is_(enabled))
        if category is not None:
            q = q.where(NewsSource.category == category.strip())
        if language is not None:
            q = q.where(NewsSource.language == language.strip().lower())
        rows = (await session.execute(q)).scalars().all()
        payload = {"sources": [_source_payload(r) for r in rows]}
    return attach_request_id(payload)


async def get_source(source_id: uuid.UUID) -> dict[str, Any]:
    async with get_db_session() as session:
        row = await session.get(NewsSource, source_id)
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "News source not found")
        payload = _source_payload(row)
    return attach_request_id(payload)


async def create_source(
    *,
    name: str,
    feed_url: str,
    category: str,
    language: str = "en",
    enabled: bool = True,
) -> dict[str, Any]:
    title = (name or "").strip()
    cat = (category or "").strip()
    lang = (language or "en").strip().lower()
    if not title:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "name is required")
    if not cat:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "category is required")
    if not (2 <= len(lang) <= 8):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "language must be 2–8 characters")
    url = _require_valid_feed_url(feed_url)

    async with get_db_session() as session:
        row = NewsSource(
            name=title[:256],
            feed_url=url[:2048],
            category=cat[:64],
            language=lang[:8],
            enabled=bool(enabled),
        )
        session.add(row)
        try:
            await session.flush()
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail="A news source with this feed_url already exists",
            ) from exc
        payload = _source_payload(row)
    return attach_request_id(payload)


async def update_source(
    source_id: uuid.UUID,
    *,
    name: str | None = None,
    feed_url: str | None = None,
    category: str | None = None,
    language: str | None = None,
) -> dict[str, Any]:
    async with get_db_session() as session:
        row = await session.get(NewsSource, source_id)
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "News source not found")

        if name is not None:
            title = name.strip()
            if not title:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "name is required")
            row.name = title[:256]
        if feed_url is not None:
            row.feed_url = _require_valid_feed_url(feed_url)[:2048]
        if category is not None:
            cat = category.strip()
            if not cat:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "category is required")
            row.category = cat[:64]
        if language is not None:
            lang = language.strip().lower()
            if not (2 <= len(lang) <= 8):
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    "language must be 2–8 characters",
                )
            row.language = lang[:8]

        try:
            await session.flush()
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail="A news source with this feed_url already exists",
            ) from exc
        payload = _source_payload(row)
    return attach_request_id(payload)


async def set_enabled(source_id: uuid.UUID, *, enabled: bool) -> dict[str, Any]:
    async with get_db_session() as session:
        row = await session.get(NewsSource, source_id)
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "News source not found")
        row.enabled = bool(enabled)
        await session.flush()
        await session.commit()
        payload = _source_payload(row)
    return attach_request_id(payload)
