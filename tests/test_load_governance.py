"""Load governance: bounded concurrency, duplicate guard, overload responses."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:5432/test")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-openai")

import main  # noqa: E402
from services.ops.load_governance import get_load_governor, reset_load_governor_for_tests  # noqa: E402
from services.ops.load_messages import (  # noqa: E402
    COUNCIL_BUSY,
    DUPLICATE_REQUEST,
    RUNTIME_SATURATED,
    overload_detail,
)


@pytest.fixture(autouse=True)
def _isolated_governor():
    reset_load_governor_for_tests(max_chat=1, max_council=1, max_total=2, dedup_window_s=60.0)
    yield
    reset_load_governor_for_tests()


@pytest.fixture(autouse=True)
def _auth_env(monkeypatch):
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_clerk")
    monkeypatch.setenv("ENFORCE_AUTH", "false")
    monkeypatch.setenv("AUTH_SHADOW_MODE", "false")
    monkeypatch.setenv("BEN_ANONYMOUS_ORG_ID", "00000000-0000-0000-0000-000000000001")


@pytest.fixture
def client():
    return TestClient(main.app)


def test_overload_detail_hebrew():
    d = overload_detail(COUNCIL_BUSY, "he")
    assert d["code"] == COUNCIL_BUSY
    assert "המועצה" in d["message"]


@pytest.mark.asyncio
async def test_council_duplicate_in_flight_rejected():
    gov = get_load_governor()
    started = asyncio.Event()
    done = asyncio.Event()

    async def holder() -> None:
        async with gov.govern_council(tenant_id="tenant-a", question="Same question?", locale="en"):
            started.set()
            await done.wait()

    task = asyncio.create_task(holder())
    await started.wait()
    with pytest.raises(HTTPException) as exc:
        async with gov.govern_council(tenant_id="tenant-a", question="Same question?", locale="en"):
            pass
    assert exc.value.status_code == 429
    assert exc.value.detail["code"] == DUPLICATE_REQUEST
    done.set()
    await task


@pytest.mark.asyncio
async def test_council_concurrency_saturated():
    gov = get_load_governor()
    gate = asyncio.Event()

    async def holder() -> None:
        async with gov.govern_council(tenant_id="t1", question="question one", locale="en"):
            gate.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(holder())
    await gate.wait()
    with pytest.raises(HTTPException) as exc:
        async with gov.govern_council(tenant_id="t2", question="question two", locale="en"):
            pass
    assert exc.value.status_code == 503
    assert exc.value.detail["code"] == COUNCIL_BUSY
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_chat_runtime_saturated():
    gov = get_load_governor()

    async def holder() -> None:
        async with gov.govern_chat(locale="en"):
            await asyncio.Event().wait()

    task = asyncio.create_task(holder())
    await asyncio.sleep(0.05)
    with pytest.raises(HTTPException) as exc:
        async with gov.govern_chat(locale="en"):
            pass
    assert exc.value.status_code == 503
    assert exc.value.detail["code"] == RUNTIME_SATURATED
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_api_council_returns_structured_overload(client):
    import threading

    hold = threading.Event()

    async def slow_council(*_a, **_k):
        for _ in range(150):
            if hold.is_set():
                break
            await asyncio.sleep(0.02)
        return {"council": [], "cost_usd": 0.0}

    with patch.object(main, "run_council", new_callable=AsyncMock, side_effect=slow_council):
        import threading
        import time

        def first() -> None:
            client.post("/council", json={"question": "Overload test?"})

        t = threading.Thread(target=first)
        t.start()
        time.sleep(0.15)
        r2 = client.post("/council", json={"question": "Different question?"})
        hold.set()
        t.join(timeout=5)
        assert r2.status_code == 503
        detail = r2.json().get("detail") or {}
        assert detail.get("code") == COUNCIL_BUSY
        assert detail.get("recoverable") is True


def test_api_duplicate_council_same_question(client):
    import threading

    hold = threading.Event()

    async def slow_council(*_a, **_k):
        for _ in range(150):
            if hold.is_set():
                break
            await asyncio.sleep(0.02)
        return {"council": [], "cost_usd": 0.0}

    with patch.object(main, "run_council", new_callable=AsyncMock, side_effect=slow_council):
        import threading
        import time

        def first() -> None:
            client.post("/council", json={"question": "Duplicate guard?"})

        t = threading.Thread(target=first)
        t.start()
        time.sleep(0.1)
        r2 = client.post("/council", json={"question": "Duplicate guard?"})
        hold.set()
        t.join(timeout=5)
        assert r2.status_code == 429
        assert r2.json()["detail"]["code"] == DUPLICATE_REQUEST


@pytest.mark.asyncio
async def test_rejected_counter_increments():
    gov = get_load_governor()
    before = gov.rejected_overload_requests

    async def holder() -> None:
        async with gov.govern_chat(locale="en"):
            await asyncio.Event().wait()

    task = asyncio.create_task(holder())
    await asyncio.sleep(0.05)
    with pytest.raises(HTTPException):
        async with gov.govern_chat(locale="en"):
            pass
    assert gov.rejected_overload_requests > before
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
