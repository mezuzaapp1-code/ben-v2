"""BEN Log capture v1: non-blocking append after chat/council persist."""
from __future__ import annotations

import os
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:5432/test")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-openai")

from database.models import BenLogEvent  # noqa: E402
from services import ben_log_service as bls  # noqa: E402
from services import chat_service  # noqa: E402
from services import council_service as cs  # noqa: E402

ORG = uuid.UUID("11111111-1111-1111-1111-111111111111")
THREAD = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


class _MemorySession:
    def __init__(self) -> None:
        self.events: list[BenLogEvent] = []

    async def execute(self, *_args, **_kwargs) -> None:
        return None

    def add(self, obj) -> None:
        if isinstance(obj, BenLogEvent):
            if obj.id is None:
                obj.id = uuid.uuid4()
            self.events.append(obj)

    async def commit(self) -> None:
        return None

    async def refresh(self, obj) -> None:
        return None


@pytest.fixture
def memory_ben_log(monkeypatch):
    session = _MemorySession()

    @asynccontextmanager
    async def fake_get_db():
        yield session

    monkeypatch.setattr(bls, "get_db_session", fake_get_db)
    return session


@pytest.mark.asyncio
async def test_append_event_writes_valid_chat_prompt(memory_ben_log):
    event_id = await bls.append_event(
        org_id=ORG,
        thread_id=THREAD,
        event_type="prompt",
        summary="hello world",
        source="chat",
    )
    assert event_id is not None
    row = memory_ben_log.events[0]
    assert row.event_type == "prompt"
    assert row.source == "chat"
    assert row.org_id == ORG
    assert row.thread_id == THREAD


@pytest.mark.asyncio
async def test_capture_chat_exchange_writes_prompt_and_response(memory_ben_log):
    await bls.capture_chat_exchange(
        org_id=ORG,
        thread_id=THREAD,
        user_message="What is 2+2?",
        assistant_response="Four",
        provider_id="gpt",
        model_used="gpt-4o-mini",
        user_message_id=uuid.uuid4(),
        assistant_message_id=uuid.uuid4(),
    )
    assert len(memory_ben_log.events) == 2
    assert memory_ben_log.events[0].event_type == "prompt"
    assert memory_ben_log.events[1].event_type == "response"
    assert memory_ben_log.events[1].provider == "gpt"
    assert memory_ben_log.events[1].model == "gpt-4o-mini"
    assert memory_ben_log.events[1].payload.get("provider_id") == "gpt"


@pytest.mark.asyncio
async def test_capture_council_synthesis_response(memory_ben_log):
    payload = {
        "available_experts": 2,
        "unavailable_experts": 1,
        "synthesis": {
            "recommendation": "Proceed with caution",
            "synthesis_mode": "partial_consensus",
            "consensus_available": True,
        },
        "room": {"id": "room-1", "question_id": "q-1", "status": "complete"},
    }
    await bls.capture_council_synthesis(
        org_id=ORG,
        thread_id=THREAD,
        question="Should we deploy?",
        payload=payload,
    )
    assert len(memory_ben_log.events) == 1
    row = memory_ben_log.events[0]
    assert row.source == "council"
    assert row.event_type == "response"
    assert row.provider == "synthesis"
    assert row.model == "synthesis"
    assert row.payload["degraded"] is True
    assert row.payload["available_experts"] == 2


@pytest.mark.asyncio
async def test_append_event_failure_does_not_raise(monkeypatch):
    @asynccontextmanager
    async def broken():
        raise RuntimeError("ben log db down")
        yield  # pragma: no cover

    monkeypatch.setattr(bls, "get_db_session", broken)
    result = await bls.append_event(
        org_id=ORG,
        thread_id=THREAD,
        event_type="prompt",
        summary="x",
        source="chat",
    )
    assert result is None


