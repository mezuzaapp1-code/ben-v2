"""Multi-Source Resolution V1 — deterministic resolver, allow-list, coverage fill."""
from __future__ import annotations

import json
import pathlib
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from services.workspace_files.chunk_retriever import ChunkHit
from services.workspace_files.multi_source import (
    INTENT_LAST_TWO,
    INTENT_PAIR,
    INTENT_PREVIOUS_CURRENT,
    INTENT_UNCOUNTED,
    MODE_CLARIFY,
    MODE_MULTI,
    MODE_PASSTHROUGH,
    MULTI_SOURCE_GROUNDING_HINT,
    classify_multi_source_intent,
    clarification_text,
    explicit_named_set_incomplete,
    filename_mentions_in_query,
    resolve_turn_sources,
    restrict_arg_for_resolution,
)
from services.workspace_files.service import WorkspaceFilesContext, load_ready_files_context
from services.workspace_files.source_policy import UPLOAD_BURST_WINDOW_SECONDS
from services.workspace_files.thread_sources import (
    apply_chat_turn,
    apply_chat_upload,
    apply_file_ready,
    empty_source_state,
)

from tests.test_document_upload_intelligence_v1 import (
    CHAT_ID,
    FILE_A,
    FILE_B,
    FILE_F0,
    FILE_W1,
    NOW,
    ORG_A,
    WS_A,
    _patch_session,
    _patch_stream_pipeline,
    _row,
    _source_state_chat_patches,
    _workspace_rows,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _sequential_uploads(*file_ids: uuid.UUID, start: datetime = NOW):
    state = empty_source_state()
    t = start
    for fid in file_ids:
        state = apply_chat_upload(state, str(fid), now=t)
        state = apply_file_ready(state, str(fid), now=t + timedelta(seconds=1))
        t = t + timedelta(seconds=UPLOAD_BURST_WINDOW_SECONDS + 1)
    return state


def _burst_ab(start: datetime = NOW):
    state = empty_source_state()
    state = apply_chat_upload(state, str(FILE_A), now=start)
    state = apply_chat_upload(state, str(FILE_B), now=start + timedelta(seconds=2))
    state = apply_file_ready(state, str(FILE_A), now=start + timedelta(seconds=3))
    state = apply_file_ready(state, str(FILE_B), now=start + timedelta(seconds=4))
    return state


# --------------------------------------------------------------------------- #
# Intent classification                                                       #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("query", "intent"),
    [
        ("כמה 2 ההצעות יחד", INTENT_PAIR),
        ("שתי ההצעות", INTENT_PAIR),
        ("שני הקבצים", INTENT_PAIR),
        ("both files", INTENT_PAIR),
        ("both proposals", INTENT_PAIR),
        ("compare both", INTENT_PAIR),
        ("sum both proposals", INTENT_PAIR),
        ("the two proposals", INTENT_PAIR),
        ("compare them", INTENT_UNCOUNTED),
        ("compare these", INTENT_UNCOUNTED),
        ("compare the proposals", INTENT_UNCOUNTED),
        ("the last two files", INTENT_LAST_TWO),
        ("שני האחרונים", INTENT_LAST_TWO),
        ("שתי האחרונות", INTENT_LAST_TWO),
        ("the previous file and this one", INTENT_PREVIOUS_CURRENT),
        ("הקובץ הקודם והנוכחי", INTENT_PREVIOUS_CURRENT),
        ("הקודם והנוכחי", INTENT_PREVIOUS_CURRENT),
        ("מה הסכום של ההצעה?", None),
        ("מה הסכום הכולל של ההצעה?", None),
        ("hello while processing", None),
    ],
)
def test_classify_hebrew_english_vocabulary(query: str, intent: str | None) -> None:
    assert classify_multi_source_intent(query) == intent


# --------------------------------------------------------------------------- #
# Resolver matrix                                                             #
# --------------------------------------------------------------------------- #


