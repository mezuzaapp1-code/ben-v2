"""Gate 4A — local Postgres chunk FTS for chat evidence.

Unit tests always run. DB tests SKIP when Postgres/asyncpg or the chunk
schema is unavailable. Flag default remains OFF so Gate 3D tests stay green.
"""
from __future__ import annotations

import json
import os
import sys
import types
import uuid
from pathlib import Path

import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://ben:ben@127.0.0.1:5432/ben")
os.environ.setdefault("BEN_TEST_PG_DSN", "postgresql://ben:ben@127.0.0.1:5432/ben")

import services.chat_service as chat_service
from services.workspace_files.chunk_retriever import (
    MAX_CHUNKS_CONSIDERED,
    MAX_CHUNKS_PER_FILE,
    MAX_CHUNKS_SELECTED,
    MAX_EVIDENCE_CHARS,
    ChunkHit,
    apply_chunk_budget,
    build_or_tsquery,
    chunk_retrieval_enabled,
    claimed_indexed_ids,
    normalize_query_tokens,
    qualify_indexed_ids,
    ReadyFile,
)
from services.workspace_files.file_resolver import (
    EligibleFile,
    apply_context_budget,
    rank_eligible_files,
)
from services.workspace_files.service import (
    WorkspaceFilesContext,
    load_ready_files_context,
)

try:
    import asyncpg
except Exception:  # pragma: no cover
    asyncpg = None

_DSN = os.getenv("BEN_TEST_PG_DSN") or "postgresql://ben:ben@127.0.0.1:5432/ben"

ORG_A = uuid.UUID("11111111-1111-1111-1111-111111111111")
ORG_B = uuid.UUID("22222222-2222-2222-2222-222222222222")
WS_A = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
WS_B = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")

EARLY = "EARLY_MARKER_GATE4_ALPHA"
MIDDLE = "MIDDLE_MARKER_GATE4_BRAVO"
LATE = "LATE_MARKER_GATE4_CHARLIE"
CLAUSE = "TERMINATION_RIGHTS_CLAUSE_UNIQUE"
HEBREW_MARK = "סיוםזכויותייחודי"
ABSENT = "zxqv9nonesuchgate4xyz"
OTHER_WS = "OTHER_WORKSPACE_ONLY_MARKER_ZULU"

NATURAL_TERMINATION = (
    "What does this contract say about termination rights and how much "
    "notice must the supplier give us?"
)
NATURAL_LATE = (
    "What do the signatures and annexes say about the late closing marker "
    f"{LATE} in this agreement?"
)
NATURAL_HEBREW = "מה החוזה אומר על זכויות סיום ומה תקופת ההודעה"


# --------------------------------------------------------------------------- #
# Unit — query normalization, budgets, flag                                   #
# --------------------------------------------------------------------------- #

def test_normalize_natural_question_drops_stopwords_and_ors():
    tokens = normalize_query_tokens(NATURAL_TERMINATION)
    assert "termination" in tokens
    assert "rights" in tokens
    assert "notice" in tokens
    assert "supplier" in tokens
    assert "what" not in tokens
    assert "does" not in tokens
    assert "this" not in tokens
    assert "the" not in tokens
    assert "and" not in tokens
    q = build_or_tsquery(tokens)
    assert q is not None
    assert " & " not in q
    assert "termination" in q
    assert " | " in q


def test_normalize_hebrew_drops_function_words():
    tokens = normalize_query_tokens(NATURAL_HEBREW)
    assert "זכויות" in tokens
    assert "סיום" in tokens
    assert "החוזה" in tokens
    assert "מה" not in tokens
    assert "על" not in tokens
    q = build_or_tsquery(tokens)
    assert q is not None
    assert "זכויות" in q
    assert " | " in q


def test_normalize_zero_tokens_skips_fts():
    assert normalize_query_tokens("What does this file show?") == []
    assert build_or_tsquery([]) is None


def test_tsquery_rejects_operator_injection():
    q = build_or_tsquery(["termination", "rights & drop", "ok-token", "notice"])
    assert q == "termination | notice"


