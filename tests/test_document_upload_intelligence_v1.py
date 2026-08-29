"""Document Upload Intelligence V1 — state machine, pack, allow-list, Initial Read."""

from __future__ import annotations

import asyncio
import inspect
import pathlib
import types
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.message_format import decode_message, encode_chat_assistant
from services.workspace_files.chunk_retriever import ChunkHit
from services.workspace_files.initial_read_pack import (
    MAX_PACK_CHUNKS,
    PackChunk,
    select_representative_chunks,
)
from services.workspace_files.service import (
    WorkspaceFilesContext,
    load_ready_files_context,
)
from services.workspace_files.source_policy import (
    ACTIVE_SOURCE_IDLE_TTL_MINUTES,
    ACTIVE_SOURCE_MAX_UNUSED_TURNS,
    FILE_INITIAL_READ_EVENT,
    INITIAL_READ_FAILED,
    INITIAL_READ_MAX_ATTEMPTS,
    UPLOAD_BURST_WINDOW_SECONDS,
)
from services.workspace_files.thread_sources import (
    apply_chat_turn,
    apply_chat_upload,
    apply_file_failed,
    apply_file_ready,
    empty_source_state,
    expire_active,
    is_vision_upload,
    parse_thread_uuid,
    restriction_file_ids,
)


CHAT_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
ORG_A = uuid.UUID("11111111-1111-1111-1111-111111111111")
WS_A = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
FILE_A = uuid.UUID("a0000000-0000-0000-0000-000000000001")
FILE_B = uuid.UUID("b0000000-0000-0000-0000-000000000002")
FILE_F0 = uuid.UUID("f0000000-0000-0000-0000-00000000000f")
FILE_W1 = uuid.UUID("11000000-0000-0000-0000-000000000011")
NOW = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)
ROOT = pathlib.Path(__file__).resolve().parents[1]


def _ids(state: dict) -> set[str]:
    return set(restriction_file_ids(state))


# --------------------------------------------------------------------------- #
# Policy + encoding                                                           #
# --------------------------------------------------------------------------- #


def test_policy_constants_are_central() -> None:
    assert ACTIVE_SOURCE_MAX_UNUSED_TURNS == 2
    assert ACTIVE_SOURCE_IDLE_TTL_MINUTES == 20
    assert UPLOAD_BURST_WINDOW_SECONDS == 120
    assert FILE_INITIAL_READ_EVENT == "file_initial_read"
    assert INITIAL_READ_MAX_ATTEMPTS == 5
    src = (ROOT / "services" / "workspace_files" / "thread_sources.py").read_text(encoding="utf-8")
    assert "ACTIVE_SOURCE_MAX_UNUSED_TURNS" in src
    assert "ACTIVE_SOURCE_IDLE_TTL_MINUTES" in src
    assert "UPLOAD_BURST_WINDOW_SECONDS" in src
    assert "timedelta(minutes=20)" not in src
    assert "timedelta(seconds=120)" not in src


def test_sqlite_is_not_source_state_owner() -> None:
    src = (ROOT / "services" / "workspace_files" / "thread_sources.py").read_text(encoding="utf-8")
    assert "from database.thread_store" not in src
    assert "import database.thread_store" not in src
    chat = (ROOT / "services" / "chat_service.py").read_text(encoding="utf-8")
    assert "restriction_file_ids" in chat
    assert "load_source_state" in chat


def test_encode_decode_preserves_source_event_without_file_overview_kind() -> None:
    raw = encode_chat_assistant(
        "Overview of A.",
        used_files=[{"id": str(FILE_A), "name": "A.pdf"}],
        source_event=FILE_INITIAL_READ_EVENT,
        source_file_id=str(FILE_A),
    )
    assert '"kind": "chat"' in raw
    assert "file_overview" not in raw
    decoded = decode_message("assistant", raw)
    assert decoded.get("kind") != "file_overview"
    assert decoded["source_event"] == FILE_INITIAL_READ_EVENT
    assert decoded["source_file_id"] == str(FILE_A)
    assert decoded["used_files"][0]["name"] == "A.pdf"
    assert decoded["used_files"][0]["id"] == str(FILE_A)


def test_no_file_overview_kind_in_python_surface() -> None:
    hits: list[str] = []
    for path in [
        ROOT / "services" / "message_format.py",
        ROOT / "services" / "workspace_files" / "initial_read.py",
        ROOT / "services" / "chat_service.py",
        ROOT / "services" / "thread_service.py",
    ]:
        text = path.read_text(encoding="utf-8")
        if "file_overview" in text:
            hits.append(str(path))
    assert hits == []


# --------------------------------------------------------------------------- #
# State machine (cases 4–8, burst, TTL)                                       #
# --------------------------------------------------------------------------- #


def test_upload_burst_pending_then_ready_active() -> None:
    state = empty_source_state()
    state = apply_chat_upload(state, str(FILE_A), now=NOW)
    assert state["pending_file_ids"] == [str(FILE_A)]
    assert not state["active_file_ids"]
    assert _ids(state) == {str(FILE_A)}

    state = apply_file_ready(state, str(FILE_A), now=NOW + timedelta(seconds=5))
    assert state["pending_file_ids"] == []
    assert state["active_file_ids"] == [str(FILE_A)]
    assert state["unused_turns"] == 0
    assert _ids(state) == {str(FILE_A)}


def test_processing_ask_does_not_clear_pending() -> None:
    """Case 8: ask while PROCESSING keeps pending restriction."""
    state = empty_source_state()
    state = apply_chat_upload(state, str(FILE_A), now=NOW)
    state = apply_chat_turn(state, [], now=NOW + timedelta(seconds=1))
    assert state["pending_file_ids"] == [str(FILE_A)]
    assert _ids(state) == {str(FILE_A)}


def test_one_unused_turn_keeps_active() -> None:
    """Case 4."""
    state = empty_source_state()
    state = apply_chat_upload(state, str(FILE_A), now=NOW)
    state = apply_file_ready(state, str(FILE_A), now=NOW)
    state = apply_chat_turn(state, [], now=NOW + timedelta(seconds=10))
    assert state["active_file_ids"] == [str(FILE_A)]
    assert state["unused_turns"] == 1
    assert _ids(state) == {str(FILE_A)}