def test_sequential_ab_singular_stays_b_only() -> None:
    state = _sequential_uploads(FILE_A, FILE_B)
    res = resolve_turn_sources("מה הסכום של ההצעה?", state)
    assert res.mode == MODE_PASSTHROUGH
    assert res.file_ids == (str(FILE_B),)
    assert restrict_arg_for_resolution(res) == [str(FILE_B)]


def test_sequential_ab_pair_hebrew_resolves_ab() -> None:
    state = _sequential_uploads(FILE_A, FILE_B)
    res = resolve_turn_sources("כמה 2 ההצעות יחד", state)
    assert res.mode == MODE_MULTI
    assert set(res.file_ids) == {str(FILE_A), str(FILE_B)}
    assert restrict_arg_for_resolution(res) == list(res.file_ids)
    assert restrict_arg_for_resolution(res) is not None


def test_same_burst_compare_them_is_ab() -> None:
    state = _burst_ab()
    res = resolve_turn_sources("compare them", state)
    assert res.mode == MODE_MULTI
    assert set(res.file_ids) == {str(FILE_A), str(FILE_B)}


def test_last_two_of_ten_is_exactly_last_two() -> None:
    ids = [uuid.uuid4() for _ in range(10)]
    state = _sequential_uploads(*ids)
    res = resolve_turn_sources("the last two proposals", state)
    assert res.mode == MODE_MULTI
    assert res.file_ids == (str(ids[-2]), str(ids[-1]))
    assert restrict_arg_for_resolution(res) == [str(ids[-2]), str(ids[-1])]


def test_uncounted_plural_with_five_candidates_clarifies() -> None:
    ids = [uuid.uuid4() for _ in range(5)]
    state = _sequential_uploads(*ids)
    res = resolve_turn_sources("compare the proposals", state)
    assert res.mode == MODE_CLARIFY
    assert res.file_ids == ()
    assert restrict_arg_for_resolution(res) == []


def test_previous_and_current_is_recent_plus_active() -> None:
    state = _sequential_uploads(FILE_A, FILE_B)
    res = resolve_turn_sources("הקודם והנוכחי", state)
    assert res.mode == MODE_MULTI
    assert res.file_ids == (str(FILE_A), str(FILE_B))


def test_pair_with_one_conversation_file_clarifies() -> None:
    state = _sequential_uploads(FILE_A)
    res = resolve_turn_sources("both proposals", state)
    assert res.mode == MODE_CLARIFY
    assert restrict_arg_for_resolution(res) == []


def test_pair_with_one_ready_id_clarifies() -> None:
    state = _sequential_uploads(FILE_A, FILE_B)
    res = resolve_turn_sources(
        "כמה 2 ההצעות יחד",
        state,
        ready_ids={str(FILE_B)},
    )
    assert res.mode == MODE_CLARIFY
    assert res.reason == "insufficient_ready_sources"


def test_after_ab_compare_a_stays_recent_b_active_singular_is_b() -> None:
    state = _sequential_uploads(FILE_A, FILE_B)
    assert str(FILE_A) in state["recent_file_ids"]
    assert state["active_file_ids"] == [str(FILE_B)]
    last = NOW + timedelta(seconds=(UPLOAD_BURST_WINDOW_SECONDS + 1) * 2)
    state = apply_chat_turn(state, [str(FILE_A), str(FILE_B)], now=last)
    assert str(FILE_A) in state["recent_file_ids"]
    assert str(FILE_A) not in state["active_file_ids"]
    assert state["active_file_ids"] == [str(FILE_B)]
    res = resolve_turn_sources("מה הסכום של ההצעה?", state)
    assert res.mode == MODE_PASSTHROUGH
    assert res.file_ids == (str(FILE_B),)


def test_multi_never_unrestricts_workspace() -> None:
    state = _sequential_uploads(FILE_A, FILE_B)
    res = resolve_turn_sources("both files", state)
    arg = restrict_arg_for_resolution(res)
    assert isinstance(arg, list) and arg
    empty = resolve_turn_sources("compare the proposals", empty_source_state())
    assert empty.mode == MODE_CLARIFY
    assert restrict_arg_for_resolution(empty) == []


def test_clarification_language_follows_query() -> None:
    assert "קבצים" in clarification_text("כמה 2 ההצעות יחד")
    assert "last two" in clarification_text("compare the proposals")


