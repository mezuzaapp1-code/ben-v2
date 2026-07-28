"""Tests for curated News seed + bounded pipeline runner."""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:5432/test")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-openai")

import main  # noqa: E402
from services.news.seed_catalog import CURATED_NEWS_SOURCES  # noqa: E402


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_clerk")
    monkeypatch.setenv("ENFORCE_AUTH", "false")
    monkeypatch.setenv("AUTH_SHADOW_MODE", "true")
    monkeypatch.setenv("BEN_ANONYMOUS_ORG_ID", "00000000-0000-0000-0000-000000000001")
    monkeypatch.setenv("BEN_NEWS_CRON_SECRET", "test-cron-secret")
    monkeypatch.delenv("BEN_LOCAL_BETA_MODE", raising=False)


def test_curated_catalog_size_and_categories():
    assert 10 <= len(CURATED_NEWS_SOURCES) <= 20
    for item in CURATED_NEWS_SOURCES:
        assert item["category"] in {"ai", "technology", "tech"}
        assert item["language"] == "en"
        assert item["feed_url"].startswith("https://")
        assert item["name"]
        assert item["tier"] in {"official", "publication"}


@pytest.mark.asyncio
async def test_seed_idempotent_skips_existing():
    from services.news.seed_service import seed_curated_sources

    existing = MagicMock()
    existing.id = uuid.uuid4()
    existing.enabled = True

    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    result = MagicMock()
    result.scalar_one_or_none.return_value = existing
    session.execute = AsyncMock(return_value=result)

    with patch("services.news.seed_service.get_db_session", return_value=session):
        with patch("services.news.seed_service._validate_live_feed", new=AsyncMock()) as validate:
            out = await seed_curated_sources(validate_live=True)
            validate.assert_not_called()
    assert out["created"] == 0
    assert out["existing"] == len(CURATED_NEWS_SOURCES)
    assert out["failed_count"] == 0


@pytest.mark.asyncio
async def test_seed_skips_invalid_feed():
    from services.news.seed_service import seed_curated_sources

    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=result)
    session.add = MagicMock()
    session.commit = AsyncMock()

    async def _fail(*_a, **_k):
        return False, "fetch_failed"

    with patch("services.news.seed_service.get_db_session", return_value=session):
        with patch("services.news.seed_service._validate_live_feed", side_effect=_fail):
            out = await seed_curated_sources(validate_live=True)
    assert out["created"] == 0
    assert out["failed_count"] == len(CURATED_NEWS_SOURCES)
    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_pipeline_overlap_rejected():
    from services.news import pipeline_service

    pipeline_service._pipeline_active = True
    try:
        out = await pipeline_service.run_news_pipeline(max_sources=1)
        assert out["status"] == "rejected"
        assert out["error_class"] == "concurrency_conflict"
    finally:
        pipeline_service._pipeline_active = False


@pytest.mark.asyncio
async def test_pipeline_isolates_source_failure():
    from services.acquisition.types import CollectResult, make_error, new_acquisition_id
    from services.news import pipeline_service
    from datetime import datetime, timezone

    sid_ok = uuid.uuid4()
    sid_bad = uuid.uuid4()
    now = datetime.now(timezone.utc)

    ok = CollectResult(
        acquisition_id=new_acquisition_id(),
        source_id=sid_ok,
        status="succeeded",
        adapter_name="rss",
        started_at=now,
        finished_at=now,
        stage_reached="complete",
        inserted_count=3,
        skipped_count=1,
    )
    bad = CollectResult(
        acquisition_id=new_acquisition_id(),
        source_id=sid_bad,
        status="failed",
        adapter_name="rss",
        started_at=now,
        finished_at=now,
        stage_reached="fetch",
        error=make_error(
            new_acquisition_id(),
            stage="fetch",
            error_class="timeout",
            message="timeout",
            retryable=True,
        ),
    )

    async def _collect(source_id, request_id=None):
        if source_id == sid_ok:
            return ok
        return bad

    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    row_ok = MagicMock(id=sid_ok, name="OK")
    row_bad = MagicMock(id=sid_bad, name="BAD")
    exec_result = MagicMock()
    exec_result.scalars.return_value.all.return_value = [row_ok, row_bad]
    session.execute = AsyncMock(return_value=exec_result)

    with patch("services.news.pipeline_service.get_db_session", return_value=session):
        with patch("services.news.pipeline_service.collect_source", side_effect=_collect):
            with patch(
                "services.news.pipeline_service.build_heuristic_event_packages",
                new=AsyncMock(
                    return_value={
                        "events_created": 2,
                        "events_updated": 0,
                        "packages_published": 2,
                        "unchanged_events_skipped": 0,
                    }
                ),
            ):
                with patch(
                    "services.news.pipeline_service.rank_top_event_packages",
                    new=AsyncMock(return_value={"items": [{"rank": 1}, {"rank": 2}]}),
                ):
                    out = await pipeline_service.run_news_pipeline(max_sources=10)

    assert out["status"] == "completed"
    assert out["sources_attempted"] == 2
    assert out["sources_succeeded"] == 1
    assert out["sources_failed"] == 1
    assert out["articles_inserted"] == 3
    assert out["duplicates_skipped"] == 1
    assert out["top10_count"] == 2


def test_pipeline_endpoint_accepts_cron_secret():
    client = TestClient(main.app)
    with patch(
        "routers.news_sources.run_news_pipeline",
        new=AsyncMock(return_value={"run_id": "r1", "status": "completed", "top10_count": 0}),
    ):
        resp = client.post(
            "/api/internal/news/pipeline/run",
            headers={"X-BEN-News-Cron-Secret": "test-cron-secret"},
            json={"max_sources": 5},
        )
    assert resp.status_code == 200
    assert resp.json()["auth_mode"] == "cron_secret"


def test_pipeline_endpoint_rejects_missing_auth():
    client = TestClient(main.app)
    resp = client.post("/api/internal/news/pipeline/run", json={"max_sources": 5})
    assert resp.status_code in (401, 403)
