"""N3.0 persist_articles counting and safe error messages."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.acquisition.types import AcquisitionContext, NormalizedItem, new_acquisition_id
from services.news.persist_articles import persist_normalized_items


def _ctx() -> AcquisitionContext:
    return AcquisitionContext(
        acquisition_id=new_acquisition_id(),
        source_id=uuid.uuid4(),
        source_name="S",
        feed_url="https://example.com/feed",
        category="tech",
        language="en",
        enabled=True,
        started_at=datetime.now(timezone.utc),
    )


def _items(ctx: AcquisitionContext, n: int = 2) -> list[NormalizedItem]:
    return [
        NormalizedItem(
            acquisition_id=ctx.acquisition_id,
            source_id=ctx.source_id,
            guid=f"g{i}",
            canonical_url=f"https://example.com/{i}",
            title=f"T{i}",
            category="tech",
        )
        for i in range(n)
    ]


@pytest.mark.asyncio
async def test_persist_counts_from_returning():
    ctx = _ctx()
    items = _items(ctx, 3)

    result_proxy = MagicMock()
    # one conflict skipped → RETURNING yields 2 ids
    result_proxy.scalars.return_value.all.return_value = [uuid.uuid4(), uuid.uuid4()]

    session = AsyncMock()
    session.execute = AsyncMock(return_value=result_proxy)
    session.commit = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)

    with patch("services.news.persist_articles.get_db_session", return_value=session):
        out = await persist_normalized_items(ctx, items)

    assert out.error is None
    assert out.attempted_count == 3
    assert out.inserted_count == 2
    assert out.skipped_count == 1
    assert out.failed_count == 0
    # ensure RETURNING used
    stmt = session.execute.await_args.args[0]
    assert "RETURNING" in str(stmt).upper() or hasattr(stmt, "returning")


@pytest.mark.asyncio
async def test_persist_error_message_hides_exception_text():
    ctx = _ctx()
    items = _items(ctx, 1)

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=RuntimeError("password=supersecret host=10.0.0.1"))
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)

    with patch("services.news.persist_articles.get_db_session", return_value=session):
        out = await persist_normalized_items(ctx, items)

    assert out.error is not None
    assert out.error.error_class == "persist_error"
    assert out.error.message == "persist failed"
    assert "supersecret" not in out.error.message
    assert "10.0.0.1" not in out.error.message
    assert out.failed_count == 1
    assert out.inserted_count == 0