# --------------------------------------------------------------------------- #
# Gate 3D / 4A injection                                                      #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_gate3d_sequential_pair_injects_ab(monkeypatch):
    monkeypatch.delenv("BEN_WORKSPACE_CHUNK_RETRIEVAL", raising=False)
    _patch_session(monkeypatch, _workspace_rows())
    state = _sequential_uploads(FILE_A, FILE_B)
    res = resolve_turn_sources("כמה 2 ההצעות יחד", state)
    out = await load_ready_files_context(
        ORG_A,
        WS_A,
        max_chars=12_000,
        user_query="כמה 2 ההצעות יחד",
        restrict_to_file_ids=restrict_arg_for_resolution(res),
        cover_file_ids=list(res.file_ids),
    )
    names = {u["name"] for u in out.used_files}
    assert names == {"A.pdf", "B.pdf"}
    assert "F0-test.txt" not in names
    assert out.block
    assert "[file name=" in out.block


@pytest.mark.asyncio
async def test_gate3d_explicit_two_filenames_injects_ab(monkeypatch):
    monkeypatch.delenv("BEN_WORKSPACE_CHUNK_RETRIEVAL", raising=False)
    _patch_session(monkeypatch, _workspace_rows())
    out = await load_ready_files_context(
        ORG_A,
        WS_A,
        max_chars=12_000,
        user_query="compare A.pdf and B.pdf",
        restrict_to_file_ids=[str(FILE_B)],
    )
    names = {u["name"] for u in out.used_files}
    assert names == {"A.pdf", "B.pdf"}
    assert "F0-test.txt" not in names


@pytest.mark.asyncio
async def test_gate3d_last_two_does_not_dump_workspace(monkeypatch):
    monkeypatch.delenv("BEN_WORKSPACE_CHUNK_RETRIEVAL", raising=False)
    extra = [uuid.uuid4() for _ in range(8)]
    ids = [FILE_F0, FILE_W1, *extra, FILE_A, FILE_B]
    rows = [
        _row(rid=fid, name=f"{i}.pdf", text=f"doc {i} body " * 20, created_at=i)
        for i, fid in enumerate(ids)
    ]
    _patch_session(monkeypatch, rows)
    state = _sequential_uploads(*ids)
    res = resolve_turn_sources("the last two files", state)
    assert res.file_ids == (str(FILE_A), str(FILE_B))
    out = await load_ready_files_context(
        ORG_A,
        WS_A,
        max_chars=12_000,
        user_query="the last two files",
        restrict_to_file_ids=restrict_arg_for_resolution(res),
        cover_file_ids=list(res.file_ids),
    )
    used_ids = {u["id"] for u in out.used_files}
    assert used_ids == {str(FILE_A), str(FILE_B)}
    assert str(FILE_F0) not in used_ids
    assert str(FILE_W1) not in used_ids


@pytest.mark.asyncio
async def test_gate4a_coverage_fill_adds_missing_resolved_file(monkeypatch):
    monkeypatch.setattr(
        "services.workspace_files.service.chunk_retrieval_enabled",
        lambda _ws: True,
    )

    async def fake_prove(session, *, org_id, workspace_id, file_ids):
        return {fid: 3 for fid in file_ids}

    async def fake_search(session, *, org_id, workspace_id, file_ids, tsquery, timeout_s=1.5):
        hits = [
            ChunkHit(
                chunk_id=uuid.uuid4(),
                file_id=FILE_B,
                page_number=1,
                document_chunk_index=0,
                text="proposal B amount 80_000",
                char_count=24,
                rank=1.0,
            )
        ]
        return hits, 1.0, None

    monkeypatch.setattr("services.workspace_files.service.prove_chunk_rows", fake_prove)
    monkeypatch.setattr("services.workspace_files.service.search_chunks_bounded", fake_search)
    _patch_session(monkeypatch, _workspace_rows())
    out = await load_ready_files_context(
        ORG_A,
        WS_A,
        max_chars=12_000,
        user_query="כמה 2 ההצעות יחד",
        restrict_to_file_ids=[str(FILE_A), str(FILE_B)],
        cover_file_ids=[str(FILE_A), str(FILE_B)],
    )
    used_ids = {u["id"] for u in out.used_files}
    assert used_ids == {str(FILE_A), str(FILE_B)}
    assert "proposal B amount" in (out.block or "")
    assert "proposal budget scope" in (out.block or "")
    assert out.retrieval_mode == "mixed"
    assert "F0-test.txt" not in {u["name"] for u in out.used_files}


