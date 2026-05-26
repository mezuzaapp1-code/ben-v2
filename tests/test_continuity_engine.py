"""Continuity engine v1: read-only BEN Log aggregation."""
from __future__ import annotations

import os
import sys
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:5432/test")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-openai")

from database.models import BenLogEvent, Thread  # noqa: E402
from services import continuity_service as cont  # noqa: E402

ORG_A = uuid.UUID("11111111-1111-1111-1111-111111111111")
ORG_B = uuid.UUID("22222222-2222-2222-2222-222222222222")
THREAD = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
NOW = datetime(2026, 5, 26, 12, 0, 0, tzinfo=timezone.utc)


def _event(
    *,
    event_type: str,
    summary: str,
    source: str = "chat",
    provider: str | None = None,
    model: str | None = None,
    payload: dict | None = None,
    at: datetime | None = None,
) -> BenLogEvent:
    return BenLogEvent(
        id=uuid.uuid4(),
        org_id=ORG_A,
        thread_id=THREAD,
        event_type=event_type,
        summary=summary,
        source=source,
        provider=provider,
        model=model,
        payload=payload,
        created_at=at or NOW,
    )


class _ReadOnlySession:
    def __init__(self, events: list[BenLogEvent]) -> None:
        self.events = events
        self.added: list[object] = []

    async def execute(self, *_args, **_kwargs) -> None:
        return None

    def add(self, obj) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        raise AssertionError("continuity must not write")

    async def scalars(self, _stmt):
        return _ScalarResult(self.events)


class _ScalarResult:
    def __init__(self, items):
        self._items = items

    def all(self):
        return self._items


@pytest.fixture
def log_session(monkeypatch):
    session = _ReadOnlySession([])

    @asynccontextmanager
    async def fake_get_db():
        yield session

    monkeypatch.setattr(cont, "get_db_session", fake_get_db)
    return session


@pytest.mark.asyncio
async def test_empty_continuity_safe_state(monkeypatch, log_session):
    monkeypatch.setattr(
        cont,
        "get_thread_for_org",
        AsyncMock(return_value=Thread(id=THREAD, org_id=ORG_A, title="t")),
    )
    log_session.events = []
    out = await cont.build_thread_continuity(ORG_A, THREAD)
    assert out["event_count"] == 0
    assert out["continuity_confidence"] == "none"
    assert out["decisions"] == []
    assert out["current_direction"] == ""
    assert out["provider_activity"]["openai"] == 0


@pytest.mark.asyncio
async def test_prompt_response_low_confidence(monkeypatch, log_session):
    monkeypatch.setattr(cont, "get_thread_for_org", AsyncMock(return_value=Thread(id=THREAD, org_id=ORG_A, title="t")))
    log_session.events = [
        _event(event_type="prompt", summary="start task", at=NOW),
        _event(
            event_type="response",
            summary="ack",
            provider="gpt",
            model="gpt-4o-mini",
            payload={"provider_id": "gpt"},
            at=NOW + timedelta(seconds=1),
        ),
    ]
    out = await cont.build_thread_continuity(ORG_A, THREAD)
    assert out["continuity_confidence"] == "low"
    assert out["provider_activity"]["openai"] == 1


@pytest.mark.asyncio
async def test_unresolved_in_list(monkeypatch, log_session):
    monkeypatch.setattr(cont, "get_thread_for_org", AsyncMock(return_value=Thread(id=THREAD, org_id=ORG_A, title="t")))
    log_session.events = [_event(event_type="unresolved", summary="migration pending")]
    out = await cont.build_thread_continuity(ORG_A, THREAD)
    assert "migration pending" in out["unresolved_items"]
    assert out["continuity_confidence"] == "medium"


@pytest.mark.asyncio
async def test_rejection_in_list(monkeypatch, log_session):
    monkeypatch.setattr(cont, "get_thread_for_org", AsyncMock(return_value=Thread(id=THREAD, org_id=ORG_A, title="t")))
    log_session.events = [
        _event(event_type="rejection", summary="skip redis cache"),
        _event(event_type="next_step", summary="use postgres idempotency"),
    ]
    out = await cont.build_thread_continuity(ORG_A, THREAD)
    assert "skip redis cache" in out["rejected_paths"]
    assert out["continuity_confidence"] == "high"


@pytest.mark.asyncio
async def test_next_step_in_list(monkeypatch, log_session):
    monkeypatch.setattr(cont, "get_thread_for_org", AsyncMock(return_value=Thread(id=THREAD, org_id=ORG_A, title="t")))
    log_session.events = [_event(event_type="next_step", summary="run migration verify")]
    out = await cont.build_thread_continuity(ORG_A, THREAD)
    assert out["next_steps"] == ["run migration verify"]
    assert out["continuity_confidence"] == "medium"


@pytest.mark.asyncio
async def test_context_note_low_confidence(monkeypatch, log_session):
    monkeypatch.setattr(cont, "get_thread_for_org", AsyncMock(return_value=Thread(id=THREAD, org_id=ORG_A, title="t")))
    log_session.events = [
        _event(event_type="context", summary="phase P3 active"),
        _event(event_type="note", summary="operator note"),
    ]
    out = await cont.build_thread_continuity(ORG_A, THREAD)
    assert out["continuity_confidence"] == "low"


@pytest.mark.asyncio
async def test_current_direction_priority(monkeypatch, log_session):
    monkeypatch.setattr(cont, "get_thread_for_org", AsyncMock(return_value=Thread(id=THREAD, org_id=ORG_A, title="t")))
    log_session.events = [
        _event(event_type="response", summary="old response", at=NOW),
        _event(event_type="decision", summary="decided X", at=NOW + timedelta(seconds=1)),
        _event(event_type="next_step", summary="do Y next", at=NOW + timedelta(seconds=2)),
        _event(event_type="response", summary="newer response", at=NOW + timedelta(seconds=3)),
    ]
    out = await cont.build_thread_continuity(ORG_A, THREAD)
    assert out["current_direction"] == "do Y next"


