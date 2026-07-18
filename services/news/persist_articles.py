"""Idempotent NewsArticle persistence (ON CONFLICT DO NOTHING)."""
from __future__ import annotations

from sqlalchemy.dialects.postgresql import insert as pg_insert

from database.connection import get_db_session
from database.models import NewsArticle
from services.acquisition.types import (
    AcquisitionContext,
    NormalizedItem,
    PersistResult,
    make_error,
)
from services.ops.structured_log import log_info, log_warning


async def persist_normalized_items(
    ctx: AcquisitionContext,
    items: list[NormalizedItem],
) -> PersistResult:
    acquisition_id = ctx.acquisition_id
    source_id = ctx.source_id
    attempted = len(items)

    if attempted == 0:
        return PersistResult(
            acquisition_id=acquisition_id,
            source_id=source_id,
            attempted_count=0,
            inserted_count=0,
            skipped_count=0,
            failed_count=0,
        )

    rows = [
        {
            "source_id": item.source_id,
            "guid": item.guid[:1024],
            "title": item.title[:1024],
            "url": item.canonical_url[:2048],
            "summary": item.summary,
            "image_url": item.image_url[:2048] if item.image_url else None,
            "published_at": item.published_at,
            "category": item.category[:64],
        }
        for item in items
    ]

    try:
        async with get_db_session() as session:
            stmt = (
                pg_insert(NewsArticle)
                .values(rows)
                .on_conflict_do_nothing(constraint="uq_news_articles_source_guid")
                .returning(NewsArticle.id)
            )
            result = await session.execute(stmt)
            inserted_ids = result.scalars().all()
            inserted = len(inserted_ids)
            if inserted > attempted:
                inserted = attempted
            skipped = attempted - inserted
            await session.commit()
            log_info(
                "acquisition persist ok",
                subsystem="news_collector",
                category="collect",
                acquisition_id=acquisition_id,
                source_id=str(source_id),
                stage="persist",
                outcome="ok",
                inserted_count=inserted,
                skipped_count=skipped,
                attempted_count=attempted,
            )
            return PersistResult(
                acquisition_id=acquisition_id,
                source_id=source_id,
                attempted_count=attempted,
                inserted_count=inserted,
                skipped_count=skipped,
                failed_count=0,
            )
    except Exception:  # noqa: BLE001 — do not expose DB exception text to API
        log_warning(
            "acquisition persist failed",
            subsystem="news_collector",
            category="persist_error",
            acquisition_id=acquisition_id,
            source_id=str(source_id),
            stage="persist",
            outcome="error",
        )
        return PersistResult(
            acquisition_id=acquisition_id,
            source_id=source_id,
            attempted_count=attempted,
            inserted_count=0,
            skipped_count=0,
            failed_count=attempted,
            error=make_error(
                acquisition_id,
                stage="persist",
                error_class="persist_error",
                message="persist failed",
            ),
        )