# --------------------------------------------------------------------------- #
# Chat wiring                                                                 #
# --------------------------------------------------------------------------- #


def _used_ab_ctx(_org, _ws, *, max_chars, user_query=None, restrict_to_file_ids=None, **_k):
    return WorkspaceFilesContext(
        block='<workspace_files>[file name="A.pdf"]A[/file]\n[file name="B.pdf"]B[/file]</workspace_files>',
        count=2,
        chars=20,
        truncated=False,
        used_files=(
            {"id": str(FILE_A), "name": "A.pdf"},
            {"id": str(FILE_B), "name": "B.pdf"},
        ),
    )


@pytest.mark.asyncio
async def test_chat_sequential_singular_restricts_b(monkeypatch):
    import services.chat_service as chat_service

    captured: dict = {}
    _patch_stream_pipeline(monkeypatch, captured)
    state = _sequential_uploads(FILE_A, FILE_B)
    _source_state_chat_patches(monkeypatch, chat_service, captured, state)
    captured_used = {}

    async def fake_ctx(_org, _ws, *, max_chars, user_query=None, restrict_to_file_ids=None, **_k):
        captured["restrict"] = restrict_to_file_ids
        captured["cover"] = _k.get("cover_file_ids")
        return WorkspaceFilesContext(
            block='<workspace_files>[file name="B.pdf"]B[/file]</workspace_files>',
            count=1,
            chars=4,
            truncated=False,
            used_files=({"id": str(FILE_B), "name": "B.pdf"},),
        )

    monkeypatch.setattr(chat_service, "load_ready_files_context", fake_ctx)

    async def fake_turn(*_a, **k):
        captured_used["used"] = k.get("used_file_ids")
        return apply_chat_turn(state, k.get("used_file_ids") or [])

    monkeypatch.setattr(chat_service, "record_standard_chat_turn", fake_turn)

    async for _line in chat_service.stream_chat_response(
        "מה הסכום של ההצעה?",
        "user-1",
        str(ORG_A),
        "free",
        thread_id=uuid.UUID(CHAT_ID),
        provider_id="gpt",
        project_id=WS_A,
    ):
        pass

    assert captured["restrict"] == [str(FILE_B)]
    assert not captured.get("cover")
    assert captured_used["used"] == [str(FILE_B)]
    assert MULTI_SOURCE_GROUNDING_HINT not in captured.get("message", "")


@pytest.mark.asyncio
async def test_chat_sequential_pair_restricts_ab_and_hints(monkeypatch):
    import services.chat_service as chat_service

    captured: dict = {}
    _patch_stream_pipeline(monkeypatch, captured)
    state = _sequential_uploads(FILE_A, FILE_B)
    _source_state_chat_patches(monkeypatch, chat_service, captured, state)

    async def fake_ctx(_org, _ws, *, max_chars, user_query=None, restrict_to_file_ids=None, **_k):
        captured["restrict"] = restrict_to_file_ids
        captured["cover"] = _k.get("cover_file_ids")
        return _used_ab_ctx(_org, _ws, max_chars=max_chars)

    monkeypatch.setattr(chat_service, "load_ready_files_context", fake_ctx)

    recorded = {}

    async def fake_turn(*_a, **k):
        recorded["used"] = k.get("used_file_ids")
        return apply_chat_turn(state, k.get("used_file_ids") or [])

    monkeypatch.setattr(chat_service, "record_standard_chat_turn", fake_turn)

    async for _line in chat_service.stream_chat_response(
        "כמה 2 ההצעות יחד",
        "user-1",
        str(ORG_A),
        "free",
        thread_id=uuid.UUID(CHAT_ID),
        provider_id="gpt",
        project_id=WS_A,
    ):
        pass

    assert set(captured["restrict"] or []) == {str(FILE_A), str(FILE_B)}
    assert set(captured["cover"] or []) == {str(FILE_A), str(FILE_B)}
    assert set(recorded["used"]) == {str(FILE_A), str(FILE_B)}
    assert MULTI_SOURCE_GROUNDING_HINT in captured.get("message", "")


