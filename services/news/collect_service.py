"""Single-source collection orchestrator (N3.0)."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

from database.connection import get_db_session
from database.models import NewsSource
from services.acquisition.fetch_safe import fetch_safe
from services.acquisition.protocols import AdapterParseError, SourceAdapter
from services.acquisition.types import (
    ACQUISITION_COLLECT_BUDGET_S,
    AcquisitionContext,
    AcquisitionStage,
    CollectResult,
    make_error,
    new_acquisition_id,
)
from services.news.persist_articles import persist_normalized_items
from services.news.rss_adapter import RssAtomAdapter
from services.ops.request_context import get_request_id
from services.ops.structured_log import log_info, log_warning

# Fail-fast in-process registry: check-and-add under one lock, then run unlocked.
_active_sources: set[uuid.UUID] = set()
_active_guard = asyncio.Lock()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


async def _try_begin_source(source_id: uuid.UUID) -> bool:
    """Atomically claim source_id. Returns False if already collecting."""
    async with _active_guard:
        if source_id in _active_sources:
            return False
        _active_sources.add(source_id)
        return True


async def _end_source(source_id: uuid.UUID) -> None:
    async with _active_guard:
        _active_sources.discard(source_id)


def _result(
    *,
    acquisition_id: str,
    source_id: uuid.UUID,
    status: str,
    adapter_name: str,
    started_at: datetime,
    stage_reached: AcquisitionStage,
    request_id: str | None,
    fetched_bytes: int = 0,
    http_status: int | None = None,
    final_url: str | None = None,
    parsed_count: int = 0,
    normalized_count: int = 0,
    inserted_count: int = 0,
    skipped_count: int = 0,
    failed_count: int = 0,
    error=None,
) -> CollectResult:
    return CollectResult(
        acquisition_id=acquisition_id,
        source_id=source_id,
        status=status,  # type: ignore[arg-type]
        adapter_name=adapter_name,
        started_at=started_at,
        finished_at=_utc_now(),
        stage_reached=stage_reached,
        fetched_bytes=fetched_bytes,
        http_status=http_status,
        final_url=final_url,
        parsed_count=parsed_count,
        normalized_count=normalized_count,
        inserted_count=inserted_count,
        skipped_count=skipped_count,
        failed_count=failed_count,
        error=error,
        request_id=request_id,
    )


def _concurrency_conflict(
    *,
    acquisition_id: str,
    source_id: uuid.UUID,
    adapter_name: str,
    started_at: datetime,
    request_id: str | None,
) -> CollectResult:
    err = make_error(
        acquisition_id,
        stage="load_source",
        error_class="concurrency_conflict",
        message="collection already in progress for this source",
    )
    log_warning(
        "acquisition concurrency conflict",
        subsystem="news_collector",
        category="concurrency_conflict",
        acquisition_id=acquisition_id,
        source_id=str(source_id),
        stage="load_source",
        outcome="error",
    )
    return _result(
        acquisition_id=acquisition_id,
        source_id=source_id,
        status="failed",
        adapter_name=adapter_name,
        started_at=started_at,
        stage_reached="load_source",
        request_id=request_id,
        error=err,
    )


async def _run_collect(
    *,
    acquisition_id: str,
    source_id: uuid.UUID,
    started_at: datetime,
    rid: str | None,
    active_adapter: SourceAdapter,
    adapter_name: str,
) -> CollectResult:
    async with get_db_session() as session:
        row = await session.get(NewsSource, source_id)
        if row is None:
            err = make_error(
                acquisition_id,
                stage="load_source",
                error_class="source_not_found",
                message="News source not found",
            )
            return _result(
                acquisition_id=acquisition_id,
                source_id=source_id,
                status="rejected",
                adapter_name=adapter_name,
                started_at=started_at,
                stage_reached="load_source",
                request_id=rid,
                error=err,
            )
        if not bool(row.enabled):
            err = make_error(
                acquisition_id,
                stage="load_source",
                error_class="source_disabled",
                message="News source is disabled",
            )
            return _result(
                acquisition_id=acquisition_id,
                source_id=source_id,
                status="rejected",
                adapter_name=adapter_name,
                started_at=started_at,
                stage_reached="load_source",
                request_id=rid,
                error=err,
            )
        ctx = AcquisitionContext(
            acquisition_id=acquisition_id,
            source_id=row.id,
            source_name=row.name,
            feed_url=row.feed_url,
            category=row.category,
            language=row.language,
            enabled=bool(row.enabled),
            started_at=started_at,
            request_id=rid,
            adapter_name=adapter_name,
        )

    log_info(
        "acquisition started",
        subsystem="news_collector",
        category="collect",
        acquisition_id=acquisition_id,
        source_id=str(source_id),
        stage="load_source",
        outcome="ok",
    )

    fetch = await fetch_safe(ctx)
    if not fetch.ok:
        log_warning(
            "acquisition fetch failed",
            subsystem="news_collector",
            category=(fetch.error.error_class if fetch.error else "http_error"),
            acquisition_id=acquisition_id,
            source_id=str(source_id),
            stage="fetch",
            outcome="error",
        )
        return _result(
            acquisition_id=acquisition_id,
            source_id=source_id,
            status="failed",
            adapter_name=adapter_name,
            started_at=started_at,
            stage_reached="fetch",
            request_id=rid,
            fetched_bytes=fetch.body_size,
            http_status=fetch.status_code,
            final_url=fetch.final_url,
            error=fetch.error,
        )

    try:
        items = active_adapter.parse(ctx, fetch)
    except AdapterParseError as exc:
        log_warning(
            "acquisition parse failed",
            subsystem="news_collector",
            category=exc.error.error_class,
            acquisition_id=acquisition_id,
            source_id=str(source_id),
            stage=exc.error.stage,
            outcome="error",
        )
        return _result(
            acquisition_id=acquisition_id,
            source_id=source_id,
            status="failed",
            adapter_name=adapter_name,
            started_at=started_at,
            stage_reached=exc.error.stage,
            request_id=rid,
            fetched_bytes=fetch.body_size,
            http_status=fetch.status_code,
            final_url=fetch.final_url,
            error=exc.error,
        )

    parsed_count = len(items)
    normalized_count = len(items)

    persist = await persist_normalized_items(ctx, items)
    if persist.error is not None:
        return _result(
            acquisition_id=acquisition_id,
            source_id=source_id,
            status="failed",
            adapter_name=adapter_name,
            started_at=started_at,
            stage_reached="persist",
            request_id=rid,
            fetched_bytes=fetch.body_size,
            http_status=fetch.status_code,
            final_url=fetch.final_url,
            parsed_count=parsed_count,
            normalized_count=normalized_count,
            inserted_count=0,
            skipped_count=0,
            failed_count=persist.failed_count,
            error=persist.error,
        )

    log_info(
        "acquisition complete",
        subsystem="news_collector",
        category="collect",
        acquisition_id=acquisition_id,
        source_id=str(source_id),
        stage="complete",
        outcome="ok",
        inserted_count=persist.inserted_count,
        skipped_count=persist.skipped_count,
    )
    return _result(
        acquisition_id=acquisition_id,
        source_id=source_id,
        status="succeeded",
        adapter_name=adapter_name,
        started_at=started_at,
        stage_reached="complete",
        request_id=rid,
        fetched_bytes=fetch.body_size,
        http_status=fetch.status_code,
        final_url=fetch.final_url,
        parsed_count=parsed_count,
        normalized_count=normalized_count,
        inserted_count=persist.inserted_count,
        skipped_count=persist.skipped_count,
        failed_count=0,
    )


async def collect_source(
    source_id: uuid.UUID,
    *,
    request_id: str | None = None,
    adapter: SourceAdapter | None = None,
) -> CollectResult:
    acquisition_id = new_acquisition_id()
    started_at = _utc_now()
    rid = request_id if request_id is not None else get_request_id()
    active_adapter: SourceAdapter = adapter or RssAtomAdapter()
    adapter_name = active_adapter.name

    claimed = await _try_begin_source(source_id)
    if not claimed:
        return _concurrency_conflict(
            acquisition_id=acquisition_id,
            source_id=source_id,
            adapter_name=adapter_name,
            started_at=started_at,
            request_id=rid,
        )

    try:
        try:
            return await asyncio.wait_for(
                _run_collect(
                    acquisition_id=acquisition_id,
                    source_id=source_id,
                    started_at=started_at,
                    rid=rid,
                    active_adapter=active_adapter,
                    adapter_name=adapter_name,
                ),
                timeout=ACQUISITION_COLLECT_BUDGET_S,
            )
        except asyncio.TimeoutError:
            err = make_error(
                acquisition_id,
                stage="fetch",
                error_class="timeout",
                message="collection exceeded wall-clock budget",
                retryable=True,
            )
            log_warning(
                "acquisition wall-clock budget exceeded",
                subsystem="news_collector",
                category="timeout",
                acquisition_id=acquisition_id,
                source_id=str(source_id),
                stage="fetch",
                outcome="error",
            )
            return _result(
                acquisition_id=acquisition_id,
                source_id=source_id,
                status="failed",
                adapter_name=adapter_name,
                started_at=started_at,
                stage_reached="fetch",
                request_id=rid,
                error=err,
            )
    finally:
        await _end_source(source_id)
