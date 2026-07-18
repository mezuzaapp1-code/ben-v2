"""N3.0 collect orchestrator + internal collect route."""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:5432/test")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-openai")

import main  # noqa: E402
from services.acquisition.types import (  # noqa: E402
    AcquisitionContext,
    CollectResult,
    FetchResult,
    NormalizedItem,
    PersistResult,
    make_error,
    new_acquisition_id,
)
from services.news.collect_service import collect_source  # noqa: E402

ORG_A = "11111111-1111-1111-1111-111111111111"
SOURCE_ID = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_clerk")
    monkeypatch.setenv("ENFORCE_AUTH", "false")
    monkeypatch.setenv("AUTH_SHADOW_MODE", "true")
    monkeypatch.setenv("BEN_ANONYMOUS_ORG_ID", "00000000-0000-0000-0000-000000000001")
    monkeypatch.delenv("BEN_LOCAL_BETA_MODE", raising=False)


def _admin_claims():
    return {
        "user_id": "user_1",
        "email": "a@b.com",
        "org_id": ORG_A,
        "org_role": "org:admin",
    }


def test_collect_endpoint_requires_auth():
    client = TestClient(main.app)
    res = client.post(f"/api/internal/news/sources/{SOURCE_ID}/collect")
    assert res.status_code == 401


def test_collect_endpoint_forbidden_for_member():
    claims = {**_admin_claims(), "org_role": "org:member"}
    with patch(
        "auth.beta_gate.authenticate_request",
        return_value=("auth_valid", claims, True),
    ):
        client = TestClient(main.app)
        res = client.post(
            f"/api/internal/news/sources/{SOURCE_ID}/collect",
            headers={"Authorization": "Bearer t"},
        )
    assert res.status_code == 403


def test_collect_endpoint_success_shape():
    started = datetime.now(timezone.utc)
    result = CollectResult(
        acquisition_id=new_acquisition_id(),
        source_id=SOURCE_ID,
        status="succeeded",
        adapter_name="rss_atom",
        started_at=started,
        finished_at=started,
        stage_reached="complete",
        fetched_bytes=10,
        http_status=200,
        final_url="https://example.com/feed",
        parsed_count=2,
        normalized_count=2,
        inserted_count=1,
        skipped_count=1,
        failed_count=0,
    )
    with patch(
        "auth.beta_gate.authenticate_request",
        return_value=("auth_valid", _admin_claims(), True),
    ), patch(
        "routers.news_sources.collect_source",
        new_callable=AsyncMock,
        return_value=result,
    ):
        client = TestClient(main.app)
        res = client.post(
            f"/api/internal/news/sources/{SOURCE_ID}/collect",
            headers={"Authorization": "Bearer t"},
        )
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "succeeded"
    assert body["adapter_name"] == "rss_atom"
    assert body["inserted_count"] == 1
    assert body["skipped_count"] == 1
    assert "acquisition_id" in body
    assert body["source_id"] == str(SOURCE_ID)


def test_collect_endpoint_maps_not_found():
    started = datetime.now(timezone.utc)
    aid = new_acquisition_id()
    result = CollectResult(
        acquisition_id=aid,
        source_id=SOURCE_ID,
        status="rejected",
        adapter_name="rss_atom",
        started_at=started,
        finished_at=started,
        stage_reached="load_source",
        error=make_error(
            aid,
            stage="load_source",
            error_class="source_not_found",
            message="News source not found",
        ),
    )
    with patch(
        "auth.beta_gate.authenticate_request",
        return_value=("auth_valid", _admin_claims(), True),
    ), patch(
        "routers.news_sources.collect_source",
        new_callable=AsyncMock,
        return_value=result,
    ):
        client = TestClient(main.app)
        res = client.post(
            f"/api/internal/news/sources/{SOURCE_ID}/collect",
            headers={"Authorization": "Bearer t"},
        )
    assert res.status_code == 404
    assert res.json()["error"]["error_class"] == "source_not_found"


@pytest.mark.asyncio
async def test_collect_source_disabled():
    source = MagicMock()
    source.id = SOURCE_ID
    source.name = "S"
    source.feed_url = "https://example.com/feed"
    source.category = "tech"
    source.language = "en"
    source.enabled = False

    session = AsyncMock()
    session.get = AsyncMock(return_value=source)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)

    with patch("services.news.collect_service.get_db_session", return_value=session):
        result = await collect_source(SOURCE_ID)
    assert result.status == "rejected"
    assert result.error.error_class == "source_disabled"


