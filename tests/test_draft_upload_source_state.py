"""Draft-chat upload must persist a real thread UUID before source-state recording.

Production failure: new chat uses draft:<uuid>, upload sends that as
source_chat_id, parse_thread_uuid returns None, record_chat_upload no-ops,
conversation_file_ids stays empty. Files still become READY.

This suite locks the persist-first caller lifecycle. It does not teach
parse_thread_uuid to accept draft:* and does not change the Multi-Source
resolver.
"""
from __future__ import annotations

import types
import uuid
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import services.thread_service as thread_service
from services.thread_service import create_conversation_thread
from services.workspace_files.multi_source import MODE_MULTI, resolve_turn_sources
from services.workspace_files.source_policy import (
    INITIAL_READ_PENDING,
    INITIAL_READ_SKIPPED,
    UPLOAD_BURST_WINDOW_SECONDS,
)
from services.workspace_files.thread_sources import (
    apply_chat_upload,
    apply_file_ready,
    empty_source_state,
    parse_thread_uuid,
    record_chat_upload,
)
from tests.test_document_upload_intelligence_v1 import (
    CHAT_ID,
    FILE_A,
    FILE_B,
    NOW,
    ORG_A,
    WS_A,
)
from tests.test_multi_source_resolution_v1 import (
    _patch_stream_pipeline as _ms_patch_stream,
    _source_state_chat_patches as _ms_source_patches,
    _used_ab_ctx,
)

DRAFT_CHAT_ID = f"draft:{uuid.UUID(CHAT_ID)}"
PROD_LAST_TWO = "2 הקבצים האחרונים שהעלתי"
PROD_PAIR_TOTAL = "מה הסכום הכולל של 2 ההצעות"


def test_parse_thread_uuid_still_rejects_draft_prefix() -> None:
    assert parse_thread_uuid(DRAFT_CHAT_ID) is None
    assert parse_thread_uuid("draft-local-id") is None
    assert parse_thread_uuid(CHAT_ID) == uuid.UUID(CHAT_ID)


@pytest.mark.asyncio
async def test_record_chat_upload_noops_on_draft_source_chat_id(monkeypatch):
    called = {"n": 0}

    async def boom(*_a, **_k):
        called["n"] += 1
        raise AssertionError("draft source_chat_id must not mutate source_state")

    monkeypatch.setattr(
        "services.workspace_files.thread_sources.mutate_source_state", boom
    )
    out = await record_chat_upload(
        org_id=ORG_A,
        source_chat_id=DRAFT_CHAT_ID,
        file_id=FILE_A,
        media_type="application/pdf",
        filename="A.pdf",
    )
    assert out is None
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_create_conversation_thread_uses_resolve_thread_id(monkeypatch):
    created = uuid.uuid4()
    resolve = AsyncMock(return_value=created)
    monkeypatch.setattr("services.thread_service.resolve_thread_id", resolve)
    monkeypatch.setattr(
        "services.thread_service.get_thread_metadata",
        lambda *_a, **_k: {"session_type": "chat"},
    )

    payload = await create_conversation_thread(ORG_A, title="New conversation")

    resolve.assert_awaited_once_with(ORG_A, None, title="New conversation")
    assert payload["thread"]["id"] == str(created)
    assert payload["thread"]["session_type"] == "chat"
    assert parse_thread_uuid(payload["thread"]["id"]) == created


@pytest.mark.asyncio
async def test_resolve_existing_thread_does_not_create_another(monkeypatch):
    """Attach persist + first message must share one resolve_thread_id identity."""
    persisted = uuid.uuid4()
    calls: list[uuid.UUID | None] = []

    async def fake_resolve(org_id, thread_id, *, title):
        calls.append(thread_id)
        if thread_id is None:
            return persisted
        return thread_id

    monkeypatch.setattr(thread_service, "resolve_thread_id", fake_resolve)
    monkeypatch.setattr(
        thread_service,
        "get_thread_metadata",
        lambda *_a, **_k: {"session_type": "chat"},
    )

    created = await create_conversation_thread(ORG_A, title="New conversation")
    reused = await thread_service.resolve_thread_id(
        ORG_A, uuid.UUID(created["thread"]["id"]), title="hello"
    )

    assert created["thread"]["id"] == str(persisted)
    assert reused == persisted
    assert calls == [None, persisted]