def test_two_unused_turns_demote_to_recent() -> None:
    """Case 5."""
    state = empty_source_state()
    state = apply_chat_upload(state, str(FILE_A), now=NOW)
    state = apply_file_ready(state, str(FILE_A), now=NOW)
    state = apply_chat_turn(state, [], now=NOW)
    state = apply_chat_turn(state, [], now=NOW + timedelta(seconds=5))
    assert state["active_file_ids"] == []
    assert str(FILE_A) in state["recent_file_ids"]
    assert _ids(state) == set()


def test_using_active_file_resets_unused_turns() -> None:
    state = empty_source_state()
    state = apply_chat_upload(state, str(FILE_A), now=NOW)
    state = apply_file_ready(state, str(FILE_A), now=NOW)
    state = apply_chat_turn(state, [], now=NOW)
    state = apply_chat_turn(state, [str(FILE_A)], now=NOW + timedelta(seconds=5))
    assert state["active_file_ids"] == [str(FILE_A)]
    assert state["unused_turns"] == 0


def test_burst_survives_ready_before_second_upload() -> None:
    """Blocker 2: A READY then B within burst → same cohort, not recent."""
    state = empty_source_state()
    state = apply_chat_upload(state, str(FILE_A), now=NOW)
    state = apply_file_ready(state, str(FILE_A), now=NOW + timedelta(seconds=1))
    assert state["pending_file_ids"] == []
    assert state["active_file_ids"] == [str(FILE_A)]
    state = apply_chat_upload(state, str(FILE_B), now=NOW + timedelta(seconds=10))
    assert str(FILE_A) in state["active_file_ids"]
    assert str(FILE_A) not in state["recent_file_ids"]
    assert str(FILE_B) in state["pending_file_ids"]
    state = apply_file_ready(state, str(FILE_B), now=NOW + timedelta(seconds=11))
    assert set(state["active_file_ids"]) == {str(FILE_A), str(FILE_B)}
    assert state["burst_opened_at"] is not None
    restriction = _ids(state)
    assert restriction == {str(FILE_A), str(FILE_B)}


def test_burst_survives_reverse_ready_order() -> None:
    """Blocker 2: B READY before A; still one cohort on compare-these."""
    state = empty_source_state()
    t0 = NOW
    state = apply_chat_upload(state, str(FILE_A), now=t0)
    state = apply_chat_upload(state, str(FILE_B), now=t0 + timedelta(seconds=2))
    state = apply_file_ready(state, str(FILE_B), now=t0 + timedelta(seconds=3))
    assert str(FILE_B) in state["active_file_ids"]
    assert str(FILE_A) in state["pending_file_ids"]
    state = apply_file_ready(state, str(FILE_A), now=t0 + timedelta(seconds=4))
    assert set(state["active_file_ids"]) == {str(FILE_A), str(FILE_B)}
    assert _ids(state) == {str(FILE_A), str(FILE_B)}


def test_explicit_name_does_not_clear_active() -> None:
    """Case 7: named B wins that turn; A stays active."""
    state = empty_source_state()
    state = apply_chat_upload(state, str(FILE_A), now=NOW)
    state = apply_file_ready(state, str(FILE_A), now=NOW)
    state = apply_chat_turn(state, [str(FILE_B)], now=NOW + timedelta(seconds=5))
    assert state["active_file_ids"] == [str(FILE_A)]
    assert _ids(state) == {str(FILE_A)}


def test_burst_a_plus_b_active_together() -> None:
    """Case 6."""
    state = empty_source_state()
    state = apply_chat_upload(state, str(FILE_A), now=NOW)
    state = apply_chat_upload(state, str(FILE_B), now=NOW + timedelta(seconds=2))
    assert set(state["pending_file_ids"]) == {str(FILE_A), str(FILE_B)}
    state = apply_file_ready(state, str(FILE_A), now=NOW + timedelta(seconds=10))
    assert str(FILE_A) in state["active_file_ids"]
    assert str(FILE_B) in state["pending_file_ids"]
    assert _ids(state) == {str(FILE_A), str(FILE_B)}
    state = apply_file_ready(state, str(FILE_B), now=NOW + timedelta(seconds=11))
    assert set(state["active_file_ids"]) == {str(FILE_A), str(FILE_B)}
    assert state["pending_file_ids"] == []
    assert _ids(state) == {str(FILE_A), str(FILE_B)}


def test_burst_does_not_rejoin_after_unused_turn_demotion() -> None:
    """After active → recent, a later upload is a new burst even inside 120s."""
    state = empty_source_state()
    state = apply_chat_upload(state, str(FILE_A), now=NOW)
    state = apply_file_ready(state, str(FILE_A), now=NOW)
    state = apply_chat_turn(state, [], now=NOW)
    state = apply_chat_turn(state, [], now=NOW + timedelta(seconds=5))
    assert str(FILE_A) in state["recent_file_ids"]
    assert not state["active_file_ids"]
    state = apply_chat_upload(state, str(FILE_B), now=NOW + timedelta(seconds=10))
    assert state["pending_file_ids"] == [str(FILE_B)]
    assert str(FILE_A) in state["recent_file_ids"]
    assert str(FILE_A) not in state["active_file_ids"]
    assert str(FILE_A) not in state["pending_file_ids"]


def test_new_burst_supersedes_active() -> None:
    state = empty_source_state()
    state = apply_chat_upload(state, str(FILE_A), now=NOW)
    state = apply_file_ready(state, str(FILE_A), now=NOW)
    later = NOW + timedelta(seconds=UPLOAD_BURST_WINDOW_SECONDS + 1)
    state = apply_chat_upload(state, str(FILE_B), now=later)
    assert state["pending_file_ids"] == [str(FILE_B)]
    assert str(FILE_A) in state["recent_file_ids"]
    assert str(FILE_A) not in state["active_file_ids"]


def test_idle_ttl_demotes_active() -> None:
    state = empty_source_state()
    state = apply_chat_upload(state, str(FILE_A), now=NOW)
    state = apply_file_ready(state, str(FILE_A), now=NOW)
    later = NOW + timedelta(minutes=ACTIVE_SOURCE_IDLE_TTL_MINUTES, seconds=1)
    state = expire_active(state, now=later)
    assert state["active_file_ids"] == []
    assert str(FILE_A) in state["recent_file_ids"]