@pytest.mark.asyncio
async def test_chat_ambiguous_plural_clarifies_without_workspace_load(monkeypatch):
    import services.chat_service as chat_service

    captured: dict = {}
    _patch_stream_pipeline(monkeypatch, captured)
    ids = [uuid.uuid4() for _ in range(5)]
    state = _sequential_uploads(*ids)
    _source_state_chat_patches(monkeypatch, chat_service, captured, state)
    captured["loaded"] = False

    async def boom(_org, _ws, **_k):
        captured["loaded"] = True
        raise AssertionError("ambiguous multi-source must not load workspace files")

    monkeypatch.setattr(chat_service, "load_ready_files_context", boom)

    events = []
    async for line in chat_service.stream_chat_response(
        "compare the proposals",
        "user-1",
        str(ORG_A),
        "free",
        thread_id=uuid.UUID(CHAT_ID),
        provider_id="gpt",
        project_id=WS_A,
    ):
        events.append(line)

    assert captured["loaded"] is False
    body = "".join(events)
    assert "last two" in body
    done = [json.loads(e) for e in events if '"type": "done"' in e or '"type":"done"' in e]
    assert done
    assert done[-1].get("workspace_files_used") in ([], None)
    assert captured.get("message") is None
    assert captured.get("record_calls") == []


@pytest.mark.asyncio
async def test_chat_pair_with_one_ready_after_load_clarifies(monkeypatch):
    import services.chat_service as chat_service

    captured: dict = {}
    _patch_stream_pipeline(monkeypatch, captured)
    state = _sequential_uploads(FILE_A, FILE_B)
    _source_state_chat_patches(monkeypatch, chat_service, captured, state)

    async def fake_ctx(_org, _ws, *, max_chars, user_query=None, restrict_to_file_ids=None, **_k):
        captured["restrict"] = restrict_to_file_ids
        return WorkspaceFilesContext(
            block='<workspace_files>[file name="B.pdf"]B[/file]</workspace_files>',
            count=1,
            chars=4,
            truncated=False,
            used_files=({"id": str(FILE_B), "name": "B.pdf"},),
        )

    monkeypatch.setattr(chat_service, "load_ready_files_context", fake_ctx)

    events = []
    async for line in chat_service.stream_chat_response(
        "both files",
        "user-1",
        str(ORG_A),
        "free",
        thread_id=uuid.UUID(CHAT_ID),
        provider_id="gpt",
        project_id=WS_A,
    ):
        events.append(line)

    assert set(captured["restrict"] or []) == {str(FILE_A), str(FILE_B)}
    body = "".join(events)
    assert "last two" in body or "קבצים" in body
    done = [json.loads(e) for e in events if '"done"' in e]
    assert done[-1].get("workspace_files_used") in ([], None)
    assert captured.get("record_calls") == []