def _in_memory_source_store(monkeypatch):
    store: dict[uuid.UUID, dict] = {}

    async def fake_mutate(org_id, thread_id, mutator):
        current = store.get(thread_id) or empty_source_state()
        nxt = mutator(current)
        store[thread_id] = nxt
        return nxt

    monkeypatch.setattr(
        "services.workspace_files.thread_sources.mutate_source_state", fake_mutate
    )
    return store


@pytest.mark.asyncio
async def test_draft_then_persist_then_upload_records_a(monkeypatch):
    store = _in_memory_source_store(monkeypatch)
    persisted = uuid.uuid4()
    monkeypatch.setattr(
        "services.thread_service.resolve_thread_id",
        AsyncMock(return_value=persisted),
    )
    monkeypatch.setattr(
        "services.thread_service.get_thread_metadata",
        lambda *_a, **_k: {"session_type": "chat"},
    )

    assert parse_thread_uuid(DRAFT_CHAT_ID) is None
    created = await create_conversation_thread(ORG_A, title="New conversation")
    source_chat_id = created["thread"]["id"]
    assert source_chat_id == str(persisted)

    skipped = await record_chat_upload(
        org_id=ORG_A,
        source_chat_id=DRAFT_CHAT_ID,
        file_id=FILE_A,
        media_type="application/pdf",
        filename="A.pdf",
    )
    assert skipped is None

    recorded = await record_chat_upload(
        org_id=ORG_A,
        source_chat_id=source_chat_id,
        file_id=FILE_A,
        media_type="application/pdf",
        filename="A.pdf",
    )
    assert recorded is not None
    assert recorded["conversation_file_ids"] == [str(FILE_A)]
    assert store[persisted]["conversation_file_ids"] == [str(FILE_A)]


@pytest.mark.asyncio
async def test_second_file_same_persisted_chat_is_ab_sequential(monkeypatch):
    store = _in_memory_source_store(monkeypatch)
    tid = uuid.UUID(CHAT_ID)
    t0 = NOW

    with patch("services.workspace_files.thread_sources.utcnow", return_value=t0):
        first = await record_chat_upload(
            org_id=ORG_A,
            source_chat_id=str(tid),
            file_id=FILE_A,
            media_type="application/pdf",
            filename="A.pdf",
        )
    assert first is not None
    ready_a = apply_file_ready(first, str(FILE_A), now=t0 + timedelta(seconds=1))
    store[tid] = ready_a

    later = t0 + timedelta(seconds=UPLOAD_BURST_WINDOW_SECONDS + 5)
    with patch("services.workspace_files.thread_sources.utcnow", return_value=later):
        second = await record_chat_upload(
            org_id=ORG_A,
            source_chat_id=str(tid),
            file_id=FILE_B,
            media_type="application/pdf",
            filename="B.pdf",
        )
    assert second is not None
    ready_b = apply_file_ready(second, str(FILE_B), now=later + timedelta(seconds=1))

    assert ready_b["conversation_file_ids"] == [str(FILE_A), str(FILE_B)]
    assert ready_b["active_file_ids"] == [str(FILE_B)]
    assert ready_b["recent_file_ids"] == [str(FILE_A)]


def test_production_queries_resolve_ab_from_persisted_lifecycle() -> None:
    state = empty_source_state()
    t = NOW
    state = apply_chat_upload(state, str(FILE_A), now=t)
    state = apply_file_ready(state, str(FILE_A), now=t + timedelta(seconds=1))
    t = t + timedelta(seconds=UPLOAD_BURST_WINDOW_SECONDS + 1)
    state = apply_chat_upload(state, str(FILE_B), now=t)
    state = apply_file_ready(state, str(FILE_B), now=t + timedelta(seconds=1))

    last_two = resolve_turn_sources(PROD_LAST_TWO, state)
    assert last_two.mode == MODE_MULTI
    assert set(last_two.file_ids) == {str(FILE_A), str(FILE_B)}

    pair_total = resolve_turn_sources(PROD_PAIR_TOTAL, state)
    assert pair_total.mode == MODE_MULTI
    assert set(pair_total.file_ids) == {str(FILE_A), str(FILE_B)}


