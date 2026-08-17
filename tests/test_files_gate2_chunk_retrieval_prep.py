"""Gate 2 — production-grade chunk retrieval preparation.

Controlled READY-text indexing, no-match fallback, Used Files truthfulness,
fail-closed 4A activation, long-document benchmark, and isolation.
Does not enable production flags or call drain/runner.
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path

import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://ben:ben@127.0.0.1:5432/ben")
os.environ.setdefault("BEN_TEST_PG_DSN", "postgresql://ben:ben@127.0.0.1:5432/ben")

import services.chat_service as chat_service
from services.workspace_files.chunk_retriever import (
    MAX_EVIDENCE_CHARS,
    chunk_retrieval_enabled,
)
from services.workspace_files.chunking import CHUNK_MAX_CHARS, chunk_extracted_text
from services.workspace_files.file_resolver import PER_FILE_MAX_CHARS
from services.workspace_files.ready_text_indexer import (
    DEFAULT_INDEX_LIMIT,
    MAX_INDEX_LIMIT,
    PROTECTED_FILE_IDS,
    clamp_index_limit,
    index_ready_extracted_text,
    parse_index_workspace_allowlist,
    ready_text_index_allowed,
)
from services.workspace_files.service import load_ready_files_context

try:
    import asyncpg
except Exception:  # pragma: no cover
    asyncpg = None

_DSN = os.getenv("BEN_TEST_PG_DSN") or "postgresql://ben:ben@127.0.0.1:5432/ben"

EARLY = "BEGINNINGEVIDENCEALPHA"
MIDDLE = "MIDDLEEVIDENCEBRAVO"
LATE = "LATEEVIDENCECHARLIE"
TERM = "TERMINATIONRIGHTSCLAUSEZXQ"
DISTRACTOR = "CATERINGNOTESDISTRACTORQZX"
QUEUED_SECRET = "QUEUEDSECRETNEVERINJECTQZX"
ABSENT = "zxqv9nonesuchgate2xyz"
OTHER_WS = "OTHERWORKSPACEONLYMARKERZULU"

HISTORICAL_PDF = uuid.UUID("43cef794-1fff-40ae-bd3c-47d9fc121518")
OLD_QUEUED_CANARY = uuid.UUID("0bbd0dd0-cfd9-4ef4-a3b9-c1e96bef83a4")

Q_EARLY = f"What do the opening recitals say about {EARLY}?"
Q_MIDDLE = f"Where is the delivery schedule {MIDDLE} described?"
Q_LATE = f"What do the signatures and annexes say about {LATE}?"
Q_TERM = (
    "What does this contract say about termination rights and how much "
    "notice must the supplier give us?"
)
Q_MULTI = f"Compare the delivery schedule {MIDDLE} with the closing annex {LATE}."
Q_ABSENT = f"Does any file mention {ABSENT} anywhere at all?"
Q_NAMED_ABSENT = f"Read zz_named_only.pdf — does it mention {ABSENT}?"


def _pad(n: int) -> str:
    unit = "of the "
    if n <= 0:
        return ""
    return (unit * (n // len(unit) + 1))[:n]


def _window(body: str, size: int = CHUNK_MAX_CHARS) -> str:
    body = body.rstrip() + "\n"
    if len(body) > size:
        return body[: size - 1] + "\n"
    return body + _pad(size - len(body) - 1) + "\n"


def long_agreement_text() -> str:
    early = _window(f"{EARLY} Opening recitals of the parties to this agreement.")
    pad = _window("Boilerplate background facts occupy this window.")
    middle = _window(f"{MIDDLE} Delivery schedule and acceptance criteria.")
    late = (
        f"{LATE} Signatures and annexes of the closing set. "
        f"Section 14.2 Termination rights. Either party may terminate upon "
        f"thirty days written notice for material breach. {TERM}.\n"
    )
    text = early + pad + middle + late
    assert text.find(EARLY) < PER_FILE_MAX_CHARS
    assert text.find(MIDDLE) > PER_FILE_MAX_CHARS
    assert text.find(LATE) > PER_FILE_MAX_CHARS
    assert text.find(TERM) > PER_FILE_MAX_CHARS
    return text


def _reduction(before: int, after: int) -> float:
    if before <= 0:
        return 0.0
    return max(0.0, 1.0 - (after / before))


# --------------------------------------------------------------------------- #
# Unit                                                                        #
# --------------------------------------------------------------------------- #


def test_chunk_extracted_text_is_deterministic_and_covers_tail():
    text = long_agreement_text()
    first = chunk_extracted_text(text)
    second = chunk_extracted_text(text)
    assert first == second
    assert len(first) >= 4
    assert EARLY in first[0].text
    assert LATE in first[-1].text
    assert first[-1].page_number == len(first)
    assert first[-1].document_chunk_index == len(first) - 1


def test_index_allowlist_fail_closed(monkeypatch):
    monkeypatch.delenv("BEN_WORKSPACE_READY_TEXT_INDEX_WORKSPACE_IDS", raising=False)
    ws = uuid.uuid4()
    assert parse_index_workspace_allowlist("") == set()
    assert ready_text_index_allowed(ws) is False
    monkeypatch.setenv("BEN_WORKSPACE_READY_TEXT_INDEX_WORKSPACE_IDS", str(ws))
    assert ready_text_index_allowed(ws) is True
    assert ready_text_index_allowed(uuid.uuid4()) is False


def test_clamp_index_limit_is_bounded():
    assert clamp_index_limit(None) == DEFAULT_INDEX_LIMIT
    assert clamp_index_limit(0) == 1
    assert clamp_index_limit(999) == MAX_INDEX_LIMIT
    assert clamp_index_limit("nope") == DEFAULT_INDEX_LIMIT


def test_indexer_source_never_touches_drain_or_runner():
    indexer = Path("services/workspace_files/ready_text_indexer.py").read_text()
    drain = Path("services/workspace_files/drain.py").read_text()
    runner = Path("services/workspace_files/runner_config.py").read_text()
    router = Path("routers/document_processing.py").read_text()
    assert "drain_document_processing" not in indexer
    assert "claim_jobs" not in indexer
    assert "CLAIM_GLOBAL" not in indexer
    assert "ready_text_indexer" not in drain
    assert "ready_text_indexer" not in runner
    assert "ready_text_indexer" not in router
    assert "BEN_WORKSPACE_READY_TEXT_INDEX" not in drain


def test_retrieval_flag_requires_workspace_allowlist(monkeypatch):
    monkeypatch.setenv("BEN_WORKSPACE_CHUNK_RETRIEVAL", "on")
    monkeypatch.delenv("BEN_WORKSPACE_CHUNK_RETRIEVAL_WORKSPACE_IDS", raising=False)
    assert chunk_retrieval_enabled(uuid.uuid4()) is False


# --------------------------------------------------------------------------- #
# DB helpers                                                                  #
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


async def _mk_workspace(conn, org_id, name="di-gate2"):
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
    name="doc.txt",
    text="legacy prefix text",
    status="ready",
    index_status="not_indexed",
    indexed_chunk_count=0,
    fid=None,
) -> uuid.UUID:
    fid = fid or uuid.uuid4()
    await conn.execute(
        """
        INSERT INTO ben.workspace_files
            (id, org_id, workspace_id, project_id, original_filename, display_name,
             media_type, byte_size, checksum, storage_key, status, extracted_text,
             extraction_status, index_status, indexed_chunk_count)
        VALUES ($1,$2,$3,$3,$4,$4,'text/plain',0,'x',$5,$6,$7,'complete',$8,$9)
        """,
        fid, org_id, ws, name, f"k/{fid}", status, text, index_status, indexed_chunk_count,
    )
    return fid


async def _cleanup(conn, *workspaces):
    for ws in workspaces:
        await conn.execute("DELETE FROM ben.projects WHERE id=$1", ws)


async def _file_row(conn, fid):
    return await conn.fetchrow(
        """
        SELECT status, extracted_text, index_status, indexed_chunk_count, processing_error
        FROM ben.workspace_files WHERE id=$1
        """,
        fid,
    )


async def _chunk_count(conn, fid):
    return int(
        await conn.fetchval(
            "SELECT count(*) FROM ben.workspace_file_chunks WHERE file_id=$1", fid
        )
        or 0
    )


@pytest_asyncio.fixture
async def fresh_engine():
    from database.connection import dispose_engine

    await dispose_engine()
    yield
    await dispose_engine()


def _enable_index(monkeypatch, ws):
    monkeypatch.setenv("BEN_WORKSPACE_READY_TEXT_INDEX_WORKSPACE_IDS", str(ws))


def _enable_4a(monkeypatch, ws):
    monkeypatch.setenv("BEN_WORKSPACE_CHUNK_RETRIEVAL", "on")
    monkeypatch.setenv("BEN_WORKSPACE_CHUNK_RETRIEVAL_WORKSPACE_IDS", str(ws))


def _disable_4a(monkeypatch):
    monkeypatch.delenv("BEN_WORKSPACE_CHUNK_RETRIEVAL", raising=False)
    monkeypatch.delenv("BEN_WORKSPACE_CHUNK_RETRIEVAL_WORKSPACE_IDS", raising=False)


# --------------------------------------------------------------------------- #
# Indexing safety                                                             #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_index_refuses_without_allowlist(fresh_engine, monkeypatch):
    monkeypatch.delenv("BEN_WORKSPACE_READY_TEXT_INDEX_WORKSPACE_IDS", raising=False)
    conn = await _open()
    org = uuid.uuid4()
    ws = await _mk_workspace(conn, org)
    try:
        fid = await _mk_file(conn, org, ws, name="a.txt", text=long_agreement_text())
        await conn.close()
        out = await index_ready_extracted_text(org_id=org, workspace_id=ws)
        assert out["ok"] is False
        assert out["error"] == "workspace_not_allowlisted"
        assert out["indexed"] == 0
        conn = await _open()
        row = await _file_row(conn, fid)
        assert row["index_status"] == "not_indexed"
        assert await _chunk_count(conn, fid) == 0
    finally:
        conn = await _open()
        await _cleanup(conn, ws)
        await conn.close()


@pytest.mark.asyncio
async def test_index_allowlist_cannot_expand_to_other_workspace(fresh_engine, monkeypatch):
    conn = await _open()
    org = uuid.uuid4()
    ws_a = await _mk_workspace(conn, org, "a")
    ws_b = await _mk_workspace(conn, org, "b")
    _enable_index(monkeypatch, ws_a)
    try:
        fa = await _mk_file(conn, org, ws_a, name="a.txt", text=long_agreement_text())
        fb = await _mk_file(conn, org, ws_b, name="b.txt", text=long_agreement_text())
        await conn.close()
        denied = await index_ready_extracted_text(org_id=org, workspace_id=ws_b)
        assert denied["ok"] is False
        assert denied["error"] == "workspace_not_allowlisted"
        allowed = await index_ready_extracted_text(org_id=org, workspace_id=ws_a)
        assert allowed["ok"] is True
        assert allowed["indexed"] == 1
        conn = await _open()
        assert (await _file_row(conn, fa))["index_status"] == "indexed"
        assert (await _file_row(conn, fb))["index_status"] == "not_indexed"
        assert await _chunk_count(conn, fb) == 0
    finally:
        conn = await _open()
        await _cleanup(conn, ws_a, ws_b)
        await conn.close()


@pytest.mark.asyncio
async def test_index_ready_text_only_skips_non_ready_and_protected(fresh_engine, monkeypatch):
    conn = await _open()
    org = uuid.uuid4()
    ws = await _mk_workspace(conn, org)
    _enable_index(monkeypatch, ws)
    try:
        await conn.execute(
            "DELETE FROM ben.workspace_files WHERE id = ANY($1::uuid[])",
            [HISTORICAL_PDF, OLD_QUEUED_CANARY],
        )
        ready = await _mk_file(conn, org, ws, name="ready.txt", text=long_agreement_text())
        queued = await _mk_file(
            conn, org, ws, name="queued.txt", text=QUEUED_SECRET, status="queued"
        )
        processing = await _mk_file(
            conn, org, ws, name="proc.txt", text="PROCESSING_BODY", status="processing"
        )
        failed = await _mk_file(
            conn, org, ws, name="fail.txt", text="FAILED_BODY", status="failed"
        )
        empty = await _mk_file(conn, org, ws, name="empty.txt", text="   ")
        hist = await _mk_file(
            conn, org, ws, name="hist.pdf", text="HISTORICAL_PDF_TEXT",
            status="queued", fid=HISTORICAL_PDF,
        )
        old = await _mk_file(
            conn, org, ws, name="old_canary.txt", text="OLD_CANARY_TEXT",
            status="queued", fid=OLD_QUEUED_CANARY,
        )
        await conn.close()
        out = await index_ready_extracted_text(org_id=org, workspace_id=ws, limit=20)
        assert out["ok"] is True
        assert out["indexed"] == 1
        assert out["protected_in_workspace"] == 2
        conn = await _open()
        assert (await _file_row(conn, ready))["index_status"] == "indexed"
        assert await _chunk_count(conn, ready) >= 4
        for fid, status, secret in (
            (queued, "queued", QUEUED_SECRET),
            (processing, "processing", "PROCESSING_BODY"),
            (failed, "failed", "FAILED_BODY"),
            (hist, "queued", "HISTORICAL_PDF_TEXT"),
            (old, "queued", "OLD_CANARY_TEXT"),
        ):
            row = await _file_row(conn, fid)
            assert row["status"] == status
            assert row["extracted_text"] == secret
            assert row["index_status"] == "not_indexed"
            assert await _chunk_count(conn, fid) == 0
        empty_row = await _file_row(conn, empty)
        assert empty_row["status"] == "ready"
        assert empty_row["index_status"] == "not_indexed"
        assert await _chunk_count(conn, empty) == 0
        assert set(PROTECTED_FILE_IDS) == {HISTORICAL_PDF, OLD_QUEUED_CANARY}
    finally:
        conn = await _open()
        await _cleanup(conn, ws)
        await conn.close()


@pytest.mark.asyncio
async def test_index_is_idempotent_bounded_and_org_scoped(fresh_engine, monkeypatch):
    conn = await _open()
    org_a = uuid.uuid4()
    org_b = uuid.uuid4()
    ws_a = await _mk_workspace(conn, org_a, "oa")
    ws_b = await _mk_workspace(conn, org_b, "ob")
    _enable_index(monkeypatch, ws_a)
    try:
        files = [
            await _mk_file(conn, org_a, ws_a, name=f"n{i}.txt", text=f"{EARLY} file {i} " + ("body " * 200))
            for i in range(6)
        ]
        other = await _mk_file(conn, org_b, ws_b, name="other.txt", text=f"{OTHER_WS} " + ("body " * 200))
        await conn.close()
        first = await index_ready_extracted_text(org_id=org_a, workspace_id=ws_a, limit=3)
        assert first["ok"] is True
        assert first["indexed"] == 3
        assert first["considered"] == 3
        second = await index_ready_extracted_text(org_id=org_a, workspace_id=ws_a, limit=3)
        assert second["indexed"] == 3
        assert second["already_indexed"] == 0
        leaked = await index_ready_extracted_text(org_id=org_b, workspace_id=ws_b, limit=8)
        assert leaked["ok"] is False
        conn = await _open()
        indexed = 0
        for fid in files:
            if (await _file_row(conn, fid))["index_status"] == "indexed":
                indexed += 1
        assert indexed == 6
        assert (await _file_row(conn, other))["index_status"] == "not_indexed"
        assert await _chunk_count(conn, other) == 0
        counts = [await _chunk_count(conn, fid) for fid in files]
        await conn.close()
        third = await index_ready_extracted_text(org_id=org_a, workspace_id=ws_a, limit=8)
        assert third["indexed"] == 0
        assert third["considered"] == 0
        fourth = await index_ready_extracted_text(org_id=org_a, workspace_id=ws_a, limit=8)
        assert fourth["indexed"] == 0
        conn = await _open()
        counts2 = [await _chunk_count(conn, fid) for fid in files]
        assert counts == counts2
        await conn.close()
    finally:
        conn = await _open()
        await _cleanup(conn, ws_a, ws_b)
        await conn.close()


# --------------------------------------------------------------------------- #
# Fallback + Used Files                                                       #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_no_match_injects_nothing_named_prefix_only(fresh_engine, monkeypatch):
    conn = await _open()
    org = uuid.uuid4()
    ws = await _mk_workspace(conn, org)
    _enable_index(monkeypatch, ws)
    _enable_4a(monkeypatch, ws)
    try:
        agreement = await _mk_file(
            conn, org, ws, name="zz_named_only.pdf", text=long_agreement_text()
        )
        distractor = await _mk_file(
            conn, org, ws, name="notes.txt", text=f"Lunch catering {DISTRACTOR} office plants"
        )
        queued = await _mk_file(
            conn, org, ws, name="queued.txt", text=QUEUED_SECRET, status="queued"
        )
        await conn.close()
        indexed = await index_ready_extracted_text(org_id=org, workspace_id=ws, limit=8)
        assert indexed["indexed"] == 2

        unnamed = await load_ready_files_context(org, ws, max_chars=12_000, user_query=Q_ABSENT)
        assert unnamed.retrieval_mode == "empty"
        assert unnamed.fallback_reason == "no_lexical_match"
        assert unnamed.block == ""
        assert unnamed.used_files == ()
        assert DISTRACTOR not in unnamed.block
        assert QUEUED_SECRET not in unnamed.block

        named = await load_ready_files_context(org, ws, max_chars=12_000, user_query=Q_NAMED_ABSENT)
        assert named.retrieval_mode == "prefix_fallback"
        assert named.fallback_reason == "no_lexical_match"
        assert named.used_files == ({"id": str(agreement), "name": "zz_named_only.pdf"},)
        assert EARLY in named.block
        assert DISTRACTOR not in named.block
        assert QUEUED_SECRET not in named.block
        assert str(distractor) not in str(named.used_files)
        assert str(queued) not in str(named.used_files)
    finally:
        conn = await _open()
        await _cleanup(conn, ws)
        await conn.close()


@pytest.mark.asyncio
async def test_match_injects_chunks_only_used_files_truthful(fresh_engine, monkeypatch):
    conn = await _open()
    org = uuid.uuid4()
    ws = await _mk_workspace(conn, org)
    _enable_index(monkeypatch, ws)
    _enable_4a(monkeypatch, ws)
    try:
        agreement = await _mk_file(
            conn, org, ws, name="master_agreement.pdf", text=long_agreement_text()
        )
        await _mk_file(conn, org, ws, name="notes.txt", text=f"Lunch {DISTRACTOR}")
        await _mk_file(conn, org, ws, name="queued.txt", text=QUEUED_SECRET, status="queued")
        await conn.close()
        await index_ready_extracted_text(org_id=org, workspace_id=ws, limit=8)
        out = await load_ready_files_context(org, ws, max_chars=12_000, user_query=Q_LATE)
        assert out.retrieval_mode == "chunks"
        assert LATE in out.block
        assert DISTRACTOR not in out.block
        assert QUEUED_SECRET not in out.block
        assert EARLY not in out.block
        assert out.used_files == ({"id": str(agreement), "name": "master_agreement.pdf"},)
        assert out.unavailable_count == 1
    finally:
        conn = await _open()
        await _cleanup(conn, ws)
        await conn.close()


# --------------------------------------------------------------------------- #
# Benchmark: Gate 3D vs Gate 4A                                               #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_long_document_benchmark_3d_vs_4a(fresh_engine, monkeypatch):
    conn = await _open()
    org_a = uuid.uuid4()
    org_b = uuid.uuid4()
    ws = await _mk_workspace(conn, org_a, "bench")
    ws_b = await _mk_workspace(conn, org_b, "other")
    _enable_index(monkeypatch, ws)
    try:
        agreement = await _mk_file(
            conn, org_a, ws, name="master_agreement.pdf", text=long_agreement_text()
        )
        await _mk_file(
            conn, org_a, ws, name="notes.txt",
            text=("Weekly catering and office plants. " * 40) + DISTRACTOR,
        )
        await _mk_file(
            conn, org_a, ws, name="queued.txt", text=QUEUED_SECRET, status="queued"
        )
        await _mk_file(
            conn, org_b, ws_b, name="master_agreement.pdf",
            text=f"{OTHER_WS} termination rights {LATE}",
        )
        await conn.close()
        idx = await index_ready_extracted_text(org_id=org_a, workspace_id=ws, limit=8)
        assert idx["indexed"] == 2

        queries = {
            "early": Q_EARLY,
            "middle": Q_MIDDLE,
            "late": Q_LATE,
            "termination": Q_TERM,
            "multi": Q_MULTI,
            "absent": Q_ABSENT,
        }
        rows = []
        for label, query in queries.items():
            _disable_4a(monkeypatch)
            g3 = await load_ready_files_context(org_a, ws, max_chars=12_000, user_query=query)
            _enable_4a(monkeypatch, ws)
            g4 = await load_ready_files_context(org_a, ws, max_chars=12_000, user_query=query)
            rows.append(
                {
                    "query": label,
                    "g3_mode": g3.retrieval_mode,
                    "g3_chars": g3.chars,
                    "g3_used": [u["name"] for u in g3.used_files],
                    "g3_has_early": EARLY in g3.block,
                    "g3_has_middle": MIDDLE in g3.block,
                    "g3_has_late": LATE in g3.block,
                    "g3_has_term": TERM in g3.block,
                    "g3_has_distractor": DISTRACTOR in g3.block,
                    "g3_has_queued": QUEUED_SECRET in g3.block,
                    "g3_has_other": OTHER_WS in g3.block,
                    "g4_mode": g4.retrieval_mode,
                    "g4_chars": g4.chars,
                    "g4_chunks": g4.chunks_selected,
                    "g4_pages": list(g4.evidence_pages),
                    "g4_used": [u["id"] for u in g4.used_files],
                    "g4_used_names": [u["name"] for u in g4.used_files],
                    "g4_has_early": EARLY in g4.block,
                    "g4_has_middle": MIDDLE in g4.block,
                    "g4_has_late": LATE in g4.block,
                    "g4_has_term": TERM in g4.block,
                    "g4_has_distractor": DISTRACTOR in g4.block,
                    "g4_has_queued": QUEUED_SECRET in g4.block,
                    "g4_has_other": OTHER_WS in g4.block,
                    "g4_latency_ms": g4.fts_latency_ms,
                    "reduction": round(_reduction(g3.chars, g4.chars), 4),
                    "token_est_g3": round(g3.chars / 4),
                    "token_est_g4": round(g4.chars / 4),
                }
            )

        by_label = {row["query"]: row for row in rows}

        # Gate 3D misses middle/late; 4A retrieves them.
        assert by_label["middle"]["g3_has_middle"] is False
        assert by_label["late"]["g3_has_late"] is False
        assert by_label["termination"]["g3_has_term"] is False
        assert by_label["middle"]["g4_has_middle"] is True
        assert by_label["late"]["g4_has_late"] is True
        assert by_label["termination"]["g4_has_term"] is True
        assert by_label["middle"]["g4_mode"] == "chunks"
        assert by_label["late"]["g4_mode"] == "chunks"
        assert by_label["late"]["g4_used"] == [str(agreement)]
        assert by_label["late"]["g4_has_distractor"] is False
        assert by_label["late"]["g4_has_queued"] is False
        assert by_label["late"]["g4_has_other"] is False

        # Multi-chunk: both markers, still the agreement only.
        assert by_label["multi"]["g4_has_middle"] is True
        assert by_label["multi"]["g4_has_late"] is True
        assert by_label["multi"]["g4_chunks"] >= 2
        assert by_label["multi"]["g4_used"] == [str(agreement)]

        # No-match must not dump unrelated prefixes.
        assert by_label["absent"]["g4_mode"] == "empty"
        assert by_label["absent"]["g4_chars"] == 0
        assert by_label["absent"]["g4_used"] == []
        assert by_label["absent"]["g4_has_distractor"] is False
        assert by_label["absent"]["g3_has_distractor"] is True

        # Isolation
        for row in rows:
            assert row["g4_has_queued"] is False
            assert row["g4_has_other"] is False
            assert row["g3_has_queued"] is False
            assert row["g3_has_other"] is False

        factual = [by_label[k] for k in ("middle", "late", "termination")]
        for row in factual:
            assert row["g4_chars"] <= MAX_EVIDENCE_CHARS
            assert row["g4_latency_ms"] is not None
            assert row["g4_latency_ms"] < 200
        late_reduction = by_label["late"]["reduction"]
        term_reduction = by_label["termination"]["reduction"]
        assert late_reduction >= 0.70
        assert term_reduction >= 0.70

        # Attach for the run report (assertions above are the contract).
        assert all("token_est_g4" in row for row in rows)
        assert idx["protected_in_workspace"] == 0
    finally:
        conn = await _open()
        await _cleanup(conn, ws, ws_b)
        await conn.close()


@pytest.mark.asyncio
async def test_workspace_allowlist_does_not_enable_other_workspace_retrieval(
    fresh_engine, monkeypatch
):
    conn = await _open()
    org = uuid.uuid4()
    ws_a = await _mk_workspace(conn, org, "a")
    ws_b = await _mk_workspace(conn, org, "b")
    _enable_index(monkeypatch, ws_a)
    _enable_4a(monkeypatch, ws_a)
    try:
        await _mk_file(conn, org, ws_a, name="a.pdf", text=long_agreement_text())
        await _mk_file(conn, org, ws_b, name="b.pdf", text=f"PREFIX {LATE} {TERM}")
        await conn.close()
        await index_ready_extracted_text(org_id=org, workspace_id=ws_a, limit=8)
        a = await load_ready_files_context(org, ws_a, max_chars=12_000, user_query=Q_LATE)
        b = await load_ready_files_context(org, ws_b, max_chars=12_000, user_query=Q_LATE)
        assert a.retrieval_mode == "chunks"
        assert b.retrieval_mode == "off"
        assert b.fallback_reason == "flag_off"
        assert LATE in b.block
    finally:
        conn = await _open()
        await _cleanup(conn, ws_a, ws_b)
        await conn.close()


# --------------------------------------------------------------------------- #
# Chat Used Files on empty 4A miss                                            #
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
async def test_chat_done_used_files_empty_when_no_injection(fresh_engine, monkeypatch):
    conn = await _open()
    org = uuid.uuid4()
    ws = await _mk_workspace(conn, org)
    _enable_index(monkeypatch, ws)
    _enable_4a(monkeypatch, ws)
    captured: dict = {}
    _patch_stream_pipeline(monkeypatch, captured)
    try:
        await _mk_file(conn, org, ws, name="master_agreement.pdf", text=long_agreement_text())
        await _mk_file(conn, org, ws, name="notes.txt", text=DISTRACTOR)
        await conn.close()
        await index_ready_extracted_text(org_id=org, workspace_id=ws, limit=8)
        events = []
        async for line in chat_service.stream_chat_response(
            Q_ABSENT,
            "user-1",
            str(org),
            "free",
            thread_id=uuid.uuid4(),
            provider_id="gpt",
            project_id=ws,
        ):
            events.append(json.loads(line))
        done = next(e for e in events if e["type"] == "done")
        assert done["workspace_files_injected"] is False
        assert done.get("workspace_files_used") in (None, [])
        assert DISTRACTOR not in captured["message"]
        assert EARLY not in captured["message"]
        assert "<workspace_files" not in captured["message"]
    finally:
        conn = await _open()
        await _cleanup(conn, ws)
        await conn.close()
