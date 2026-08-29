"""Gate 3D — selection before context budgeting.

Proves READY workspace files are ranked from the user query *before* the
global/per-file character budgets are applied. No embeddings, no chunk
retrieval, no provider calls. Isolation boundaries stay identical to Gate 3C.
"""
from __future__ import annotations

import json
import sys
import types
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import services.chat_service as chat_service
from services.workspace_files.file_resolver import (
    EligibleFile,
    apply_context_budget,
    rank_eligible_files,
    score_file,
)
from services.workspace_files.service import (
    WorkspaceFilesContext,
    load_ready_files_context,
)

ORG_A = uuid.UUID("11111111-1111-1111-1111-111111111111")
ORG_B = uuid.UUID("22222222-2222-2222-2222-222222222222")
WS_A = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
WS_B = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
ID_OLD = uuid.UUID("00000000-0000-0000-0000-000000000001")
ID_NEW = uuid.UUID("00000000-0000-0000-0000-000000000099")


def _row(
    *,
    org_id=ORG_A,
    workspace_id=WS_A,
    status="ready",
    text="content",
    name="doc.pdf",
    created_at=0,
    rid=None,
):
    return types.SimpleNamespace(
        org_id=org_id,
        workspace_id=workspace_id,
        status=status,
        extracted_text=text,
        display_name=name,
        original_filename=name,
        created_at=created_at,
        id=rid or uuid.uuid4(),
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


def _eligible(name, text, created_at, rid):
    return EligibleFile(
        id=rid,
        created_at=created_at,
        display_name=name,
        original_filename=name,
        text=text,
    )


# --------------------------------------------------------------------------- #
# 1. Original starvation reproduction                                         #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_relevant_canary_survives_older_files_consuming_global_budget(monkeypatch):
    """6 × 2000-char older files would exhaust a 12_000 budget before the canary
    if iteration were created_at ASC. Named files are an allow-list, so the
    canary is the only injected file.
    """
    older = [
        _row(text="O" * 2000, name=f"old_{i}.txt", created_at=i, rid=uuid.UUID(int=i + 1))
        for i in range(1, 7)
    ]
    canary = _row(
        text="CANARY-SECRET-TOKEN",
        name="ben_canary.txt",
        created_at=99,
        rid=ID_NEW,
    )
    _patch_session(monkeypatch, older + [canary])

    out = await load_ready_files_context(
        ORG_A,
        WS_A,
        max_chars=12_000,
        per_file_max=2_000,
        user_query="Read ben_canary.txt",
    )
    assert "CANARY-SECRET-TOKEN" in out.block
    assert '[file name="ben_canary.txt"]' in out.block
    assert "old_1.txt" not in out.block
    assert out.block.count("[file name=") == 1
    assert out.chars <= 12_000
    assert out.count == 1


# --------------------------------------------------------------------------- #
# 2. Explicit filename                                                        #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_explicit_filename_ranks_ahead_of_unrelated_files(monkeypatch):
    unrelated = _row(text="payroll numbers", name="budget.xlsx.txt", created_at=50, rid=ID_OLD)
    named = _row(text="CANARY-BODY", name="ben_canary.txt", created_at=1, rid=ID_NEW)
    _patch_session(monkeypatch, [unrelated, named])

    out = await load_ready_files_context(
        ORG_A,
        WS_A,
        max_chars=11,
        per_file_max=11,
        user_query="Read ben_canary.txt",
    )
    assert out.block.count("[file name=") == 1
    assert "CANARY-BODY" in out.block
    assert "payroll" not in out.block
    assert out.count == 1


def test_explicit_filename_score_outranks_body_overlap():
    named = _eligible("ben_canary.txt", "unrelated body", created_at=1, rid=ID_NEW)
    overlapping = _eligible("notes.txt", "please read the canary notes", created_at=99, rid=ID_OLD)
    ranked = rank_eligible_files([overlapping, named], "Read ben_canary.txt")
    assert ranked[0].file.display_name == "ben_canary.txt"
    assert ranked[0].explicit_name is True
    assert score_file(named, "Read ben_canary.txt").score > score_file(
        overlapping, "Read ben_canary.txt"
    ).score


# --------------------------------------------------------------------------- #
# 3. Per-file cap                                                             #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_per_file_cap_prevents_one_file_consuming_global_budget(monkeypatch):
    _patch_session(
        monkeypatch,
        [_row(text="X" * 10_000, name="huge.txt", created_at=1)],
    )
    out = await load_ready_files_context(
        ORG_A, WS_A, max_chars=12_000, per_file_max=2_000, user_query=""
    )
    assert out.chars == 2_000
    assert out.count == 1
    assert out.truncated is True
    assert ("X" * 2_000) in out.block
    assert ("X" * 2_001) not in out.block


# --------------------------------------------------------------------------- #
# 4. Global cap                                                               #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_global_cap_never_exceeded(monkeypatch):
    rows = [
        _row(text="A" * 2_000, name="a.txt", created_at=1, rid=uuid.UUID(int=1)),
        _row(text="B" * 2_000, name="b.txt", created_at=2, rid=uuid.UUID(int=2)),
        _row(text="C" * 2_000, name="c.txt", created_at=3, rid=uuid.UUID(int=3)),
    ]
    _patch_session(monkeypatch, rows)
    out = await load_ready_files_context(
        ORG_A, WS_A, max_chars=5_000, per_file_max=2_000, user_query=""
    )
    assert out.chars == 5_000
    assert out.chars <= 5_000
    assert out.truncated is True
    assert out.count == 3  # 2000 + 2000 + 1000


# --------------------------------------------------------------------------- #
# 5. Exact-budget boundary                                                    #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_exact_budget_includes_all_without_false_truncation(monkeypatch):
    rows = [
        _row(text="A" * 100, name="a.txt", created_at=1, rid=uuid.UUID(int=1)),
        _row(text="B" * 100, name="b.txt", created_at=2, rid=uuid.UUID(int=2)),
    ]
    _patch_session(monkeypatch, rows)
    out = await load_ready_files_context(
        ORG_A, WS_A, max_chars=200, per_file_max=10_000, user_query=""
    )
    assert out.chars == 200
    assert out.count == 2
    assert out.truncated is False
    assert ("A" * 100) in out.block
    assert ("B" * 100) in out.block


@pytest.mark.asyncio
async def test_exact_budget_with_leftover_eligible_file_is_truncated(monkeypatch):
    rows = [
        _row(text="A" * 100, name="a.txt", created_at=1, rid=uuid.UUID(int=1)),
        _row(text="B" * 100, name="b.txt", created_at=2, rid=uuid.UUID(int=2)),
        _row(text="C" * 100, name="c.txt", created_at=3, rid=uuid.UUID(int=3)),
    ]
    _patch_session(monkeypatch, rows)
    out = await load_ready_files_context(
        ORG_A, WS_A, max_chars=200, per_file_max=10_000, user_query=""
    )
    assert out.chars == 200
    assert out.truncated is True
    assert out.count == 2


# --------------------------------------------------------------------------- #
# 6. Partial inclusion / clip markers                                         #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_partial_inclusion_clips_last_selected_file(monkeypatch):
    rows = [
        _row(text="HEAD" + ("T" * 96), name="keep.txt", created_at=2, rid=uuid.UUID(int=2)),
        _row(text="TAIL" + ("Z" * 96), name="clip.txt", created_at=1, rid=uuid.UUID(int=1)),
    ]
    _patch_session(monkeypatch, rows)
    out = await load_ready_files_context(
        ORG_A, WS_A, max_chars=120, per_file_max=10_000, user_query=""
    )
    # Newer keep.txt (100) selected first; clip.txt contributes 20 chars.
    assert out.truncated is True
    assert out.chars == 120
    assert "HEAD" in out.block
    assert "TAIL" in out.block
    assert ("Z" * 96) not in out.block
    assert '[file name="keep.txt"]' in out.block
    assert '[file name="clip.txt"]' in out.block
    assert out.block.endswith("</workspace_files>")


# --------------------------------------------------------------------------- #
# 7. Determinism                                                              #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_identical_inputs_produce_identical_selection(monkeypatch):
    rows = [
        _row(text="alpha body", name="alpha.txt", created_at=1, rid=uuid.UUID(int=1)),
        _row(text="beta canary", name="beta.txt", created_at=2, rid=uuid.UUID(int=2)),
        _row(text="gamma", name="gamma.txt", created_at=3, rid=uuid.UUID(int=3)),
    ]
    _patch_session(monkeypatch, rows)
    kwargs = dict(max_chars=50, per_file_max=20, user_query="beta canary please")
    first = await load_ready_files_context(ORG_A, WS_A, **kwargs)
    second = await load_ready_files_context(ORG_A, WS_A, **kwargs)
    assert first.block == second.block
    assert first.count == second.count
    assert first.chars == second.chars
    assert first.truncated == second.truncated


def test_rank_is_stable_for_shuffled_equal_inputs():
    files = [
        _eligible("a.txt", "same", 1, uuid.UUID(int=1)),
        _eligible("b.txt", "same", 1, uuid.UUID(int=2)),
        _eligible("c.txt", "same", 1, uuid.UUID(int=3)),
    ]
    left = [r.file.id for r in rank_eligible_files(files, "")]
    right = [r.file.id for r in rank_eligible_files(list(reversed(files)), "")]
    assert left == right


# --------------------------------------------------------------------------- #
# 8. Eligibility isolation                                                    #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_ranking_cannot_admit_other_org_even_when_named(monkeypatch):
    foreign = _row(
        org_id=ORG_B,
        text="FOREIGN-SECRET",
        name="ben_canary.txt",
        created_at=99,
    )
    local = _row(text="local only", name="notes.txt", created_at=1)
    _patch_session(monkeypatch, [foreign, local])
    out = await load_ready_files_context(
        ORG_A, WS_A, max_chars=10_000, user_query="Read ben_canary.txt"
    )
    assert "FOREIGN-SECRET" not in out.block
    assert "ben_canary.txt" not in out.block
    assert "local only" in out.block


@pytest.mark.asyncio
async def test_ranking_cannot_admit_other_workspace_even_when_named(monkeypatch):
    foreign = _row(
        workspace_id=WS_B,
        text="OTHER-WS-SECRET",
        name="ben_canary.txt",
        created_at=99,
    )
    _patch_session(monkeypatch, [foreign])
    out = await load_ready_files_context(
        ORG_A, WS_A, max_chars=10_000, user_query="Read ben_canary.txt"
    )
    assert out.count == 0
    assert "OTHER-WS-SECRET" not in out.block


@pytest.mark.asyncio
async def test_non_ready_named_file_is_not_selected(monkeypatch):
    _patch_session(
        monkeypatch,
        [_row(status="queued", text="NOT-READY", name="ben_canary.txt", created_at=1)],
    )
    out = await load_ready_files_context(
        ORG_A, WS_A, max_chars=10_000, user_query="Read ben_canary.txt"
    )
    assert out.count == 0
    assert "NOT-READY" not in out.block


# --------------------------------------------------------------------------- #
# user_query propagation into chat injection                                  #
# --------------------------------------------------------------------------- #

def _patch_stream_pipeline(monkeypatch, captured):
    async def fake_stream(message, tenant_id, tier, *, provider_id=None, model_override=None, system=None):
        captured["message"] = message
        yield ("ok", "model-x", "openai")

    async def _aid(*a, **k):
        return None

    monkeypatch.setattr("services.chat_service.resolve_thread_id", lambda *a, **k: _aid())
    monkeypatch.setattr("services.chat_service.is_project_setup_thread", lambda _tid: False)

    async def _ctx(_o, _t, m):
        return m

    async def _knowledge(_m, payload):
        return payload

    monkeypatch.setattr("services.chat_service.build_chat_message_with_thread_context", _ctx)
    monkeypatch.setattr("services.chat_service.inject_knowledge_few_shot", _knowledge)
    monkeypatch.setattr("services.chat_service.apply_language_context", lambda msg, _lang: msg)
    monkeypatch.setattr("services.chat_service.route_request_stream", fake_stream)
    monkeypatch.setattr("services.chat_service.persist_chat_exchange_sqlite", lambda *a, **k: (1, 2))
    monkeypatch.setattr("services.chat_service._schedule_chat_persist", lambda *_a, **_k: None)


@pytest.mark.asyncio
async def test_stream_propagates_user_query_into_file_context(monkeypatch):
    captured: dict = {}
    _patch_stream_pipeline(monkeypatch, captured)

    async def fake_ctx(_org, _ws, *, max_chars, user_query=None, **_k):
        captured["user_query"] = user_query
        captured["max_chars"] = max_chars
        return WorkspaceFilesContext(
            block='<workspace_files>\n[file name="ben_canary.txt"]\nCANARY\n[/file]\n</workspace_files>',
            count=1,
            chars=6,
            truncated=False,
        )

    monkeypatch.setattr("services.chat_service.load_ready_files_context", fake_ctx)

    events = []
    async for line in chat_service.stream_chat_response(
        "Read ben_canary.txt",
        "user-1",
        str(ORG_A),
        "free",
        thread_id=uuid.uuid4(),
        provider_id="gpt",
        project_id=WS_A,
    ):
        events.append(json.loads(line))

    assert captured["user_query"] == "Read ben_canary.txt"
    assert "CANARY" in captured["message"]
    done = next(e for e in events if e["type"] == "done")
    assert done["workspace_files_injected"] is True


def test_budget_does_not_run_before_selection():
    """Resolver ranks the full eligible set; budget only sees that order."""
    files = [
        _eligible("old_1.txt", "O" * 2000, 1, uuid.UUID(int=1)),
        _eligible("old_2.txt", "O" * 2000, 2, uuid.UUID(int=2)),
        _eligible("ben_canary.txt", "CANARY", 3, uuid.UUID(int=3)),
    ]
    ranked = rank_eligible_files(files, "Read ben_canary.txt")
    assert ranked[0].file.display_name == "ben_canary.txt"
    budgeted, truncated = apply_context_budget(
        ranked,
        max_chars=2000,
        per_file_max=2000,
        sanitize_name=lambda n: n,
    )
    assert budgeted[0].name == "ben_canary.txt"
    assert budgeted[0].text == "CANARY"
    assert truncated is True  # older files omitted after the selected canary