def test_token_cap_is_twelve():
    words = " ".join(f"token{i:02d}" for i in range(20))
    tokens = normalize_query_tokens(words)
    assert len(tokens) == 12
    assert tokens[0] == "token00"
    assert tokens[-1] == "token11"


def test_flag_defaults_off(monkeypatch):
    monkeypatch.delenv("BEN_WORKSPACE_CHUNK_RETRIEVAL", raising=False)
    monkeypatch.delenv("BEN_WORKSPACE_CHUNK_RETRIEVAL_WORKSPACE_IDS", raising=False)
    assert chunk_retrieval_enabled(WS_A) is False


def test_flag_on_allowlist(monkeypatch):
    monkeypatch.setenv("BEN_WORKSPACE_CHUNK_RETRIEVAL", "on")
    monkeypatch.setenv("BEN_WORKSPACE_CHUNK_RETRIEVAL_WORKSPACE_IDS", str(WS_A))
    assert chunk_retrieval_enabled(WS_A) is True
    assert chunk_retrieval_enabled(WS_B) is False


def test_chunk_budget_enforces_hard_caps():
    hits = []
    for i in range(200):
        hits.append(
            ChunkHit(
                chunk_id=uuid.UUID(int=i + 1),
                file_id=uuid.UUID(int=(i % 10) + 1),
                page_number=i + 1,
                document_chunk_index=i,
                text=("liability " * 80) + str(i),
                char_count=0,
                rank=1.0 - (i * 0.001),
            )
        )
    selected = apply_chunk_budget(hits)
    assert len(selected) <= MAX_CHUNKS_SELECTED
    per_file: dict[str, int] = {}
    total = 0
    seen = set()
    for hit in selected:
        assert hit.chunk_id not in seen
        seen.add(hit.chunk_id)
        key = str(hit.file_id)
        per_file[key] = per_file.get(key, 0) + 1
        total += len(hit.text)
        assert per_file[key] <= MAX_CHUNKS_PER_FILE
    assert total <= MAX_EVIDENCE_CHARS


def test_duplicate_chunk_ids_are_dropped():
    cid = uuid.uuid4()
    fid = uuid.uuid4()
    hit = ChunkHit(cid, fid, 3, 0, "same chunk body unique", 20, 0.5)
    selected = apply_chunk_budget([hit, hit, hit])
    assert len(selected) == 1
    assert selected[0].chunk_id == cid


def test_mismatch_when_indexed_but_zero_rows():
    claimed, mismatch = claimed_indexed_ids(
        [
            ReadyFile(
                id=uuid.UUID(int=1),
                created_at=1,
                display_name="a.pdf",
                original_filename="a.pdf",
                text="prefix",
                index_status="indexed",
                indexed_chunk_count=4,
                extraction_status="complete",
                extraction_truncated=False,
            )
        ]
    )
    qualified, mismatched = qualify_indexed_ids(claimed, mismatch, {})
    assert qualified == []
    assert mismatched == [uuid.UUID(int=1)]


# --------------------------------------------------------------------------- #
# Unit — flag OFF preserves Gate 3D injection                                 #
# --------------------------------------------------------------------------- #

