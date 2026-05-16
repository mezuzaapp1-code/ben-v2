"""Council lifecycle: non-blocking persistence and error humanization."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:5432/test")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-openai")

from services import council_service as cs


def _humanize(status: int, data: dict) -> str:
    """Mirror frontend humanizeCouncilHttpError for CI."""
    detail = data.get("detail")
    if status == 401:
        return detail if isinstance(detail, str) else "Sign in required to use Council."
    if status == 400:
        return (
            detail
            if isinstance(detail, str)
            else "Organization context missing. Select an organization in Clerk and try again."
        )
    if status == 422:
        return detail if isinstance(detail, str) else "Invalid request. Check your session and try again."
    if status >= 500:
        return "Council is temporarily unavailable. Please try again in a moment."
    return f"Council request failed ({status}). You can retry."


def test_humanize_http_errors():
    assert "Sign in" in _humanize(401, {})
    assert "Organization" in _humanize(400, {})
    assert "Invalid" in _humanize(422, {})
    assert "unavailable" in _humanize(503, {})


@pytest.mark.asyncio
async def test_schedule_background_does_not_block_caller():
    gate = asyncio.Event()

    async def slow():
        await gate.wait()

    t0 = asyncio.get_running_loop().time()
    cs._schedule_background_task(slow())
    elapsed = asyncio.get_running_loop().time() - t0
    assert elapsed < 0.05
    gate.set()
    await asyncio.sleep(0.05)