def test_failed_file_drops_from_pending() -> None:
    state = empty_source_state()
    state = apply_chat_upload(state, str(FILE_A), now=NOW)
    state = apply_chat_upload(state, str(FILE_B), now=NOW + timedelta(seconds=1))
    state = apply_file_failed(state, str(FILE_A))
    assert str(FILE_A) not in state["pending_file_ids"]
    assert str(FILE_B) in state["pending_file_ids"]
    assert _ids(state) == {str(FILE_B)}


def test_vision_and_draft_uploads_are_not_sources() -> None:
    assert is_vision_upload(media_type="image/png", filename="x.png") is True
    assert is_vision_upload(media_type="application/pdf", filename="A.pdf") is False
    assert parse_thread_uuid("draft-local-id") is None
    assert parse_thread_uuid(CHAT_ID) == uuid.UUID(CHAT_ID)


# --------------------------------------------------------------------------- #
# Representative pack                                                         #
# --------------------------------------------------------------------------- #


def _chunk(index: int, page: int, text: str, *, page_chunk: int = 0) -> PackChunk:
    return PackChunk(
        file_id=FILE_A,
        chunk_id=uuid.UUID(int=index + 1),
        page_number=page,
        document_chunk_index=index,
        page_chunk_index=page_chunk,
        text=text,
        char_count=len(text),
        page_char_count=len(text),
    )


def test_representative_pack_headings_tables_first_last_dedup() -> None:
    chunks = [
        _chunk(0, 1, "Introduction. " + ("body " * 40)),
        _chunk(1, 2, "## Scope\nTable 1 | col a | col b\n" + ("dense " * 80)),
        _chunk(2, 2, "## Scope\nTable 1 | col a | col b\n" + ("dense " * 80)),
        _chunk(3, 3, "short"),
        _chunk(4, 8, "Conclusion and next steps. " + ("end " * 30)),
    ]
    selected = select_representative_chunks(chunks)
    assert 1 <= len(selected) <= MAX_PACK_CHUNKS
    texts = [c.text for c in selected]
    assert any("Introduction" in t for t in texts)
    assert any("Scope" in t or "Table" in t for t in texts)
    assert any("Conclusion" in t for t in texts)
    assert len(texts) == len(set(texts))
    indexes = [c.document_chunk_index for c in selected]
    assert indexes == sorted(indexes)


# --------------------------------------------------------------------------- #
# Gate 3D / 4A allow-list (cases 2, 3, 6, 7, 8)                               #
# --------------------------------------------------------------------------- #


def _row(*, rid, name, text="content", status="ready", created_at=1, index_status="indexed"):
    return types.SimpleNamespace(
        org_id=ORG_A,
        workspace_id=WS_A,
        status=status,
        extracted_text=text,
        display_name=name,
        original_filename=name,
        created_at=created_at,
        id=rid,
        index_status=index_status,
        indexed_chunk_count=4,
        extraction_status="complete",
        extraction_truncated=False,
    )


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, *a, **k):
        return _FakeResult(self._rows)


def _patch_session(monkeypatch, rows):
    monkeypatch.setattr(
        "services.workspace_files.service.get_db_session",
        lambda: _FakeSession(rows),
    )


def _workspace_rows():
    return [
        _row(rid=FILE_A, name="A.pdf", text="proposal budget scope " * 20, created_at=3),
        _row(rid=FILE_B, name="B.pdf", text="compare annex signature " * 20, created_at=4),
        _row(rid=FILE_F0, name="F0-test.txt", text="f0 workspace filler " * 20, created_at=1),
        _row(rid=FILE_W1, name="W1-production-smoke.txt", text="w1 smoke filler " * 20, created_at=2),
    ]


@pytest.mark.asyncio
async def test_gate3d_allow_list_excludes_workspace_dump(monkeypatch):
    """Cases 2, 3: restriction allow-list; F0/W1 must not appear."""
    monkeypatch.delenv("BEN_WORKSPACE_CHUNK_RETRIEVAL", raising=False)
    _patch_session(monkeypatch, _workspace_rows())
    out = await load_ready_files_context(
        ORG_A,
        WS_A,
        max_chars=12_000,
        user_query="summarize the proposal",
        restrict_to_file_ids=[str(FILE_A)],
    )
    names = [u["name"] for u in out.used_files]
    assert names == ["A.pdf"]
    assert "F0-test.txt" not in (out.block or "")
    assert "W1-production-smoke.txt" not in (out.block or "")
    assert "f0 workspace" not in (out.block or "")


@pytest.mark.asyncio
async def test_gate3d_empty_restriction_intersection_injects_nothing(monkeypatch):
    """Case 8: pending A is not READY; do not dump Workspace Files."""
    monkeypatch.delenv("BEN_WORKSPACE_CHUNK_RETRIEVAL", raising=False)
    rows = [
        _row(rid=FILE_A, name="A.pdf", text="proposal", status="processing"),
        _row(rid=FILE_F0, name="F0-test.txt", text="f0 workspace filler " * 20),
        _row(rid=FILE_W1, name="W1-production-smoke.txt", text="w1 smoke filler " * 20),
    ]
    _patch_session(monkeypatch, rows)
    out = await load_ready_files_context(
        ORG_A,
        WS_A,
        max_chars=12_000,
        user_query="hello",
        restrict_to_file_ids=[str(FILE_A)],
    )
    assert out.count == 0
    assert out.block == ""
    assert out.used_files == ()
    assert "F0-test.txt" not in (out.block or "")


@pytest.mark.asyncio
async def test_gate3d_named_overrides_restriction(monkeypatch):
    """Case 7."""
    monkeypatch.delenv("BEN_WORKSPACE_CHUNK_RETRIEVAL", raising=False)
    _patch_session(monkeypatch, _workspace_rows())
    out = await load_ready_files_context(
        ORG_A,
        WS_A,
        max_chars=12_000,
        user_query="compare with B.pdf",
        restrict_to_file_ids=[str(FILE_A)],
    )
    names = [u["name"] for u in out.used_files]
    assert names == ["B.pdf"]
    assert "A.pdf" not in names
    assert "F0-test.txt" not in names