def _row(
    *,
    org_id=ORG_A,
    workspace_id=WS_A,
    status="ready",
    text="content",
    name="doc.pdf",
    created_at=0,
    rid=None,
    index_status="not_indexed",
    indexed_chunk_count=None,
    extraction_status="pending",
    extraction_truncated=False,
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
        index_status=index_status,
        indexed_chunk_count=indexed_chunk_count,
        extraction_status=extraction_status,
        extraction_truncated=extraction_truncated,
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


@pytest.mark.asyncio
async def test_flag_off_preserves_gate3d_injection_format(monkeypatch):
    monkeypatch.delenv("BEN_WORKSPACE_CHUNK_RETRIEVAL", raising=False)
    rows = [
        _row(text="first", name="a.txt", created_at=1, rid=uuid.UUID(int=1)),
        _row(text="second", name="b.txt", created_at=2, rid=uuid.UUID(int=2)),
    ]
    _patch_session(monkeypatch, rows)
    out = await load_ready_files_context(ORG_A, WS_A, max_chars=10_000)
    assert out.retrieval_mode == "off"
    assert out.fallback_reason == "flag_off"
    assert out.block.startswith("<workspace_files>\n")
    assert 'retrieval_mode="' not in out.block
    assert "retrieval=\"legacy_prefix\"" not in out.block
    assert '[file name="b.txt"]\nsecond\n[/file]' in out.block
    assert out.block.index("b.txt") < out.block.index("a.txt")
    assert out.block.endswith("</workspace_files>")


@pytest.mark.asyncio
async def test_flag_off_does_not_use_budgeted_set_change(monkeypatch):
    """OFF path still ranks then budgets; canary survives by filename."""
    monkeypatch.delenv("BEN_WORKSPACE_CHUNK_RETRIEVAL", raising=False)
    older = [
        _row(text="O" * 2000, name=f"old_{i}.txt", created_at=i, rid=uuid.UUID(int=i + 1))
        for i in range(1, 7)
    ]
    canary = _row(text="CANARY-SECRET-TOKEN", name="ben_canary.txt", created_at=99)
    _patch_session(monkeypatch, older + [canary])
    out = await load_ready_files_context(
        ORG_A, WS_A, max_chars=12_000, per_file_max=2_000, user_query="Read ben_canary.txt"
    )
    assert "CANARY-SECRET-TOKEN" in out.block
    assert out.retrieval_mode == "off"


def test_gate3d_rank_math_unchanged():
    named = EligibleFile(uuid.UUID(int=1), 1, "ben_canary.txt", "ben_canary.txt", "unrelated")
    other = EligibleFile(uuid.UUID(int=2), 99, "notes.txt", "notes.txt", "canary notes")
    ranked = rank_eligible_files([other, named], "Read ben_canary.txt")
    assert ranked[0].file.display_name == "ben_canary.txt"
    budgeted, _ = apply_context_budget(
        ranked, max_chars=20, per_file_max=20, sanitize_name=lambda n: n
    )
    assert budgeted[0].name == "ben_canary.txt"


# --------------------------------------------------------------------------- #
# Chat wiring                                                                 #
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
async def test_direct_chat_without_workspace_unchanged(monkeypatch):
    captured: dict = {}
    _patch_stream_pipeline(monkeypatch, captured)

    async def boom(*a, **k):
        raise AssertionError("load_ready_files_context should not be called without a workspace")

    monkeypatch.setattr("services.chat_service.load_ready_files_context", boom)
    events = []
    async for line in chat_service.stream_chat_response(
        "hello",
        "user-1",
        str(ORG_A),
        "free",
        thread_id=uuid.uuid4(),
        provider_id="gpt",
        project_id=None,
    ):
        events.append(json.loads(line))
    assert "<workspace_files>" not in captured["message"]
    done = next(e for e in events if e["type"] == "done")
    assert done["workspace_files_injected"] is False
    assert done["retrieval_mode"] == "off"


@pytest.mark.asyncio
async def test_fts_error_fallback_does_not_break_chat(monkeypatch):
    captured: dict = {}
    _patch_stream_pipeline(monkeypatch, captured)

    async def fake_ctx(_org, _ws, *, max_chars, user_query=None, **_k):
        return WorkspaceFilesContext(
            block='<workspace_files retrieval_mode="prefix_fallback" coverage="legacy">\n'
            '[file name="a.txt" retrieval="legacy_prefix"]\nprefix\n[/file]\n'
            "<coverage>This evidence is a clipped prefix of extracted text, "
            "not a full-document read.</coverage>\n</workspace_files>",
            count=1,
            chars=6,
            truncated=False,
            retrieval_mode="prefix_fallback",
            fallback_reason="fts_error",
            extraction_coverage="legacy",
        )

    monkeypatch.setattr("services.chat_service.load_ready_files_context", fake_ctx)
    events = []
    async for line in chat_service.stream_chat_response(
        NATURAL_TERMINATION,
        "user-1",
        str(ORG_A),
        "free",
        thread_id=uuid.uuid4(),
        provider_id="gpt",
        project_id=WS_A,
    ):
        events.append(json.loads(line))
    assert any(e["type"] == "done" for e in events)
    done = next(e for e in events if e["type"] == "done")
    assert done["workspace_files_injected"] is True
    assert done["fallback_reason"] == "fts_error"
    assert "prefix" in captured["message"]


# --------------------------------------------------------------------------- #
# DB integration                                                              #
# --------------------------------------------------------------------------- #

async def _open():
    if asyncpg is None:
        pytest.skip("asyncpg not installed")
    try:
        conn = await asyncpg.connect(_DSN)
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"Postgres unavailable: {exc}")
    present = await conn.fetchval("SELECT to_regclass('ben.workspace_file_chunks') IS NOT NULL")
    if not present:
        await conn.close()
        pytest.skip("Document Intelligence schema not applied")
    return conn