@pytest.mark.asyncio
async def test_clarification_does_not_age_active_source(monkeypatch):
    import services.chat_service as chat_service

    captured: dict = {}
    _patch_stream_pipeline(monkeypatch, captured)
    seed = _sequential_uploads(FILE_A, FILE_B)
    used_at = NOW + timedelta(seconds=(UPLOAD_BURST_WINDOW_SECONDS + 1) * 2)
    seed = apply_chat_turn(seed, [str(FILE_B)], now=used_at)
    unused_before = seed["unused_turns"]
    last_used_before = seed["last_used_at"]

    ids = [uuid.uuid4() for _ in range(5)]
    crowded = _sequential_uploads(*ids)
    crowded["unused_turns"] = unused_before
    crowded["last_used_at"] = last_used_before
    crowded["active_file_ids"] = [str(FILE_B)]
    crowded["recent_file_ids"] = [str(FILE_A), *[str(i) for i in ids[:-1]]]
    crowded["pending_file_ids"] = []
    _source_state_chat_patches(monkeypatch, chat_service, captured, crowded)

    async def boom(_org, _ws, **_k):
        raise AssertionError("ambiguous multi-source must not load workspace files")

    monkeypatch.setattr(chat_service, "load_ready_files_context", boom)

    async def run(query: str) -> list[str]:
        events = []
        async for line in chat_service.stream_chat_response(
            query,
            "user-1",
            str(ORG_A),
            "free",
            thread_id=uuid.UUID(CHAT_ID),
            provider_id="gpt",
            project_id=WS_A,
        ):
            events.append(line)
        return events

    first = await run("compare the proposals")
    assert captured.get("message") is None
    assert "last two" in "".join(first)
    assert captured.get("record_calls") == []
    assert crowded["active_file_ids"] == [str(FILE_B)]
    assert str(FILE_A) in crowded["recent_file_ids"]
    assert crowded["unused_turns"] == unused_before
    assert crowded["last_used_at"] == last_used_before

    second = await run("compare the proposals")
    assert captured.get("message") is None
    assert "last two" in "".join(second)
    assert captured.get("record_calls") == []
    assert crowded["active_file_ids"] == [str(FILE_B)]
    assert crowded["unused_turns"] == unused_before
    assert crowded["last_used_at"] == last_used_before

    captured["record_calls"] = []

    async def singular_ctx(_org, _ws, *, max_chars, user_query=None, restrict_to_file_ids=None, **_k):
        captured["restrict"] = restrict_to_file_ids
        return WorkspaceFilesContext(
            block='<workspace_files>[file name="B.pdf"]B[/file]</workspace_files>',
            count=1,
            chars=4,
            truncated=False,
            used_files=({"id": str(FILE_B), "name": "B.pdf"},),
        )

    monkeypatch.setattr(chat_service, "load_ready_files_context", singular_ctx)
    await run("מה הסכום של ההצעה?")
    assert captured["restrict"] == [str(FILE_B)]
    assert captured.get("message")
    assert MULTI_SOURCE_GROUNDING_HINT not in captured["message"]


def test_filename_mentions_and_incomplete_named_set() -> None:
    assert filename_mentions_in_query("compare A.pdf and B.pdf") == ("a.pdf", "b.pdf")
    assert filename_mentions_in_query("summarize A.pdf") == ("a.pdf",)
    assert explicit_named_set_incomplete(
        "compare A.pdf and B.pdf",
        named_ids=(str(FILE_A), str(FILE_B)),
        used_ids=(str(FILE_A),),
    )
    assert explicit_named_set_incomplete(
        "compare A.pdf and B.pdf",
        named_ids=(str(FILE_A),),
        used_ids=(str(FILE_A),),
    )
    assert not explicit_named_set_incomplete(
        "compare A.pdf and B.pdf",
        named_ids=(str(FILE_A), str(FILE_B)),
        used_ids=(str(FILE_A), str(FILE_B)),
    )
    assert not explicit_named_set_incomplete(
        "summarize A.pdf",
        named_ids=(str(FILE_A),),
        used_ids=(str(FILE_A),),
    )