@pytest.mark.asyncio
async def test_chat_succeeds_when_ben_log_fails(monkeypatch):
    tid = uuid.uuid4()

    async def fake_resolve(*_a, **_k):
        return tid

    async def fake_route(*_a, **_k):
        return {
            "content": "ok",
            "model_used": "gpt-4o-mini",
            "cost_usd": 0.0,
            "provider_used": "openai",
        }

    class _ChatSession:
        async def execute(self, *_a, **_k):
            return None

        def add_all(self, _rows):
            return None

        async def flush(self):
            return None

        async def commit(self):
            return None

    @asynccontextmanager
    async def fake_chat_db():
        yield _ChatSession()

    monkeypatch.setattr(chat_service, "resolve_thread_id", fake_resolve)
    monkeypatch.setattr(chat_service, "route_request", fake_route)
    monkeypatch.setattr(chat_service, "get_db_session", fake_chat_db)
    monkeypatch.setattr(
        bls,
        "append_event",
        AsyncMock(side_effect=RuntimeError("ben log db down")),
    )

    out = await chat_service.handle_chat("hi", "user", str(ORG), "free", provider_id="gpt")
    assert out["response"] == "ok"


@pytest.mark.asyncio
async def test_run_council_copy_paste_invokes_opinion_pipeline(monkeypatch):
    captured: dict = {}

    async def fake_copy_paste(
        *,
        org_id,
        thread_id,
        tenant_id,
        tier,
        opinion_request,
        provider_id=None,
    ):
        captured["opinion_request"] = opinion_request
        captured["tenant_id"] = tenant_id
        return {"response": "Council answer", "cost_usd": 0.0, "thread_id": str(THREAD)}

    monkeypatch.setattr(cs, "run_copy_paste_opinion", fake_copy_paste)
    payload = await cs.run_council("Should we deploy?", str(ORG), force_codebase=False)
    assert captured["opinion_request"] == "Should we deploy?"
    assert payload["mode"] == "copy_paste"
    assert payload["response"] == "Council answer"
    assert payload["council"] == []
    assert payload["synthesis"] is None


@pytest.mark.asyncio
async def test_run_council_survives_ben_log_capture_failure(monkeypatch):
    async def fake_copy_paste(
        *,
        org_id,
        thread_id,
        tenant_id,
        tier,
        opinion_request,
        provider_id=None,
    ):
        return {"response": "ok", "cost_usd": 0.0, "thread_id": str(THREAD)}

    monkeypatch.setattr(cs, "run_copy_paste_opinion", fake_copy_paste)
    monkeypatch.setattr(
        bls,
        "append_event",
        AsyncMock(side_effect=RuntimeError("ben log db down")),
    )
    payload = await cs.run_council("q?", str(ORG), force_codebase=False)
    assert payload["response"] == "ok"


@pytest.mark.asyncio
async def test_handle_chat_writes_prompt_and_response_events(monkeypatch, memory_ben_log):
    tid = uuid.uuid4()

    async def fake_resolve(*_a, **_k):
        return tid

    async def fake_route(*_a, **_k):
        return {
            "content": "Four",
            "model_used": "gpt-4o-mini",
            "cost_usd": 0.0,
            "provider_used": "openai",
        }

    class _ChatSession:
        async def execute(self, *_a, **_k):
            return None

        def add_all(self, _rows):
            for row in _rows:
                if row.id is None:
                    row.id = uuid.uuid4()

        async def flush(self):
            return None

        async def commit(self):
            return None

    @asynccontextmanager
    async def fake_chat_db():
        yield _ChatSession()

    monkeypatch.setattr(chat_service, "resolve_thread_id", fake_resolve)
    monkeypatch.setattr(chat_service, "route_request", fake_route)
    monkeypatch.setattr(chat_service, "get_db_session", fake_chat_db)

    out = await chat_service.handle_chat("What is 2+2?", "user", str(ORG), "free", provider_id="gpt")
    assert out["response"] == "Four"
    assert len(memory_ben_log.events) == 2
    assert memory_ben_log.events[0].source == "chat"
    assert memory_ben_log.events[1].event_type == "response"


def test_no_ledger_imports_in_capture_modules():
    src_ben = Path(bls.__file__).read_text(encoding="utf-8")
    src_chat = Path(chat_service.__file__).read_text(encoding="utf-8")
    src_council = Path(cs.__file__).read_text(encoding="utf-8")
    assert "ledger" not in src_ben.lower()
    assert "ledger_auth" not in src_ben
    assert "ledger" not in src_chat.lower()
    assert "ledger_auth" not in src_council


def test_main_has_no_ben_log_routes():
    import main

    routes = [getattr(r, "path", None) for r in main.app.routes]
    assert not any(p and "/api/log" in p for p in routes)