async def _mk_workspace(conn, org_id, name="di-gate4a"):
    ws = uuid.uuid4()
    await conn.execute(
        "INSERT INTO ben.projects (id, org_id, name, status) VALUES ($1,$2,$3,'active')",
        ws, org_id, name,
    )
    return ws


async def _mk_file(
    conn,
    org_id,
    ws,
    *,
    name="contract.pdf",
    text="legacy prefix text",
    index_status="indexed",
    indexed_chunk_count=1,
    extraction_status="complete",
    extraction_truncated=False,
    created_at=None,
) -> uuid.UUID:
    fid = uuid.uuid4()
    extra = ""
    args = [
        fid, org_id, ws, name, text, index_status, indexed_chunk_count,
        extraction_status, extraction_truncated, f"k/{fid}",
    ]
    created_sql = ""
    if created_at is not None:
        created_sql = ", created_at"
        extra = ", $11"
        args.append(created_at)
    await conn.execute(
        f"""
        INSERT INTO ben.workspace_files
            (id, org_id, workspace_id, project_id, original_filename, display_name,
             media_type, byte_size, checksum, storage_key, status, extracted_text,
             extraction_status, index_status, indexed_chunk_count, extraction_truncated
             {created_sql})
        VALUES ($1,$2,$3,$3,$4,$4,'application/pdf',0,'x',$10,'ready',$5,
                $8,$6,$7,$9{extra})
        """,
        *args,
    )
    return fid


async def _mk_chunk(conn, org_id, ws, fid, *, document_chunk_index, text, page_number, chunk_id=None):
    cid = chunk_id or uuid.uuid4()
    await conn.execute(
        """
        INSERT INTO ben.workspace_file_chunks
            (id, org_id, workspace_id, file_id, page_number, page_chunk_index,
             document_chunk_index, text, char_count, extraction_version, chunking_version)
        VALUES ($1,$2,$3,$4,$5,0,$6,$7,$8,1,1)
        """,
        cid, org_id, ws, fid, page_number, document_chunk_index, text, len(text),
    )
    return cid


async def _cleanup(conn, *workspaces):
    for ws in workspaces:
        await conn.execute("DELETE FROM ben.projects WHERE id=$1", ws)


@pytest_asyncio.fixture
async def fresh_engine():
    from database.connection import dispose_engine

    await dispose_engine()
    yield
    await dispose_engine()


def _enable(monkeypatch, ws=None):
    monkeypatch.setenv("BEN_WORKSPACE_CHUNK_RETRIEVAL", "on")
    if ws is None:
        monkeypatch.delenv("BEN_WORKSPACE_CHUNK_RETRIEVAL_WORKSPACE_IDS", raising=False)
    else:
        monkeypatch.setenv("BEN_WORKSPACE_CHUNK_RETRIEVAL_WORKSPACE_IDS", str(ws))


