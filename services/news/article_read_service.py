"""N4.0 internal NewsArticle read API — list/detail with keyset pagination."""
from __future__ import annotations

import base64
import binascii
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import and_, or_, select

from database.connection import get_db_session
from database.models import NewsArticle
from services.ops.request_context import attach_request_id

CURSOR_VERSION = 1
DEFAULT_LIMIT = 20
MIN_LIMIT = 1
MAX_LIMIT = 100

_ARTICLE_FIELDS = (
    "id",
    "source_id",
    "guid",
    "title",
    "url",
    "summary",
    "image_url",
    "published_at",
    "category",
    "created_at",
)


def normalize_category_filter(raw: str | None) -> str | None:
    """Strip category; empty-after-strip means filter omitted."""
    if raw is None:
        return None
    stripped = raw.strip()
    if not stripped:
        return None
    if len(stripped) > 64:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="category must be at most 64 characters",
        )
    return stripped


def active_filters(
    *,
    source_id: uuid.UUID | None,
    category: str | None,
) -> dict[str, str]:
    filters: dict[str, str] = {}
    if source_id is not None:
        filters["source_id"] = str(source_id)
    if category is not None:
        filters["category"] = category
    return filters


def _article_payload(row: NewsArticle) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "source_id": str(row.source_id),
        "guid": row.guid,
        "title": row.title,
        "url": row.url,
        "summary": row.summary,
        "image_url": row.image_url,
        "published_at": row.published_at.isoformat() if row.published_at else None,
        "category": row.category,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(token: str) -> bytes:
    pad = "=" * (-len(token) % 4)
    return base64.urlsafe_b64decode(token + pad)


def encode_cursor(
    *,
    published_at: datetime | None,
    article_id: uuid.UUID,
    filters: dict[str, str],
) -> str:
    payload: dict[str, Any] = {
        "v": CURSOR_VERSION,
        "p": published_at.isoformat() if published_at is not None else None,
        "i": str(article_id),
        "f": dict(filters),
    }
    return _b64url_encode(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    )


def decode_cursor(token: str, *, request_filters: dict[str, str]) -> tuple[datetime | None, uuid.UUID]:
    if not token or not isinstance(token, str):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid_cursor")
    try:
        raw = _b64url_decode(token.strip())
        data = json.loads(raw.decode("utf-8"))
    except (binascii.Error, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid_cursor"
        ) from exc

    if not isinstance(data, dict):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid_cursor")

    version = data.get("v")
    if version != CURSOR_VERSION:
        if isinstance(version, int) and version != CURSOR_VERSION:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="unsupported_cursor_version",
            )
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid_cursor")

    if "p" not in data or "i" not in data or "f" not in data:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid_cursor")

    published_raw = data["p"]
    article_raw = data["i"]
    cursor_filters = data["f"]

    if published_raw is not None and not isinstance(published_raw, str):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid_cursor")
    if not isinstance(article_raw, str):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid_cursor")
    if not isinstance(cursor_filters, dict):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid_cursor")

    # Normalize cursor filter values to strings for exact match.
    normalized_cursor_filters: dict[str, str] = {}
    for key, value in cursor_filters.items():
        if key not in ("source_id", "category"):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid_cursor")
        if not isinstance(value, str):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid_cursor")
        normalized_cursor_filters[key] = value

    if normalized_cursor_filters != request_filters:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="cursor_filter_mismatch",
        )

    published_at: datetime | None
    if published_raw is None:
        published_at = None
    else:
        try:
            published_at = datetime.fromisoformat(published_raw)
        except ValueError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid_cursor"
            ) from exc
        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=timezone.utc)

    try:
        article_id = uuid.UUID(article_raw)
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid_cursor"
        ) from exc

    return published_at, article_id


def _keyset_predicate(published_at: datetime | None, article_id: uuid.UUID):
    if published_at is not None:
        return or_(
            NewsArticle.published_at < published_at,
            and_(
                NewsArticle.published_at == published_at,
                NewsArticle.id < article_id,
            ),
            NewsArticle.published_at.is_(None),
        )
    return and_(
        NewsArticle.published_at.is_(None),
        NewsArticle.id < article_id,
    )


async def list_articles(
    *,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
    source_id: uuid.UUID | None = None,
    category: str | None = None,
) -> dict[str, Any]:
    if limit < MIN_LIMIT or limit > MAX_LIMIT:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"limit must be between {MIN_LIMIT} and {MAX_LIMIT}",
        )

    category_filter = normalize_category_filter(category)
    filters = active_filters(source_id=source_id, category=category_filter)

    cursor_published: datetime | None = None
    cursor_id: uuid.UUID | None = None
    if cursor is not None and cursor.strip() != "":
        cursor_published, cursor_id = decode_cursor(cursor, request_filters=filters)

    try:
        async with get_db_session() as session:
            q = select(NewsArticle)
            if source_id is not None:
                q = q.where(NewsArticle.source_id == source_id)
            if category_filter is not None:
                q = q.where(NewsArticle.category == category_filter)
            if cursor_id is not None:
                q = q.where(_keyset_predicate(cursor_published, cursor_id))
            q = q.order_by(
                NewsArticle.published_at.desc().nulls_last(),
                NewsArticle.id.desc(),
            ).limit(limit + 1)
            rows = list((await session.execute(q)).scalars().all())
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 — never expose DB exception text
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list news articles",
        ) from exc

    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = None
    if has_more and page:
        last = page[-1]
        next_cursor = encode_cursor(
            published_at=last.published_at,
            article_id=last.id,
            filters=filters,
        )

    payload = {
        "items": [_article_payload(r) for r in page],
        "next_cursor": next_cursor,
    }
    return attach_request_id(payload)


async def get_article(article_id: uuid.UUID) -> dict[str, Any]:
    try:
        async with get_db_session() as session:
            row = await session.get(NewsArticle, article_id)
            if row is None:
                raise HTTPException(
                    status.HTTP_404_NOT_FOUND,
                    detail="News article not found",
                )
            payload = _article_payload(row)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 — never expose DB exception text
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load news article",
        ) from exc
    return attach_request_id(payload)


# Exported for architecture tests / clarity
ARTICLE_RESPONSE_FIELDS = _ARTICLE_FIELDS
