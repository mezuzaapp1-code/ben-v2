"""Bounded News refresh pipeline: collect enabled sources → build EventPackages."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from database.connection import get_db_session
from database.models import NewsSource
from services.news.collect_service import collect_source
from services.news.editorial_ranker import rank_top_event_packages
from services.news.heuristic_event_builder import build_heuristic_event_packages
from services.ops.request_context import attach_request_id, get_request_id
from services.ops.structured_log import log_info, log_warning

DEFAULT_MAX_SOURCES = 20
DEFAULT_LOOKBACK_HOURS = 72
DEFAULT_MAX_ARTICLES = 500
DEFAULT_PER_SOURCE_TIMEOUT_S = 45.0

_pipeline_lock = asyncio.Lock()
_pipeline_active = False


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_pipeline_run_id() -> str:
    return str(uuid.uuid4())


async def run_news_pipeline(
    *,
    max_sources: int = DEFAULT_MAX_SOURCES,
    lookback_hours: int = DEFAULT_LOOKBACK_HOURS,
    max_articles: int = DEFAULT_MAX_ARTICLES,
    per_source_timeout_s: float = DEFAULT_PER_SOURCE_TIMEOUT_S,
    skip_build: bool = False,
    dry_run_build: bool = False,
) -> dict[str, Any]:
    """
    One bounded, overlap-protected refresh cycle.
    Failure of one source does not abort the run.
    """
    global _pipeline_active
    run_id = new_pipeline_run_id()
    started = _utc_now()
    request_id = get_request_id()

    if max_sources < 1 or max_sources > 50:
        return attach_request_id(
            {
                "run_id": run_id,
                "status": "rejected",
                "error": "max_sources must be 1..50",
            }
        )

    async with _pipeline_lock:
        if _pipeline_active:
            log_warning(
                "news pipeline overlap blocked",
                subsystem="news_pipeline",
                operation="run_news_pipeline",
                outcome="rejected",
                run_id=run_id,
            )
            return attach_request_id(
                {
                    "run_id": run_id,
                    "status": "rejected",
                    "error_class": "concurrency_conflict",
                    "message": "news pipeline already in progress",
                }
            )
        _pipeline_active = True

    collect_results: list[dict[str, Any]] = []
    sources_attempted = 0
    sources_succeeded = 0
    sources_failed = 0
    articles_inserted = 0
    duplicates_skipped = 0

    try:
        async with get_db_session() as session:
            rows = (
                await session.execute(
                    select(NewsSource)
                    .where(NewsSource.enabled.is_(True))
                    .order_by(NewsSource.name.asc(), NewsSource.id.asc())
                    .limit(max_sources)
                )
            ).scalars().all()
            source_ids = [(row.id, row.name) for row in rows]

        log_info(
            "news pipeline started",
            subsystem="news_pipeline",
            operation="run_news_pipeline",
            outcome="ok",
            run_id=run_id,
            source_count=len(source_ids),
            max_sources=max_sources,
        )

        for source_id, source_name in source_ids:
            sources_attempted += 1
            try:
                result = await asyncio.wait_for(
                    collect_source(source_id, request_id=request_id),
                    timeout=per_source_timeout_s,
                )
                payload = result.to_dict()
                payload["source_name"] = source_name
                collect_results.append(payload)
                if result.status == "succeeded":
                    sources_succeeded += 1
                    articles_inserted += int(result.inserted_count or 0)
                    duplicates_skipped += int(result.skipped_count or 0)
                else:
                    sources_failed += 1
            except asyncio.TimeoutError:
                sources_failed += 1
                collect_results.append(
                    {
                        "source_id": str(source_id),
                        "source_name": source_name,
                        "status": "failed",
                        "error_class": "timeout",
                        "message": f"per-source timeout after {per_source_timeout_s}s",
                    }
                )
            except Exception as exc:  # noqa: BLE001
                sources_failed += 1
                collect_results.append(
                    {
                        "source_id": str(source_id),
                        "source_name": source_name,
                        "status": "failed",
                        "error_class": type(exc).__name__,
                        "message": str(exc)[:240],
                    }
                )

        build_result: dict[str, Any] | None = None
        if not skip_build:
            build_result = await build_heuristic_event_packages(
                lookback_hours=lookback_hours,
                max_articles=max_articles,
                dry_run=dry_run_build,
            )

        top = await rank_top_event_packages(top_n=10)
        top_count = len((top or {}).get("items") or [])

        finished = _utc_now()
        summary = {
            "run_id": run_id,
            "status": "completed",
            "started_at": started.isoformat(),
            "finished_at": finished.isoformat(),
            "duration_ms": int((finished - started).total_seconds() * 1000),
            "sources_attempted": sources_attempted,
            "sources_succeeded": sources_succeeded,
            "sources_failed": sources_failed,
            "articles_inserted": articles_inserted,
            "duplicates_skipped": duplicates_skipped,
            "collect_results": collect_results,
            "build": build_result,
            "top10_count": top_count,
            "events_created": (build_result or {}).get("events_created"),
            "events_updated": (build_result or {}).get("events_updated"),
            "packages_published": (build_result or {}).get("packages_published"),
            "unchanged_events_skipped": (build_result or {}).get("unchanged_events_skipped"),
        }
        log_info(
            "news pipeline completed",
            subsystem="news_pipeline",
            operation="run_news_pipeline",
            outcome="ok",
            run_id=run_id,
            sources_attempted=sources_attempted,
            sources_succeeded=sources_succeeded,
            sources_failed=sources_failed,
            articles_inserted=articles_inserted,
            top10_count=top_count,
        )
        return attach_request_id(summary)
    finally:
        async with _pipeline_lock:
            _pipeline_active = False