@pytest.mark.asyncio
async def test_early_middle_late_and_natural_clause(fresh_engine, monkeypatch):
    _enable(monkeypatch)
    conn = await _open()
    org = uuid.uuid4()
    ws = await _mk_workspace(conn, org)
    try:
        fid = await _mk_file(
            conn, org, ws, name="master_agreement.pdf",
            text="PREFIX ONLY introduction without the later clauses.",
            indexed_chunk_count=4,
        )
        await _mk_chunk(conn, org, ws, fid, document_chunk_index=0, page_number=2,
                        text=f"{EARLY} Introduction and definitions for the parties.")
        await _mk_chunk(conn, org, ws, fid, document_chunk_index=1, page_number=100,
                        text=f"{MIDDLE} Delivery schedule and acceptance criteria.")
        await _mk_chunk(
            conn, org, ws, fid, document_chunk_index=2, page_number=120,
            text=(
                "Section 14.2 Termination rights. Either party may terminate this "
                f"agreement upon thirty days written notice for material breach. {CLAUSE}."
            ),
        )
        await _mk_chunk(conn, org, ws, fid, document_chunk_index=3, page_number=198,
                        text=f"{LATE} Signatures and annexes of the closing set.")
        await conn.close()

        early = await load_ready_files_context(
            org, ws, max_chars=12_000,
            user_query=f"What does the introduction say about {EARLY} definitions?",
        )
        assert early.retrieval_mode == "chunks"
        assert 2 in early.evidence_pages
        assert EARLY in early.block
        assert 'page="2"' in early.block

        middle = await load_ready_files_context(
            org, ws, max_chars=12_000,
            user_query=f"Where is the delivery schedule {MIDDLE} described?",
        )
        assert 100 in middle.evidence_pages
        assert MIDDLE in middle.block

        late = await load_ready_files_context(org, ws, max_chars=12_000, user_query=NATURAL_LATE)
        assert 198 in late.evidence_pages
        assert LATE in late.block
        assert late.block.index("page=\"198\"") > 0

        clause = await load_ready_files_context(
            org, ws, max_chars=12_000, user_query=NATURAL_TERMINATION
        )
        assert clause.retrieval_mode == "chunks"
        assert 120 in clause.evidence_pages
        assert "thirty days" in clause.block
        assert CLAUSE in clause.block
        assert "PREFIX ONLY" not in clause.block
        assert clause.chars <= MAX_EVIDENCE_CHARS
        assert clause.chunks_considered <= MAX_CHUNKS_CONSIDERED
        assert clause.chunks_selected <= MAX_CHUNKS_SELECTED
    finally:
        conn = await _open()
        await _cleanup(conn, ws)
        await conn.close()


@pytest.mark.asyncio
async def test_explicit_filename_late_page_not_gate3d_cutoff(fresh_engine, monkeypatch):
    """Newer decoys would starve Gate 3D; named + FTS still finds the late page."""
    _enable(monkeypatch)
    conn = await _open()
    org = uuid.uuid4()
    ws = await _mk_workspace(conn, org)
    try:
        decoys = []
        for i in range(8):
            decoys.append(
                await _mk_file(
                    conn, org, ws, name=f"notes_{i}.txt",
                    text="recent meeting notes about catering and office plants",
                    index_status="not_indexed",
                    indexed_chunk_count=0,
                    extraction_status="complete",
                )
            )
        fid = await _mk_file(
            conn, org, ws, name="master_agreement.pdf",
            text="PREFIX of the old contract without the closing annex.",
            indexed_chunk_count=1,
        )
        await _mk_chunk(
            conn, org, ws, fid, document_chunk_index=0, page_number=198,
            text=f"{LATE} Signatures and annexes of the closing set.",
        )
        await conn.close()

        out = await load_ready_files_context(
            org, ws, max_chars=12_000, per_file_max=2_000,
            user_query=f"Read master_agreement.pdf — {NATURAL_LATE}",
        )
        assert LATE in out.block
        assert 198 in out.evidence_pages
        assert "catering" not in out.block
        assert out.retrieval_mode == "chunks"
    finally:
        conn = await _open()
        await _cleanup(conn, ws)
        await conn.close()


@pytest.mark.asyncio
async def test_unnamed_query_searches_all_indexed_not_budgeted_set(fresh_engine, monkeypatch):
    _enable(monkeypatch)
    conn = await _open()
    org = uuid.uuid4()
    ws = await _mk_workspace(conn, org)
    try:
        for i in range(8):
            await _mk_file(
                conn, org, ws, name=f"recent_{i}.txt",
                text="recent meeting notes about catering",
                index_status="not_indexed",
                indexed_chunk_count=0,
            )
        fid = await _mk_file(
            conn, org, ws, name="old_contract.pdf",
            text="PREFIX without late annex words",
            indexed_chunk_count=1,
        )
        await _mk_chunk(
            conn, org, ws, fid, document_chunk_index=0, page_number=198,
            text=f"{LATE} Signatures and annexes of the closing set.",
        )
        await conn.close()
        out = await load_ready_files_context(org, ws, max_chars=12_000, user_query=NATURAL_LATE)
        assert LATE in out.block
        assert 198 in out.evidence_pages
    finally:
        conn = await _open()
        await _cleanup(conn, ws)
        await conn.close()