@pytest.mark.asyncio
async def test_gate3d_burst_ab_only(monkeypatch):
    """Case 6."""
    monkeypatch.delenv("BEN_WORKSPACE_CHUNK_RETRIEVAL", raising=False)
    _patch_session(monkeypatch, _workspace_rows())
    out = await load_ready_files_context(
        ORG_A,
        WS_A,
        max_chars=12_000,
        user_query="compare these",
        restrict_to_file_ids=[str(FILE_A), str(FILE_B)],
    )
    names = {u["name"] for u in out.used_files}
    assert names == {"A.pdf", "B.pdf"}
    assert "F0-test.txt" not in names


@pytest.mark.asyncio
async def test_unrestricted_still_selects_workspace_files(monkeypatch):
    """After recent, Workspace retrieval is not allow-listed."""
    monkeypatch.delenv("BEN_WORKSPACE_CHUNK_RETRIEVAL", raising=False)
    _patch_session(monkeypatch, _workspace_rows())
    out = await load_ready_files_context(
        ORG_A,
        WS_A,
        max_chars=12_000,
        user_query="summarize the proposal",
        restrict_to_file_ids=None,
    )
    names = {u["name"] for u in out.used_files}
    assert "F0-test.txt" in names or "W1-production-smoke.txt" in names or "A.pdf" in names
    assert out.count >= 1


@pytest.mark.asyncio
async def test_gate4a_allow_list_is_search_set_not_boost(monkeypatch):
    monkeypatch.setattr(
        "services.workspace_files.service.chunk_retrieval_enabled",
        lambda _ws: True,
    )
    captured: dict = {}

    async def fake_prove(session, *, org_id, workspace_id, file_ids):
        captured["proved"] = [str(x) for x in file_ids]
        return {fid: 3 for fid in file_ids}

    async def fake_search(session, *, org_id, workspace_id, file_ids, tsquery, timeout_s=1.5):
        captured["searched"] = [str(x) for x in file_ids]
        hits = [
            ChunkHit(
                chunk_id=uuid.uuid4(),
                file_id=fid,
                page_number=1,
                document_chunk_index=0,
                text=f"proposal evidence for {fid}",
                char_count=40,
                rank=1.0,
            )
            for fid in file_ids
        ]
        return hits, 1.0, None

    monkeypatch.setattr("services.workspace_files.service.prove_chunk_rows", fake_prove)
    monkeypatch.setattr("services.workspace_files.service.search_chunks_bounded", fake_search)
    _patch_session(monkeypatch, _workspace_rows())
    out = await load_ready_files_context(
        ORG_A,
        WS_A,
        max_chars=12_000,
        user_query="summarize the proposal",
        restrict_to_file_ids=[str(FILE_A)],
    )
    assert captured.get("searched") == [str(FILE_A)]
    assert captured.get("proved") == [str(FILE_A)]
    names = [u["name"] for u in out.used_files]
    assert names == ["A.pdf"]
    assert str(FILE_F0) not in captured.get("searched", [])
    assert str(FILE_W1) not in captured.get("searched", [])


@pytest.mark.asyncio
async def test_gate4a_empty_restriction_does_not_search_workspace(monkeypatch):
    monkeypatch.setattr(
        "services.workspace_files.service.chunk_retrieval_enabled",
        lambda _ws: True,
    )
    searched = {"called": False}

    async def boom(*a, **k):
        searched["called"] = True
        raise AssertionError("FTS must not run on an empty restriction intersection")

    monkeypatch.setattr("services.workspace_files.service.prove_chunk_rows", boom)
    monkeypatch.setattr("services.workspace_files.service.search_chunks_bounded", boom)
    rows = [
        _row(rid=FILE_F0, name="F0-test.txt", text="f0 workspace filler " * 20),
        _row(rid=FILE_W1, name="W1-production-smoke.txt", text="w1 smoke filler " * 20),
    ]
    _patch_session(monkeypatch, rows)
    out = await load_ready_files_context(
        ORG_A,
        WS_A,
        max_chars=12_000,
        user_query="summarize the proposal",
        restrict_to_file_ids=[str(FILE_A)],
    )
    assert searched["called"] is False
    assert out.block == ""
    assert out.used_files == ()
    assert out.fallback_reason == "source_restricted_empty"


# --------------------------------------------------------------------------- #
# Initial Read: idempotent, non-blocking                                      #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_initial_read_claim_is_idempotent(monkeypatch):
    from services.workspace_files.initial_read import claim_initial_read

    wf = types.SimpleNamespace(
        id=FILE_A,
        org_id=ORG_A,
        status="ready",
        media_type="application/pdf",
        original_filename="A.pdf",
        source_chat_id=CHAT_ID,
        initial_read_status="none",
        initial_read_at=None,
    )

    class _ClaimSession:
        def __init__(self, row):
            self.row = row
            self.commits = 0
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
            self.commits += 1

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
    first = await claim_initial_read(ORG_A, FILE_A)
    assert first is wf
    assert wf.initial_read_status == "pending"

    second_session = _ClaimSession(wf)
    monkeypatch.setattr(
        "services.workspace_files.initial_read.get_db_session",
        lambda: second_session,
    )
    second = await claim_initial_read(ORG_A, FILE_A)
    assert second is wf
    assert wf.initial_read_status == "pending"

    wf.initial_read_status = "complete"
    third_session = _ClaimSession(wf)
    monkeypatch.setattr(
        "services.workspace_files.initial_read.get_db_session",
        lambda: third_session,
    )
    third = await claim_initial_read(ORG_A, FILE_A)
    assert third is None


def test_schedule_initial_read_never_blocks_on_llm() -> None:
    from services.workspace_files.initial_read import schedule_initial_read

    with patch("services.workspace_files.initial_read.asyncio.create_task") as create_task:
        def _create(coro, **_k):
            getattr(coro, "close", lambda: None)()
            return MagicMock()

        create_task.side_effect = _create
        ok = schedule_initial_read(ORG_A, WS_A, FILE_A)
    assert ok is True
    create_task.assert_called_once()
    drain = (ROOT / "services" / "workspace_files" / "drain.py").read_text(encoding="utf-8")
    upload = (ROOT / "services" / "workspace_files" / "service.py").read_text(encoding="utf-8")
    initial = (ROOT / "services" / "workspace_files" / "initial_read.py").read_text(encoding="utf-8")
    assert "await run_initial_read" not in drain
    assert "await run_initial_read" not in upload
    assert "create_task" in initial
    assert inspect.iscoroutinefunction(
        __import__("services.workspace_files.initial_read", fromlist=["notify_file_processed"]).notify_file_processed
    )