@pytest.mark.asyncio
@pytest.mark.parametrize("query", [PROD_LAST_TWO, PROD_PAIR_TOTAL])
async def test_production_queries_stream_provider_once_with_ab(monkeypatch, query):
    import services.chat_service as chat_service
    from services.workspace_files.multi_source import MULTI_SOURCE_GROUNDING_HINT
    from services.workspace_files.thread_sources import apply_chat_turn

    captured: dict = {}
    _ms_patch_stream(monkeypatch, captured)
    state = empty_source_state()
    t = NOW
    state = apply_chat_upload(state, str(FILE_A), now=t)
    state = apply_file_ready(state, str(FILE_A), now=t + timedelta(seconds=1))
    t = t + timedelta(seconds=UPLOAD_BURST_WINDOW_SECONDS + 1)
    state = apply_chat_upload(state, str(FILE_B), now=t)
    state = apply_file_ready(state, str(FILE_B), now=t + timedelta(seconds=1))
    _ms_source_patches(monkeypatch, chat_service, captured, state)

    async def fake_ctx(_org, _ws, *, max_chars, user_query=None, restrict_to_file_ids=None, **_k):
        captured["restrict"] = restrict_to_file_ids
        captured["cover"] = _k.get("cover_file_ids")
        return _used_ab_ctx(_org, _ws, max_chars=max_chars)

    monkeypatch.setattr(chat_service, "load_ready_files_context", fake_ctx)
    recorded = {"turns": 0}

    async def fake_turn(*_a, **k):
        recorded["turns"] += 1
        recorded["used"] = k.get("used_file_ids")
        return apply_chat_turn(state, k.get("used_file_ids") or [])

    monkeypatch.setattr(chat_service, "record_standard_chat_turn", fake_turn)

    async for _line in chat_service.stream_chat_response(
        query,
        "user-1",
        str(ORG_A),
        "free",
        thread_id=uuid.UUID(CHAT_ID),
        provider_id="gpt",
        project_id=WS_A,
    ):
        pass

    assert set(captured["restrict"] or []) == {str(FILE_A), str(FILE_B)}
    assert set(recorded["used"]) == {str(FILE_A), str(FILE_B)}
    assert recorded["turns"] == 1
    assert captured.get("message")
    assert MULTI_SOURCE_GROUNDING_HINT in captured["message"]


@pytest.mark.asyncio
async def test_claim_initial_read_skips_draft_and_runs_for_persisted_uuid(monkeypatch):
    from services.workspace_files.initial_read import claim_initial_read

    wf = types.SimpleNamespace(
        id=FILE_A,
        org_id=ORG_A,
        status="ready",
        media_type="application/pdf",
        original_filename="A.pdf",
        source_chat_id=DRAFT_CHAT_ID,
        initial_read_status="none",
        initial_read_at=None,
    )

    class _ClaimSession:
        def __init__(self, row):
            self.row = row
            self.n = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, *a, **k):
            self.n += 1
            if self.n == 1:
                return MagicMock()
            return types.SimpleNamespace(scalar_one_or_none=lambda: self.row)

        async def commit(self):
            return None

        async def refresh(self, row):
            return None

    monkeypatch.setattr(
        "services.workspace_files.initial_read.get_db_session",
        lambda: _ClaimSession(wf),
    )
    monkeypatch.setattr(
        "services.workspace_files.initial_read.sqlite_has_initial_read",
        lambda *_a, **_k: False,
    )

    skipped = await claim_initial_read(ORG_A, FILE_A)
    assert skipped is None
    assert wf.initial_read_status == INITIAL_READ_SKIPPED

    wf.source_chat_id = CHAT_ID
    wf.initial_read_status = "none"
    claimed = await claim_initial_read(ORG_A, FILE_A)
    assert claimed is wf
    assert wf.initial_read_status == INITIAL_READ_PENDING


def test_create_conversation_thread_route_uses_service(monkeypatch):
    import os

    import main
    from tests.helpers_auth import patch_main_persistent_tenant

    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_clerk")
    monkeypatch.setenv("ENFORCE_AUTH", "false")
    monkeypatch.setenv("AUTH_SHADOW_MODE", "false")
    monkeypatch.setenv("BEN_ANONYMOUS_ORG_ID", str(ORG_A))
    os.environ.setdefault("OPENAI_API_KEY", "sk-test-openai")

    payload = {
        "thread": {
            "id": CHAT_ID,
            "title": "New conversation",
            "session_type": "chat",
            "project_slug": None,
        }
    }
    with patch_main_persistent_tenant(str(ORG_A)), patch(
        "main.create_conversation_thread", new=AsyncMock(return_value=payload)
    ):
        with TestClient(main.app) as client:
            r = client.post("/api/threads", json={"title": "New conversation"})
    assert r.status_code == 200
    assert r.json()["thread"]["id"] == CHAT_ID
    assert r.json()["thread"]["session_type"] == "chat"