@pytest.mark.asyncio
async def test_hebrew_lexical_query(fresh_engine, monkeypatch):
    _enable(monkeypatch)
    conn = await _open()
    org = uuid.uuid4()
    ws = await _mk_workspace(conn, org)
    try:
        fid = await _mk_file(conn, org, ws, name="he_contract.pdf", indexed_chunk_count=1)
        await _mk_chunk(
            conn, org, ws, fid, document_chunk_index=0, page_number=44,
            text=f"סעיף סיום. לצדדים זכויות סיום בהודעה של שלושים יום. {HEBREW_MARK}",
        )
        await conn.close()
        out = await load_ready_files_context(org, ws, max_chars=12_000, user_query=NATURAL_HEBREW)
        assert out.retrieval_mode == "chunks"
        assert 44 in out.evidence_pages
        assert HEBREW_MARK in out.block
    finally:
        conn = await _open()
        await _cleanup(conn, ws)
        await conn.close()


@pytest.mark.asyncio
async def test_zero_hit_labeled_fallback_no_random_chunks(fresh_engine, monkeypatch):
    _enable(monkeypatch)
    conn = await _open()
    org = uuid.uuid4()
    ws = await _mk_workspace(conn, org)
    try:
        fid = await _mk_file(
            conn, org, ws, name="contract.pdf",
            text="PREFIX-VISIBLE-ONLY body of the agreement",
            indexed_chunk_count=1,
        )
        await _mk_chunk(
            conn, org, ws, fid, document_chunk_index=0, page_number=10,
            text="ordinary commercial terms about delivery and invoices",
        )
        await conn.close()
        out = await load_ready_files_context(
            org, ws, max_chars=12_000,
            user_query=f"Does the file mention {ABSENT} anywhere at all?",
        )
        assert out.retrieval_mode == "prefix_fallback"
        assert out.fallback_reason == "no_lexical_match"
        assert out.chunks_selected == 0
        assert "ordinary commercial terms" not in out.block
        assert "PREFIX-VISIBLE-ONLY" in out.block
        assert "legacy_prefix" in out.block
        assert "not a full-document read" in out.block
    finally:
        conn = await _open()
        await _cleanup(conn, ws)
        await conn.close()


@pytest.mark.asyncio
async def test_legacy_ready_file_uses_gate3d_prefix(fresh_engine, monkeypatch):
    _enable(monkeypatch)
    conn = await _open()
    org = uuid.uuid4()
    ws = await _mk_workspace(conn, org)
    try:
        await _mk_file(
            conn, org, ws, name="legacy.txt",
            text="LEGACY-PREFIX-BODY from the old extractor",
            index_status="not_indexed",
            indexed_chunk_count=0,
        )
        await conn.close()
        out = await load_ready_files_context(
            org, ws, max_chars=12_000, user_query="What does this contract say about termination rights?"
        )
        assert out.retrieval_mode == "prefix_fallback"
        assert out.fallback_reason == "not_indexed"
        assert "LEGACY-PREFIX-BODY" in out.block
        assert "[chunk " not in out.block
        assert "legacy_prefix" in out.block
    finally:
        conn = await _open()
        await _cleanup(conn, ws)
        await conn.close()


@pytest.mark.asyncio
async def test_indexed_without_chunk_rows_is_mismatch_fallback(fresh_engine, monkeypatch):
    _enable(monkeypatch)
    conn = await _open()
    org = uuid.uuid4()
    ws = await _mk_workspace(conn, org)
    try:
        await _mk_file(
            conn, org, ws, name="broken.pdf",
            text="MISMATCH-PREFIX-BODY",
            index_status="indexed",
            indexed_chunk_count=6,
        )
        await conn.close()
        out = await load_ready_files_context(
            org, ws, max_chars=12_000, user_query=NATURAL_TERMINATION
        )
        assert out.retrieval_mode == "prefix_fallback"
        assert out.fallback_reason == "index_chunk_mismatch"
        assert "MISMATCH-PREFIX-BODY" in out.block
        assert out.chunks_selected == 0
    finally:
        conn = await _open()
        await _cleanup(conn, ws)
        await conn.close()