@pytest.mark.asyncio
async def test_notify_file_processed_does_not_await_run_initial_read(monkeypatch):
    from services.workspace_files import initial_read

    ready = AsyncMock(return_value={})
    monkeypatch.setattr(initial_read, "on_file_ready", ready)
    enqueue = AsyncMock(return_value={"created": True})
    monkeypatch.setattr(initial_read, "enqueue_initial_read_job", enqueue)
    with patch.object(initial_read, "schedule_initial_read", return_value=True) as sched:
        with patch.object(initial_read, "run_initial_read", new=AsyncMock()) as run:
            await initial_read.notify_file_processed(
                org_id=ORG_A, workspace_id=WS_A, file_id=FILE_A, ready=True
            )
            run.assert_not_awaited()
            enqueue.assert_awaited_once()
            sched.assert_called_once_with(ORG_A, WS_A, FILE_A)
    ready.assert_awaited_once()


@pytest.mark.asyncio
async def test_initial_read_persists_grounded_chat_message(monkeypatch):
    """Case 1: READY → one grounded overview of A, kind=chat."""
    from services.workspace_files import initial_read
    from services.workspace_files.initial_read_pack import PackChunk as PC

    claimed = types.SimpleNamespace(
        id=FILE_A,
        source_chat_id=CHAT_ID,
        display_name="A.pdf",
        original_filename="A.pdf",
        page_count=3,
        extraction_status="complete",
        extracted_text="legacy unused",
    )
    monkeypatch.setattr(initial_read, "claim_initial_read", AsyncMock(return_value=claimed))
    monkeypatch.setattr(initial_read, "sqlite_has_initial_read", lambda *_a, **_k: False)
    monkeypatch.setattr(
        initial_read,
        "load_pack_chunks",
        AsyncMock(
            return_value=[
                PC(
                    file_id=FILE_A,
                    chunk_id=uuid.uuid4(),
                    page_number=1,
                    document_chunk_index=0,
                    page_chunk_index=0,
                    text="The proposal budget is $12M on page one.",
                    char_count=40,
                    page_char_count=40,
                )
            ]
        ),
    )
    monkeypatch.setattr(initial_read, "page_coverage", AsyncMock(return_value=(3, 0)))
    monkeypatch.setattr(
        initial_read,
        "route_request",
        AsyncMock(
            return_value={
                "content": "This is a proposal. Finding: budget is $12M (page 1).",
                "model_used": "test-model",
                "cost_usd": 0.0,
                "provider_used": "gpt",
            }
        ),
    )
    persisted: dict = {}

    def _persist(tid, *, encoded_content, provider=None):
        persisted["thread_id"] = str(tid)
        persisted["encoded"] = encoded_content
        persisted["provider"] = provider
        return 1

    monkeypatch.setattr(initial_read, "persist_assistant_message_sqlite", _persist)
    monkeypatch.setattr(initial_read, "_mark_initial_read", AsyncMock())

    class _Pg:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, *a, **k):
            return MagicMock()

        def add(self, obj):
            persisted["pg_role"] = obj.role
            persisted["pg_content"] = obj.content

        async def commit(self):
            persisted["pg_committed"] = True

    monkeypatch.setattr(initial_read, "get_db_session", lambda: _Pg())

    out = await initial_read.run_initial_read(ORG_A, WS_A, FILE_A)
    assert out["outcome"] == "ok"
    decoded = decode_message("assistant", persisted["encoded"])
    assert decoded["source_event"] == FILE_INITIAL_READ_EVENT
    assert decoded["source_file_id"] == str(FILE_A)
    assert decoded["used_files"][0]["name"] == "A.pdf"
    assert "file_overview" not in persisted["encoded"]
    assert '"kind": "chat"' in persisted["encoded"]
    assert "proposal" in decoded["content"].lower()


@pytest.mark.asyncio
async def test_duplicate_wake_skips_when_overview_already_in_sqlite(monkeypatch):
    from services.workspace_files import initial_read

    monkeypatch.setattr(initial_read, "claim_initial_read", AsyncMock(return_value=None))
    route = AsyncMock()
    monkeypatch.setattr(initial_read, "route_request", route)
    out = await initial_read.run_initial_read(ORG_A, WS_A, FILE_A)
    assert out["outcome"] == "skipped"
    route.assert_not_awaited()