@pytest.mark.asyncio
async def test_explicit_ab_both_ready_grounds_both(monkeypatch):
    monkeypatch.delenv("BEN_WORKSPACE_CHUNK_RETRIEVAL", raising=False)
    _patch_session(monkeypatch, _workspace_rows())
    out = await load_ready_files_context(
        ORG_A,
        WS_A,
        max_chars=12_000,
        user_query="compare A.pdf and B.pdf",
        restrict_to_file_ids=[str(FILE_B)],
    )
    assert set(out.explicit_named_ids) == {str(FILE_A), str(FILE_B)}
    assert {u["id"] for u in out.used_files} == {str(FILE_A), str(FILE_B)}

    import services.chat_service as chat_service

    captured: dict = {}
    _patch_stream_pipeline(monkeypatch, captured)
    state = _sequential_uploads(FILE_A, FILE_B)
    _source_state_chat_patches(monkeypatch, chat_service, captured, state)

    async def fake_ctx(_org, _ws, *, max_chars, user_query=None, restrict_to_file_ids=None, **_k):
        captured["restrict"] = restrict_to_file_ids
        return WorkspaceFilesContext(
            block='<workspace_files>[file name="A.pdf"]A[/file]\n[file name="B.pdf"]B[/file]</workspace_files>',
            count=2,
            chars=20,
            truncated=False,
            used_files=(
                {"id": str(FILE_A), "name": "A.pdf"},
                {"id": str(FILE_B), "name": "B.pdf"},
            ),
            explicit_named_ids=(str(FILE_A), str(FILE_B)),
        )

    monkeypatch.setattr(chat_service, "load_ready_files_context", fake_ctx)
    recorded = {}

    async def fake_turn(*_a, **k):
        recorded["used"] = k.get("used_file_ids")
        nxt = apply_chat_turn(
            state,
            k.get("used_file_ids") or [],
            now=NOW + timedelta(seconds=(UPLOAD_BURST_WINDOW_SECONDS + 1) * 2),
        )
        state.clear()
        state.update(nxt)
        return state

    monkeypatch.setattr(chat_service, "record_standard_chat_turn", fake_turn)

    async for _line in chat_service.stream_chat_response(
        "compare A.pdf and B.pdf",
        "user-1",
        str(ORG_A),
        "free",
        thread_id=uuid.UUID(CHAT_ID),
        provider_id="gpt",
        project_id=WS_A,
    ):
        pass

    assert captured.get("message")  # provider called once
    assert MULTI_SOURCE_GROUNDING_HINT in captured["message"]
    assert set(recorded["used"]) == {str(FILE_A), str(FILE_B)}
    assert state["active_file_ids"] == [str(FILE_B)]
    assert str(FILE_A) in state["recent_file_ids"]
    assert str(FILE_A) not in state["active_file_ids"]


@pytest.mark.asyncio
async def test_explicit_ab_b_not_ready_fails_closed(monkeypatch):
    monkeypatch.delenv("BEN_WORKSPACE_CHUNK_RETRIEVAL", raising=False)
    rows = [
        _row(rid=FILE_A, name="A.pdf", text="proposal budget scope " * 20, created_at=3),
        _row(rid=FILE_B, name="B.pdf", text="compare annex", status="processing", created_at=4),
        _row(rid=FILE_F0, name="F0-test.txt", text="f0 workspace filler " * 20, created_at=1),
    ]
    _patch_session(monkeypatch, rows)
    out = await load_ready_files_context(
        ORG_A,
        WS_A,
        max_chars=12_000,
        user_query="compare A.pdf and B.pdf",
        restrict_to_file_ids=[str(FILE_A)],
    )
    assert set(out.explicit_named_ids) == {str(FILE_A), str(FILE_B)}
    assert {u["id"] for u in out.used_files} == {str(FILE_A)}

    import services.chat_service as chat_service

    captured: dict = {}
    _patch_stream_pipeline(monkeypatch, captured)
    state = _sequential_uploads(FILE_A, FILE_B)
    _source_state_chat_patches(monkeypatch, chat_service, captured, state)

    async def fake_ctx(_org, _ws, **_k):
        return out

    monkeypatch.setattr(chat_service, "load_ready_files_context", fake_ctx)
    events = []
    async for line in chat_service.stream_chat_response(
        "compare A.pdf and B.pdf",
        "user-1",
        str(ORG_A),
        "free",
        thread_id=uuid.UUID(CHAT_ID),
        provider_id="gpt",
        project_id=WS_A,
    ):
        events.append(line)

    assert captured.get("message") is None
    assert "last two" in "".join(events)
    assert captured.get("record_calls") == []
    done = [json.loads(e) for e in events if '"done"' in e]
    assert done[-1].get("workspace_files_used") in ([], None)