@pytest.mark.asyncio
async def test_partial_needs_ocr_coverage_honesty(fresh_engine, monkeypatch):
    _enable(monkeypatch)
    conn = await _open()
    org = uuid.uuid4()
    ws = await _mk_workspace(conn, org)
    try:
        fid = await _mk_file(
            conn, org, ws, name="scanmix.pdf",
            text="partial prefix",
            indexed_chunk_count=1,
            extraction_status="partial",
            extraction_truncated=True,
        )
        await _mk_chunk(
            conn, org, ws, fid, document_chunk_index=0, page_number=3,
            text=f"Readable page with {CLAUSE} termination rights and notice.",
        )
        await conn.close()
        out = await load_ready_files_context(org, ws, max_chars=12_000, user_query=NATURAL_TERMINATION)
        assert out.retrieval_mode == "chunks"
        assert out.extraction_coverage == "partial"
        assert CLAUSE in out.block
        assert "not a full-document read" in out.block
        assert 'extraction="partial"' in out.block
    finally:
        conn = await _open()
        await _cleanup(conn, ws)
        await conn.close()


@pytest.mark.asyncio
async def test_same_filename_across_workspaces_no_leak(fresh_engine, monkeypatch):
    _enable(monkeypatch)
    conn = await _open()
    org = uuid.uuid4()
    ws_a = await _mk_workspace(conn, org, "ws-a")
    ws_b = await _mk_workspace(conn, org, "ws-b")
    try:
        fa = await _mk_file(conn, org, ws_a, name="contract.pdf", indexed_chunk_count=1)
        fb = await _mk_file(conn, org, ws_b, name="contract.pdf", indexed_chunk_count=1)
        await _mk_chunk(conn, org, ws_a, fa, document_chunk_index=0, page_number=5,
                        text=f"workspace A secret {CLAUSE}")
        await _mk_chunk(conn, org, ws_b, fb, document_chunk_index=0, page_number=5,
                        text=f"workspace B secret {OTHER_WS}")
        await conn.close()
        out = await load_ready_files_context(org, ws_a, max_chars=12_000, user_query=NATURAL_TERMINATION)
        assert CLAUSE in out.block
        assert OTHER_WS not in out.block
        assert "workspace B secret" not in out.block
    finally:
        conn = await _open()
        await _cleanup(conn, ws_a, ws_b)
        await conn.close()


@pytest.mark.asyncio
async def test_cross_org_isolation(fresh_engine, monkeypatch):
    _enable(monkeypatch)
    conn = await _open()
    org_a = uuid.uuid4()
    org_b = uuid.uuid4()
    ws_a = await _mk_workspace(conn, org_a, "oa")
    ws_b = await _mk_workspace(conn, org_b, "ob")
    try:
        fa = await _mk_file(conn, org_a, ws_a, name="contract.pdf", indexed_chunk_count=1)
        fb = await _mk_file(conn, org_b, ws_b, name="contract.pdf", indexed_chunk_count=1)
        await _mk_chunk(conn, org_a, ws_a, fa, document_chunk_index=0, page_number=8,
                        text=f"org A {CLAUSE} termination rights")
        await _mk_chunk(conn, org_b, ws_b, fb, document_chunk_index=0, page_number=8,
                        text=f"org B leaked {OTHER_WS} termination rights")
        await conn.close()
        out = await load_ready_files_context(org_a, ws_a, max_chars=12_000, user_query=NATURAL_TERMINATION)
        assert CLAUSE in out.block
        assert OTHER_WS not in out.block
        assert "org B leaked" not in out.block
    finally:
        conn = await _open()
        await _cleanup(conn, ws_a, ws_b)
        await conn.close()