@pytest.mark.asyncio
async def test_initial_read_crash_then_recovery_persists_once(monkeypatch):
    """Blocker 1: claim → worker dies → recovery → exactly one overview."""
    from services.workspace_files import initial_read
    from services.workspace_files.initial_read_pack import PackChunk as PC

    claimed = types.SimpleNamespace(
        id=FILE_A,
        source_chat_id=CHAT_ID,
        display_name="A.pdf",
        original_filename="A.pdf",
        page_count=3,
        extraction_status="complete",
        extracted_text="legacy unused",
        initial_read_status="pending",
    )
    persisted: list[str] = []
    calls = {"route": 0}

    monkeypatch.setattr(initial_read, "claim_initial_read", AsyncMock(return_value=claimed))
    monkeypatch.setattr(
        initial_read,
        "sqlite_has_initial_read",
        lambda *_a, **_k: bool(persisted),
    )
    monkeypatch.setattr(
        initial_read,
        "load_pack_chunks",
        AsyncMock(
            return_value=[
                PC(
                    file_id=FILE_A,
                    chunk_id=uuid.uuid4(),
                    page_number=1,
                    document_chunk_index=0,
                    page_chunk_index=0,
                    text="The proposal budget is $12M on page one.",
                    char_count=40,
                    page_char_count=40,
                )
            ]
        ),
    )
    monkeypatch.setattr(initial_read, "page_coverage", AsyncMock(return_value=(3, 0)))

    async def flaky_route(*_a, **_k):
        calls["route"] += 1
        if calls["route"] == 1:
            raise RuntimeError("worker died after claim")
        return {
            "content": "Grounded overview of A.",
            "model_used": "test-model",
            "cost_usd": 0.0,
            "provider_used": "gpt",
        }

    monkeypatch.setattr(initial_read, "route_request", flaky_route)
    monkeypatch.setattr(
        initial_read,
        "persist_assistant_message_sqlite",
        lambda tid, *, encoded_content, provider=None: persisted.append(encoded_content) or 1,
    )
    monkeypatch.setattr(initial_read, "_mark_initial_read", AsyncMock())

    class _Pg:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, *a, **k):
            return MagicMock()

        def add(self, obj):
            return None

        async def commit(self):
            return None

    monkeypatch.setattr(initial_read, "get_db_session", lambda: _Pg())

    first = await initial_read.run_initial_read(ORG_A, WS_A, FILE_A)
    assert first["outcome"] == "failed"
    assert persisted == []
    assert claimed.initial_read_status == "pending"

    second = await initial_read.run_initial_read(ORG_A, WS_A, FILE_A)
    assert second["outcome"] == "ok"
    assert len(persisted) == 1
    assert FILE_INITIAL_READ_EVENT in persisted[0]
    assert "file_overview" not in persisted[0]

    third = await initial_read.run_initial_read(ORG_A, WS_A, FILE_A)
    assert third["outcome"] == "already_present"
    assert calls["route"] == 2
    assert len(persisted) == 1


@pytest.mark.asyncio
async def test_initial_read_job_claim_crash_then_recovery_persists_once(monkeypatch):
    """Blocker 1: durable job claimed → worker dies → drain recovers → one overview."""
    from services.workspace_files import initial_read

    job = {
        "job_id": str(uuid.uuid4()),
        "org_id": str(ORG_A),
        "workspace_id": str(WS_A),
        "file_id": str(FILE_A),
        "job_type": "file_initial_read",
        "attempts": 1,
    }
    state = {"claims": 0, "runs": 0}
    persisted: list[str] = []
    completed: list[tuple] = []

    async def claim(*_a, **_k):
        state["claims"] += 1
        if state["claims"] > 2:
            return []
        return [dict(job, attempts=state["claims"])]

    async def run(*_a, **_k):
        state["runs"] += 1
        if state["runs"] == 1:
            raise RuntimeError("worker died after claim")
        persisted.append("overview")
        return {"outcome": "ok"}

    monkeypatch.setattr(initial_read, "claim_file_initial_read_job_for_file", claim)
    monkeypatch.setattr(initial_read, "reap_expired_jobs_for_file", AsyncMock(return_value=[{"reaped": True}]))
    monkeypatch.setattr(initial_read, "run_initial_read", run)
    monkeypatch.setattr(
        initial_read,
        "complete_job",
        AsyncMock(side_effect=lambda *a, **k: completed.append((a, k))),
    )
    monkeypatch.setattr(initial_read, "requeue_job", AsyncMock())
    monkeypatch.setattr(initial_read, "sync_failed_file_initial_reads", AsyncMock(return_value=0))

    first = await initial_read.drain_file_initial_read_for_file(FILE_A)
    assert first["requeued"] == 1
    assert persisted == []
    assert completed == []

    second = await initial_read.drain_file_initial_read_for_file(FILE_A)
    assert second["succeeded"] == 1
    assert persisted == ["overview"]
    assert completed[0][0][1] == "succeeded"

    third = await initial_read.drain_file_initial_read_for_file(FILE_A)
    assert third["claimed"] == 0
    assert persisted == ["overview"]
    assert len(completed) == 1


@pytest.mark.asyncio
async def test_initial_read_exhausted_marks_file_failed(monkeypatch):
    """Bounded retry: exhausted job fails the file so the UI stops polling."""
    from services.workspace_files import initial_read

    job = {
        "job_id": str(uuid.uuid4()),
        "org_id": str(ORG_A),
        "workspace_id": str(WS_A),
        "file_id": str(FILE_A),
        "job_type": "file_initial_read",
        "attempts": INITIAL_READ_MAX_ATTEMPTS,
    }
    marked: list[str] = []
    completed: list[tuple] = []

    async def claim(*_a, **_k):
        return [job]

    monkeypatch.setattr(initial_read, "claim_file_initial_read_job_for_file", claim)
    monkeypatch.setattr(initial_read, "reap_expired_jobs_for_file", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        initial_read,
        "run_initial_read",
        AsyncMock(return_value={"outcome": "failed", "reason": "empty_model"}),
    )
    monkeypatch.setattr(
        initial_read,
        "complete_job",
        AsyncMock(side_effect=lambda *a, **k: completed.append((a, k))),
    )
    monkeypatch.setattr(initial_read, "requeue_job", AsyncMock())
    monkeypatch.setattr(
        initial_read,
        "_mark_initial_read",
        AsyncMock(side_effect=lambda _o, _f, status: marked.append(status)),
    )
    monkeypatch.setattr(initial_read, "sync_failed_file_initial_reads", AsyncMock(return_value=1))

    out = await initial_read.drain_file_initial_read_for_file(FILE_A)
    assert out["failed"] == 1
    assert completed[0][0][1] == "failed"
    assert marked == [INITIAL_READ_FAILED]


@pytest.mark.asyncio
async def test_extraction_drain_requeues_initial_read_without_llm(monkeypatch):
    from services.workspace_files import drain

    requeued: list[str] = []
    monkeypatch.setattr(
        drain,
        "requeue_job",
        AsyncMock(side_effect=lambda jid, **k: requeued.append(k.get("error_code") or "")),
    )
    monkeypatch.setattr(drain, "complete_job", AsyncMock())
    monkeypatch.setattr(drain, "file_is_ingest_protected", lambda *_a, **_k: False)
    monkeypatch.setattr(drain, "stamp_claimed_jobs", lambda *_a, **_k: None)
    run_llm = AsyncMock()
    monkeypatch.setattr("services.workspace_files.initial_read.run_initial_read", run_llm)

    summary = {"requeued": 0, "failed": 0, "succeeded": 0}
    job = {
        "job_id": str(uuid.uuid4()),
        "org_id": str(ORG_A),
        "workspace_id": str(WS_A),
        "file_id": str(FILE_A),
        "job_type": "file_initial_read",
        "attempts": 1,
    }
    await drain._run_claimed_jobs(
        [job],
        summary,
        worker_id="w",
        per_job_timeout_s=1,
        max_attempts=5,
    )
    assert summary["requeued"] == 1
    assert requeued == ["wrong_drain"]
    drain.complete_job.assert_not_awaited()
    run_llm.assert_not_awaited()