@pytest.mark.asyncio
async def test_explicit_ab_b_missing_fails_closed(monkeypatch):
    monkeypatch.delenv("BEN_WORKSPACE_CHUNK_RETRIEVAL", raising=False)
    rows = [
        _row(rid=FILE_A, name="A.pdf", text="proposal budget scope " * 20, created_at=3),
        _row(rid=FILE_F0, name="F0-test.txt", text="f0 workspace filler " * 20, created_at=1),
    ]
    _patch_session(monkeypatch, rows)
    out = await load_ready_files_context(
        ORG_A,
        WS_A,
        max_chars=12_000,
        user_query="compare A.pdf and B.pdf",
        restrict_to_file_ids=[str(FILE_A)],
    )
    assert out.explicit_named_ids == (str(FILE_A),)
    assert {u["id"] for u in out.used_files} == {str(FILE_A)}

    import services.chat_service as chat_service

    captured: dict = {}
    _patch_stream_pipeline(monkeypatch, captured)
    state = _sequential_uploads(FILE_A)
    _source_state_chat_patches(monkeypatch, chat_service, captured, state)

    async def fake_ctx(*_a, **_k):
        return out

    monkeypatch.setattr(chat_service, "load_ready_files_context", fake_ctx)
    events = []
    async for line in chat_service.stream_chat_response(
        "compare A.pdf and B.pdf",
        "user-1",
        str(ORG_A),
        "free",
        thread_id=uuid.UUID(CHAT_ID),
        provider_id="gpt",
        project_id=WS_A,
    ):
        events.append(line)

    assert captured.get("message") is None
    assert captured.get("record_calls") == []
    assert "F0-test.txt" not in "".join(events)
    done = [json.loads(e) for e in events if '"done"' in e]
    assert done[-1].get("workspace_files_used") in ([], None)


@pytest.mark.asyncio
async def test_explicit_only_a_stays_singular(monkeypatch):
    monkeypatch.delenv("BEN_WORKSPACE_CHUNK_RETRIEVAL", raising=False)
    _patch_session(monkeypatch, _workspace_rows())
    out = await load_ready_files_context(
        ORG_A,
        WS_A,
        max_chars=12_000,
        user_query="summarize A.pdf",
        restrict_to_file_ids=[str(FILE_B)],
    )
    assert out.explicit_named_ids == (str(FILE_A),)
    assert {u["name"] for u in out.used_files} == {"A.pdf"}

    import services.chat_service as chat_service

    captured: dict = {}
    _patch_stream_pipeline(monkeypatch, captured)
    state = _sequential_uploads(FILE_A, FILE_B)
    _source_state_chat_patches(monkeypatch, chat_service, captured, state)

    async def fake_ctx(*_a, **_k):
        return out

    monkeypatch.setattr(chat_service, "load_ready_files_context", fake_ctx)
    async for _line in chat_service.stream_chat_response(
        "summarize A.pdf",
        "user-1",
        str(ORG_A),
        "free",
        thread_id=uuid.UUID(CHAT_ID),
        provider_id="gpt",
        project_id=WS_A,
    ):
        pass

    assert captured.get("message")
    assert MULTI_SOURCE_GROUNDING_HINT not in captured["message"]
    assert captured.get("record_calls")


# --------------------------------------------------------------------------- #
# Unrelated paths unchanged                                                   #
# --------------------------------------------------------------------------- #


def test_unrelated_subsystems_untouched() -> None:
    chat = (ROOT / "services" / "chat_service.py").read_text(encoding="utf-8")
    assert "if project_id is not None and vision_user_content is None" in chat
    assert "user_turn_focus_query_source(message)" in chat
    assert "if expert_opinion:" in chat
    assert "build_rolling_stream_prompt" in chat
    ir = (ROOT / "services" / "workspace_files" / "initial_read.py").read_text(encoding="utf-8")
    assert "resolve_turn_sources" not in ir
    policy = (ROOT / "services" / "workspace_files" / "source_policy.py").read_text(encoding="utf-8")
    assert "ACTIVE_SOURCE_MAX_UNUSED_TURNS = 2" in policy
    assert "UPLOAD_BURST_WINDOW_SECONDS = 120" in policy
