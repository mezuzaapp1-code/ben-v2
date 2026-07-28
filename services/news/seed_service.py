"""Idempotent curated NewsSource seeding with live feed validation."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from database.connection import get_db_session
from database.models import NewsSource
from services.acquisition.fetch_safe import fetch_safe
from services.acquisition.types import AcquisitionContext, new_acquisition_id
from services.news.feed_url import normalize_and_validate_feed_url
from services.news.seed_catalog import CURATED_NEWS_SOURCES, SeedSource
from services.ops.request_context import attach_request_id, get_request_id
from services.ops.structured_log import log_info, log_warning


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


async def _validate_live_feed(feed_url: str, *, name: str) -> tuple[bool, str | None]:
    """Syntax + fetch_safe probe. Returns (ok, error_message)."""
    normalized, errors = normalize_and_validate_feed_url(feed_url)
    if errors or not normalized:
        return False, "; ".join(errors) if errors else "invalid_feed_url"

    ctx = AcquisitionContext(
        acquisition_id=new_acquisition_id(),
        source_id=uuid.UUID(int=0),
        source_name=name,
        feed_url=normalized,
        category="technology",
        language="en",
        enabled=True,
        started_at=_utc_now(),
        request_id=get_request_id(),
    )
    result = await fetch_safe(ctx)
    if not result.ok:
        err = result.error
        return False, (err.message if err else "fetch_failed")
    body = result.body or b""
    sample = body[:400].lower()
    if b"<rss" not in sample and b"<feed" not in sample and b"<rdf" not in sample:
        # Some feeds are large; also accept content-type hints.
        ct = (result.content_type or "").lower()
        if "xml" not in ct and "rss" not in ct and "atom" not in ct:
            return False, "response_not_rss_or_atom"
    return True, None


async def seed_curated_sources(
    *,
    validate_live: bool = True,
    enable_valid: bool = True,
) -> dict[str, Any]:
    """
    Insert curated feeds that are not already present (by feed_url).
    Invalid feeds are skipped and reported. Existing rows are left unchanged.
    """
    attempted = 0
    created = 0
    existing = 0
    failed: list[dict[str, str]] = []
    enabled_ids: list[str] = []

    for item in CURATED_NEWS_SOURCES:
        attempted += 1
        seed: SeedSource = item
        name = seed["name"]
        raw_url = seed["feed_url"]
        normalized, errors = normalize_and_validate_feed_url(raw_url)
        if errors or not normalized:
            failed.append(
                {
                    "name": name,
                    "feed_url": raw_url,
                    "error": "; ".join(errors) if errors else "invalid_feed_url",
                }
            )
            continue

        async with get_db_session() as session:
            found = (
                await session.execute(select(NewsSource).where(NewsSource.feed_url == normalized))
            ).scalar_one_or_none()
            if found is not None:
                existing += 1
                if enable_valid and not found.enabled:
                    found.enabled = True
                    await session.commit()
                enabled_ids.append(str(found.id))
                continue

        if validate_live:
            ok, err = await _validate_live_feed(normalized, name=name)
            if not ok:
                failed.append({"name": name, "feed_url": normalized, "error": err or "validate_failed"})
                log_warning(
                    "news seed feed validation failed",
                    subsystem="news_seed",
                    operation="seed_curated_sources",
                    outcome="error",
                    source_name=name,
                    error_class="feed_validation_failed",
                )
                continue

        async with get_db_session() as session:
            again = (
                await session.execute(select(NewsSource).where(NewsSource.feed_url == normalized))
            ).scalar_one_or_none()
            if again is not None:
                existing += 1
                enabled_ids.append(str(again.id))
                continue
            row = NewsSource(
                name=name[:256],
                feed_url=normalized[:2048],
                category=seed["category"][:64],
                language=seed["language"][:8],
                enabled=bool(enable_valid and seed["enabled"]),
            )
            session.add(row)
            await session.commit()
            created += 1
            enabled_ids.append(str(row.id))
            log_info(
                "news seed source created",
                subsystem="news_seed",
                operation="seed_curated_sources",
                outcome="ok",
                source_name=name,
                tier=seed.get("tier"),
            )

    payload = {
        "attempted": attempted,
        "created": created,
        "existing": existing,
        "failed": failed,
        "failed_count": len(failed),
        "enabled_or_present": len(enabled_ids),
        "catalog_size": len(CURATED_NEWS_SOURCES),
    }
    return attach_request_id(payload)