# --------------------------------------------------------------------------- #
# Chat wiring: restriction passed into retrieval                              #
# --------------------------------------------------------------------------- #


def _patch_stream_pipeline(monkeypatch, captured):
    import services.chat_service as chat_service

    captured.setdefault("record_calls", [])

    async def fake_record(*a, **k):
        captured["record_calls"].append({"args": a, "kwargs": k})
        return empty_source_state()

    monkeypatch.setattr(chat_service, "record_standard_chat_turn", fake_record)

    async def fake_stream(message, tenant_id, tier, *, provider_id=None, model_override=None, system=None):
        captured["message"] = message
        yield ("ok", "model-x", "openai")

    async def _aid(*a, **k):
        return uuid.UUID(CHAT_ID)

    monkeypatch.setattr(chat_service, "resolve_thread_id", lambda *a, **k: _aid())
    monkeypatch.setattr(chat_service, "is_project_setup_thread", lambda _tid: False)

    async def _ctx(_o, _t, m):
        return m

    async def _knowledge(_m, payload):
        return payload

    monkeypatch.setattr(chat_service, "build_chat_message_with_thread_context", _ctx)
    monkeypatch.setattr(chat_service, "inject_knowledge_few_shot", _knowledge)
    monkeypatch.setattr(chat_service, "apply_language_context", lambda msg, _lang: msg)
    monkeypatch.setattr(chat_service, "route_request_stream", fake_stream)
    monkeypatch.setattr(chat_service, "persist_chat_exchange_sqlite", lambda *a, **k: (1, 2))

    def _sched(coro):
        getattr(coro, "close", lambda: None)()

    monkeypatch.setattr(chat_service, "_schedule_chat_persist", _sched)
    monkeypatch.setattr(chat_service, "run_copilot_preamble", AsyncMock(return_value=[]))

    async def _no_vision(*a, **k):
        return None

    monkeypatch.setattr(chat_service, "_current_turn_vision_user_content", _no_vision)


@pytest.mark.asyncio
async def test_chat_passes_pending_restriction_and_does_not_dump(monkeypatch):
    """Case 8 wiring: pending A is forwarded as an allow-list."""
    import services.chat_service as chat_service

    captured: dict = {}
    _patch_stream_pipeline(monkeypatch, captured)
    state = apply_chat_upload(empty_source_state(), str(FILE_A), now=NOW)

    async def fake_mut(*a, **k):
        return state

    async def fake_load(*a, **k):
        return state

    async def fake_turn(*a, **k):
        captured["recorded_used"] = k.get("used_file_ids")
        return state

    monkeypatch.setattr(chat_service, "mutate_source_state", fake_mut)
    monkeypatch.setattr(chat_service, "load_source_state", fake_load)
    monkeypatch.setattr(chat_service, "record_standard_chat_turn", fake_turn)

    async def fake_ctx(_org, _ws, *, max_chars, user_query=None, restrict_to_file_ids=None, **_k):
        captured["restrict"] = restrict_to_file_ids
        captured["user_query"] = user_query
        return WorkspaceFilesContext(block="", count=0, chars=0, truncated=False, used_files=())

    monkeypatch.setattr(chat_service, "load_ready_files_context", fake_ctx)

    events = []
    async for line in chat_service.stream_chat_response(
        "hello while processing",
        "user-1",
        str(ORG_A),
        "free",
        thread_id=uuid.UUID(CHAT_ID),
        provider_id="gpt",
        project_id=WS_A,
    ):
        events.append(line)

    assert captured["restrict"] == [str(FILE_A)]
    assert "<workspace_files>" not in captured.get("message", "")
    done = [e for e in events if '"type": "done"' in e or '"type":"done"' in e]
    assert done
    assert captured["recorded_used"] == []


@pytest.mark.asyncio
async def test_chat_source_state_error_is_fail_closed(monkeypatch):
    import services.chat_service as chat_service

    captured: dict = {}
    _patch_stream_pipeline(monkeypatch, captured)

    async def boom(*a, **k):
        raise RuntimeError("postgres unavailable")

    monkeypatch.setattr(chat_service, "mutate_source_state", boom)

    async def fake_ctx(_org, _ws, *, max_chars, user_query=None, restrict_to_file_ids=None, **_k):
        captured["restrict"] = restrict_to_file_ids
        return WorkspaceFilesContext(block="", count=0, chars=0, truncated=False, used_files=())

    monkeypatch.setattr(chat_service, "load_ready_files_context", fake_ctx)

    async for _line in chat_service.stream_chat_response(
        "hello",
        "user-1",
        str(ORG_A),
        "free",
        thread_id=uuid.UUID(CHAT_ID),
        provider_id="gpt",
        project_id=WS_A,
    ):
        pass

    assert captured["restrict"] == []
    assert len(captured["record_calls"]) == 1


def _source_state_chat_patches(monkeypatch, chat_service, captured, state):
    async def fake_mut(*a, **k):
        return state

    async def fake_load(*a, **k):
        return state

    monkeypatch.setattr(chat_service, "mutate_source_state", fake_mut)
    monkeypatch.setattr(chat_service, "load_source_state", fake_load)

    async def fake_ctx(_org, _ws, *, max_chars, user_query=None, restrict_to_file_ids=None, **_k):
        captured["restrict"] = restrict_to_file_ids
        captured["user_query"] = user_query
        return WorkspaceFilesContext(block="", count=0, chars=0, truncated=False, used_files=())

    monkeypatch.setattr(chat_service, "load_ready_files_context", fake_ctx)