@pytest.mark.asyncio
async def test_collect_same_source_fail_fast_concurrency():
    source = MagicMock()
    source.id = SOURCE_ID
    source.name = "S"
    source.feed_url = "https://example.com/feed"
    source.category = "tech"
    source.language = "en"
    source.enabled = True

    session = AsyncMock()
    session.get = AsyncMock(return_value=source)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)

    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_fetch(ctx: AcquisitionContext):
        started.set()
        await release.wait()
        return FetchResult(
            acquisition_id=ctx.acquisition_id,
            ok=True,
            requested_url=ctx.feed_url,
            final_url=ctx.feed_url,
            status_code=200,
            content_type="application/rss+xml",
            body=b"<rss/>",
            body_size=6,
        )

    adapter = MagicMock()
    adapter.name = "rss_atom"
    adapter.parse = MagicMock(
        return_value=[
            NormalizedItem(
                acquisition_id="x",
                source_id=SOURCE_ID,
                guid="g1",
                canonical_url="https://example.com/1",
                title="T",
                category="tech",
            )
        ]
    )

    with patch("services.news.collect_service.get_db_session", return_value=session), patch(
        "services.news.collect_service.fetch_safe",
        side_effect=slow_fetch,
    ), patch(
        "services.news.collect_service.persist_normalized_items",
        new_callable=AsyncMock,
        return_value=PersistResult(
            acquisition_id="x",
            source_id=SOURCE_ID,
            attempted_count=1,
            inserted_count=1,
            skipped_count=0,
            failed_count=0,
        ),
    ):
        task1 = asyncio.create_task(collect_source(SOURCE_ID, adapter=adapter))
        await started.wait()
        result2 = await collect_source(SOURCE_ID, adapter=adapter)
        assert result2.status == "failed"
        assert result2.error is not None
        assert result2.error.error_class == "concurrency_conflict"
        release.set()
        result1 = await task1
        assert result1.status == "succeeded"


@pytest.mark.asyncio
async def test_collect_source_happy_path():
    source = MagicMock()
    source.id = SOURCE_ID
    source.name = "S"
    source.feed_url = "https://example.com/feed"
    source.category = "tech"
    source.language = "en"
    source.enabled = True

    session = AsyncMock()
    session.get = AsyncMock(return_value=source)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)

    aid = new_acquisition_id()
    fetch = FetchResult(
        acquisition_id=aid,
        ok=True,
        requested_url=source.feed_url,
        final_url=source.feed_url,
        status_code=200,
        content_type="application/rss+xml",
        body=b"<rss/>",
        body_size=6,
    )
    items = [
        NormalizedItem(
            acquisition_id=aid,
            source_id=SOURCE_ID,
            guid="g1",
            canonical_url="https://example.com/1",
            title="T",
            category="tech",
        )
    ]
    persist = PersistResult(
        acquisition_id=aid,
        source_id=SOURCE_ID,
        attempted_count=1,
        inserted_count=1,
        skipped_count=0,
        failed_count=0,
    )

    adapter = MagicMock()
    adapter.name = "rss_atom"
    adapter.parse = MagicMock(return_value=items)

    with patch("services.news.collect_service.get_db_session", return_value=session), patch(
        "services.news.collect_service.fetch_safe",
        new_callable=AsyncMock,
        return_value=fetch,
    ), patch(
        "services.news.collect_service.persist_normalized_items",
        new_callable=AsyncMock,
        return_value=persist,
    ), patch(
        "services.news.collect_service.new_acquisition_id",
        return_value=aid,
    ):
        # fix fetch acquisition_id mismatch by making fetch_safe return matching id
        async def _fetch(ctx: AcquisitionContext):
            return FetchResult(
                acquisition_id=ctx.acquisition_id,
                ok=True,
                requested_url=ctx.feed_url,
                final_url=ctx.feed_url,
                status_code=200,
                content_type="application/rss+xml",
                body=b"<rss/>",
                body_size=6,
            )

        with patch(
            "services.news.collect_service.fetch_safe",
            side_effect=_fetch,
        ):
            result = await collect_source(SOURCE_ID, adapter=adapter)

    assert result.status == "succeeded"
    assert result.stage_reached == "complete"
    assert result.inserted_count == 1
    assert result.adapter_name == "rss_atom"
    adapter.parse.assert_called_once()