@pytest.mark.asyncio
async def test_payload_fallback_summary(monkeypatch, log_session):
    monkeypatch.setattr(cont, "get_thread_for_org", AsyncMock(return_value=Thread(id=THREAD, org_id=ORG_A, title="t")))
    row = _event(event_type="next_step", summary="", payload={"next_step": "from payload"})
    log_session.events = [row]
    out = await cont.build_thread_continuity(ORG_A, THREAD)
    assert "from payload" in out["next_steps"]


@pytest.mark.asyncio
async def test_decision_in_list(monkeypatch, log_session):
    monkeypatch.setattr(cont, "get_thread_for_org", AsyncMock(return_value=Thread(id=THREAD, org_id=ORG_A, title="t")))
    log_session.events = [
        _event(event_type="decision", summary="ship capture v1"),
        _event(event_type="next_step", summary="deploy to staging"),
    ]
    out = await cont.build_thread_continuity(ORG_A, THREAD)
    assert "ship capture v1" in out["decisions"]
    assert out["continuity_confidence"] == "high"


@pytest.mark.asyncio
async def test_provider_activity_counts(monkeypatch, log_session):
    monkeypatch.setattr(cont, "get_thread_for_org", AsyncMock(return_value=Thread(id=THREAD, org_id=ORG_A, title="t")))
    log_session.events = [
        _event(event_type="response", summary="a", provider="gpt", model="gpt-4o"),
        _event(event_type="response", summary="b", provider="claude", model="claude-sonnet"),
        _event(event_type="response", summary="c", provider="gemini", model="gemini-flash"),
        _event(event_type="response", summary="d", source="council", provider="synthesis", model="synthesis"),
    ]
    out = await cont.build_thread_continuity(ORG_A, THREAD)
    assert out["provider_activity"]["openai"] == 1
    assert out["provider_activity"]["anthropic"] == 1
    assert out["provider_activity"]["google"] == 1
    assert out["provider_activity"]["synthesis"] == 1


@pytest.mark.asyncio
async def test_max_item_limits(monkeypatch, log_session):
    monkeypatch.setattr(cont, "get_thread_for_org", AsyncMock(return_value=Thread(id=THREAD, org_id=ORG_A, title="t")))
    log_session.events = [
        _event(event_type="decision", summary=f"d{i}", at=NOW + timedelta(seconds=i)) for i in range(15)
    ]
    out = await cont.build_thread_continuity(ORG_A, THREAD)
    assert len(out["decisions"]) == cont.MAX_ITEMS_PER_SECTION


@pytest.mark.asyncio
async def test_read_failure_503(monkeypatch, log_session):
    monkeypatch.setattr(cont, "get_thread_for_org", AsyncMock(return_value=Thread(id=THREAD, org_id=ORG_A, title="t")))

    @asynccontextmanager
    async def failing_db():
        raise RuntimeError("db down")
        yield  # pragma: no cover

    monkeypatch.setattr(cont, "get_db_session", failing_db)
    with pytest.raises(HTTPException) as exc:
        await cont.build_thread_continuity(ORG_A, THREAD)
    assert exc.value.status_code == 503
    assert exc.value.detail["code"] == "continuity_read_failed"


@pytest.mark.asyncio
async def test_wrong_org_404(monkeypatch):
    monkeypatch.setattr(cont, "get_thread_for_org", AsyncMock(return_value=None))
    with pytest.raises(HTTPException) as exc:
        await cont.build_thread_continuity(ORG_B, THREAD)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_no_writes_on_read(monkeypatch, log_session):
    monkeypatch.setattr(cont, "get_thread_for_org", AsyncMock(return_value=Thread(id=THREAD, org_id=ORG_A, title="t")))
    log_session.events = [_event(event_type="prompt", summary="x")]
    await cont.build_thread_continuity(ORG_A, THREAD)
    assert log_session.added == []


def test_no_forbidden_imports():
    src = Path(cont.__file__).read_text(encoding="utf-8")
    lowered = src.lower()
    assert "ledger" not in lowered
    assert "model_gateway" not in src
    assert "append_event" not in src
    assert "ben_log_service" not in src
    assert "providers." not in src


@pytest.mark.asyncio
async def test_api_route_delegates(monkeypatch):
    import main

    monkeypatch.setattr(
        main,
        "build_thread_continuity",
        AsyncMock(
            return_value={
                "thread_id": str(THREAD),
                "event_count": 0,
                "continuity_confidence": "none",
                "decisions": [],
                "unresolved_items": [],
                "rejected_paths": [],
                "next_steps": [],
                "current_direction": "",
                "last_activity_at": None,
                "provider_activity": {"openai": 0, "anthropic": 0, "google": 0, "synthesis": 0},
            }
        ),
    )
    from auth.tenant_binding import TenantContext

    ctx = TenantContext(
        tenant_id=str(ORG_A),
        tenant_type="organization",
        org_id=str(ORG_A),
        user_id="u",
        email=None,
        auth_source="clerk_jwt",
        auth_present=True,
        org_bound=True,
    )
    monkeypatch.setattr(main, "_tenant_ctx_from_request", AsyncMock(return_value=ctx))
    from fastapi.testclient import TestClient

    with TestClient(main.app) as client:
        r = client.get(f"/api/threads/{THREAD}/continuity")
    assert r.status_code == 200
    assert r.json()["continuity_confidence"] == "none"