@pytest.mark.asyncio
async def test_chat_compare_these_after_ready_then_second_upload(monkeypatch):
    """Blocker 2 wiring: A READY then B within burst → restriction {A,B}."""
    import services.chat_service as chat_service

    captured: dict = {}
    _patch_stream_pipeline(monkeypatch, captured)
    state = empty_source_state()
    state = apply_chat_upload(state, str(FILE_A), now=NOW)
    state = apply_file_ready(state, str(FILE_A), now=NOW + timedelta(seconds=1))
    state = apply_chat_upload(state, str(FILE_B), now=NOW + timedelta(seconds=10))
    state = apply_file_ready(state, str(FILE_B), now=NOW + timedelta(seconds=11))
    _source_state_chat_patches(monkeypatch, chat_service, captured, state)

    async for _line in chat_service.stream_chat_response(
        "compare these",
        "user-1",
        str(ORG_A),
        "free",
        thread_id=uuid.UUID(CHAT_ID),
        provider_id="gpt",
        project_id=WS_A,
    ):
        pass

    assert set(captured["restrict"] or []) == {str(FILE_A), str(FILE_B)}
    assert len(captured["record_calls"]) == 1


@pytest.mark.asyncio
async def test_chat_compare_these_reverse_ready_order(monkeypatch):
    """Blocker 2 wiring: B READY before A still restricts to {A,B}."""
    import services.chat_service as chat_service

    captured: dict = {}
    _patch_stream_pipeline(monkeypatch, captured)
    t0 = NOW
    state = empty_source_state()
    state = apply_chat_upload(state, str(FILE_A), now=t0)
    state = apply_chat_upload(state, str(FILE_B), now=t0 + timedelta(seconds=2))
    state = apply_file_ready(state, str(FILE_B), now=t0 + timedelta(seconds=3))
    state = apply_file_ready(state, str(FILE_A), now=t0 + timedelta(seconds=4))
    _source_state_chat_patches(monkeypatch, chat_service, captured, state)

    async for _line in chat_service.stream_chat_response(
        "compare these",
        "user-1",
        str(ORG_A),
        "free",
        thread_id=uuid.UUID(CHAT_ID),
        provider_id="gpt",
        project_id=WS_A,
    ):
        pass

    assert set(captured["restrict"] or []) == {str(FILE_A), str(FILE_B)}
    assert len(captured["record_calls"]) == 1


@pytest.mark.asyncio
async def test_provider_error_does_not_record_source_turn(monkeypatch):
    import services.chat_service as chat_service

    captured: dict = {}
    _patch_stream_pipeline(monkeypatch, captured)
    state = apply_chat_upload(empty_source_state(), str(FILE_A), now=NOW)
    state = apply_file_ready(state, str(FILE_A), now=NOW)
    _source_state_chat_patches(monkeypatch, chat_service, captured, state)

    async def err_stream(*_a, **_k):
        yield ("provider 500", None, None)

    monkeypatch.setattr(chat_service, "route_request_stream", err_stream)
    events = []
    async for line in chat_service.stream_chat_response(
        "summarize this",
        "user-1",
        str(ORG_A),
        "free",
        thread_id=uuid.UUID(CHAT_ID),
        provider_id="gpt",
        project_id=WS_A,
    ):
        events.append(line)

    assert captured["record_calls"] == []
    assert any('"type": "error"' in e or '"type":"error"' in e for e in events)


@pytest.mark.asyncio
async def test_provider_exception_does_not_record_source_turn(monkeypatch):
    import services.chat_service as chat_service

    captured: dict = {}
    _patch_stream_pipeline(monkeypatch, captured)
    state = apply_chat_upload(empty_source_state(), str(FILE_A), now=NOW)
    state = apply_file_ready(state, str(FILE_A), now=NOW)
    _source_state_chat_patches(monkeypatch, chat_service, captured, state)

    async def boom_stream(*_a, **_k):
        if False:
            yield ("x", "model-x", "openai")
        raise RuntimeError("stream failed")

    monkeypatch.setattr(chat_service, "route_request_stream", boom_stream)
    events = []
    async for line in chat_service.stream_chat_response(
        "summarize this",
        "user-1",
        str(ORG_A),
        "free",
        thread_id=uuid.UUID(CHAT_ID),
        provider_id="gpt",
        project_id=WS_A,
    ):
        events.append(line)

    assert captured["record_calls"] == []
    assert any('"type": "error"' in e or '"type":"error"' in e for e in events)


@pytest.mark.asyncio
async def test_cancelled_stream_does_not_record_source_turn(monkeypatch):
    import services.chat_service as chat_service

    captured: dict = {}
    _patch_stream_pipeline(monkeypatch, captured)
    state = apply_chat_upload(empty_source_state(), str(FILE_A), now=NOW)
    state = apply_file_ready(state, str(FILE_A), now=NOW)
    _source_state_chat_patches(monkeypatch, chat_service, captured, state)

    async def cancel_stream(*_a, **_k):
        if False:
            yield ("x", "model-x", "openai")
        raise asyncio.CancelledError()

    monkeypatch.setattr(chat_service, "route_request_stream", cancel_stream)
    with pytest.raises(asyncio.CancelledError):
        async for _line in chat_service.stream_chat_response(
            "summarize this",
            "user-1",
            str(ORG_A),
            "free",
            thread_id=uuid.UUID(CHAT_ID),
            provider_id="gpt",
            project_id=WS_A,
        ):
            pass

    assert captured["record_calls"] == []


@pytest.mark.asyncio
async def test_empty_model_tokens_do_not_record_source_turn(monkeypatch):
    import services.chat_service as chat_service

    captured: dict = {}
    _patch_stream_pipeline(monkeypatch, captured)
    state = apply_chat_upload(empty_source_state(), str(FILE_A), now=NOW)
    state = apply_file_ready(state, str(FILE_A), now=NOW)
    _source_state_chat_patches(monkeypatch, chat_service, captured, state)

    async def empty_stream(*_a, **_k):
        yield ("", "model-x", "openai")

    monkeypatch.setattr(chat_service, "route_request_stream", empty_stream)
    async for _line in chat_service.stream_chat_response(
        "summarize this",
        "user-1",
        str(ORG_A),
        "free",
        thread_id=uuid.UUID(CHAT_ID),
        provider_id="gpt",
        project_id=WS_A,
    ):
        pass

    assert captured["record_calls"] == []