@pytest.mark.asyncio
async def test_two_hundred_matches_honor_caps(fresh_engine, monkeypatch):
    _enable(monkeypatch)
    conn = await _open()
    org = uuid.uuid4()
    ws = await _mk_workspace(conn, org)
    try:
        files = []
        for n in range(5):
            fid = await _mk_file(
                conn, org, ws, name=f"vol_{n}.pdf",
                text="prefix",
                indexed_chunk_count=40,
            )
            files.append(fid)
            for i in range(40):
                await _mk_chunk(
                    conn, org, ws, fid,
                    document_chunk_index=i,
                    page_number=i + 1,
                    text=f"shared liabilityclauseunique term page {i} file {n} " + ("x" * 200),
                )
        await conn.close()
        out = await load_ready_files_context(
            org, ws, max_chars=12_000,
            user_query="What does this contract say about the liabilityclauseunique term?",
        )
        assert out.retrieval_mode == "chunks"
        assert out.chunks_considered <= MAX_CHUNKS_CONSIDERED
        assert out.chunks_selected <= MAX_CHUNKS_SELECTED
        assert out.chars <= MAX_EVIDENCE_CHARS
        assert out.block.count("[chunk ") <= MAX_CHUNKS_SELECTED
        # per-file cap
        from collections import Counter
        pages_by_file = Counter()
        for line in out.block.splitlines():
            if line.startswith('[file name='):
                current = line
            elif line.startswith("[chunk "):
                pages_by_file[current] += 1
        assert all(v <= MAX_CHUNKS_PER_FILE for v in pages_by_file.values())
    finally:
        conn = await _open()
        await _cleanup(conn, ws)
        await conn.close()


@pytest.mark.asyncio
async def test_fts_timeout_falls_back_labeled(fresh_engine, monkeypatch):
    _enable(monkeypatch)
    conn = await _open()
    org = uuid.uuid4()
    ws = await _mk_workspace(conn, org)
    try:
        await _mk_file(
            conn, org, ws, name="contract.pdf",
            text="TIMEOUT-PREFIX-BODY",
            indexed_chunk_count=1,
        )
        # prove_chunk_rows needs a real row so we qualify, then FTS is stubbed.
        fid_row = await conn.fetchval(
            "SELECT id FROM ben.workspace_files WHERE workspace_id=$1", ws
        )
        await _mk_chunk(
            conn, org, ws, fid_row, document_chunk_index=0, page_number=1,
            text="termination rights notice supplier",
        )
        await conn.close()

        async def fake_search(*a, **k):
            return None, 201.0, "fts_timeout"

        monkeypatch.setattr(
            "services.workspace_files.service.search_chunks_bounded", fake_search
        )
        out = await load_ready_files_context(org, ws, max_chars=12_000, user_query=NATURAL_TERMINATION)
        assert out.retrieval_mode == "prefix_fallback"
        assert out.fallback_reason == "fts_timeout"
        assert "TIMEOUT-PREFIX-BODY" in out.block
    finally:
        conn = await _open()
        await _cleanup(conn, ws)
        await conn.close()


@pytest.mark.asyncio
async def test_fts_error_falls_back_labeled(fresh_engine, monkeypatch):
    _enable(monkeypatch)
    conn = await _open()
    org = uuid.uuid4()
    ws = await _mk_workspace(conn, org)
    try:
        fid = await _mk_file(
            conn, org, ws, name="contract.pdf",
            text="ERROR-PREFIX-BODY",
            indexed_chunk_count=1,
        )
        await _mk_chunk(
            conn, org, ws, fid, document_chunk_index=0, page_number=1,
            text="termination rights notice supplier",
        )
        await conn.close()

        async def fake_search(*a, **k):
            return None, 12.0, "fts_error"

        monkeypatch.setattr(
            "services.workspace_files.service.search_chunks_bounded", fake_search
        )
        out = await load_ready_files_context(org, ws, max_chars=12_000, user_query=NATURAL_TERMINATION)
        assert out.retrieval_mode == "prefix_fallback"
        assert out.fallback_reason == "fts_error"
        assert "ERROR-PREFIX-BODY" in out.block
    finally:
        conn = await _open()
        await _cleanup(conn, ws)
        await conn.close()
