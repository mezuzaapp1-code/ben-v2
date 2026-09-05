"""SOURCE COMPLETENESS & EVIDENCE V1 — capture, persist, stream parity."""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import services.chat_service as chat_service
from services.message_format import decode_message, encode_chat_assistant
from services.workspace_files.chunk_retriever import ChunkHit, ReadyFile
from services.workspace_files.file_resolver import BudgetedFile, EligibleFile
from services.workspace_files.response_evidence import (
    MAX_EVIDENCE_ITEMS,
    MAX_EXCERPT_CHARS,
    MAX_SOURCES,
    MAX_TOTAL_EXCERPT_CHARS,
    EvidenceUnit,
    build_response_evidence,
    clip_excerpt,
    sanitize_response_evidence,
    units_from_budgeted,
    units_from_chunk_hits,
)
from services.workspace_files.multi_source import MODE_CLARIFY, SourceResolution
from services.workspace_files.service import WorkspaceFilesContext, _context_from_gate3d

FILE_A = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1")
FILE_B = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb2")
FILE_C = uuid.UUID("cccccccc-cccc-cccc-cccc-ccccccccccc3")
CHUNK_A = uuid.UUID("11111111-1111-1111-1111-111111111111")
CHUNK_B = uuid.UUID("22222222-2222-2222-2222-222222222222")
ORG_A = uuid.UUID("11111111-1111-1111-1111-111111111111")
WS_A = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


def _chunk_unit(*, source=FILE_A, name="A.pdf", excerpt="total 306", chunk=CHUNK_A, page=2):
    return EvidenceUnit(
        source_id=str(source),
        display_name=name,
        excerpt=excerpt,
        chunk_id=str(chunk),
        page=page,
    )


def _prefix_unit(*, source=FILE_B, name="B.pdf", excerpt="total 193"):
    return EvidenceUnit(source_id=str(source), display_name=name, excerpt=excerpt)


def test_clip_excerpt_caps_code_points():
    body = "ש" * 500
    assert len(clip_excerpt(body)) == MAX_EXCERPT_CHARS


def test_chunk_capture_keeps_page_and_injected_excerpt():
    injected = "page two amount ₪306,233.60 " + ("x" * 200)
    out = build_response_evidence(
        retrieval_mode="chunks",
        units=[_chunk_unit(excerpt=injected, page=2)],
    )
    assert out is not None
    assert out["retrieval_mode"] == "chunks"
    assert out["sources"] == [
        {"source_id": str(FILE_A), "source_type": "workspace_file", "display_name": "A.pdf"}
    ]
    item = out["evidence"][0]
    assert item["evidence_id"] == f"chunk:{CHUNK_A}"
    assert item["chunk_id"] == str(CHUNK_A)
    assert item["page"] == 2
    assert item["excerpt"] == injected[:MAX_EXCERPT_CHARS]
    assert item["origin"] == "ben_retrieval"
    assert item["excerpt"] != "reloaded-from-db"


def test_prefix_capture_never_gets_page_or_chunk():
    sneaky = EvidenceUnit(
        source_id=str(FILE_A),
        display_name="A.pdf",
        excerpt="prefix body",
        chunk_id=str(CHUNK_A),
        page=9,
    )
    # Prefix helper omits chunk/page; constructor still treats missing chunk as prefix.
    prefix_only = _prefix_unit(source=FILE_A, name="A.pdf", excerpt="prefix body")
    out = build_response_evidence(retrieval_mode="prefix_fallback", units=[prefix_only])
    assert out["retrieval_mode"] == "prefix_fallback"
    item = out["evidence"][0]
    assert item["evidence_id"] == f"prefix:{FILE_A}"
    assert "page" not in item
    assert "chunk_id" not in item
    assert item["excerpt"] == "prefix body"
    # A unit that includes chunk_id is chunk evidence — prefix helper must not set those.
    chunky = build_response_evidence(retrieval_mode="prefix_fallback", units=[sneaky])
    assert "chunk_id" in chunky["evidence"][0]


def test_units_from_budgeted_are_prefix_only():
    budgeted = [
        BudgetedFile(name="A.pdf", text="injected prefix", chars=15, clipped=True, file_id=str(FILE_A))
    ]
    units = units_from_budgeted(budgeted)
    assert units[0].chunk_id is None
    assert units[0].page is None
    out = build_response_evidence(retrieval_mode="prefix_fallback", units=units)
    assert "page" not in out["evidence"][0]
    assert "chunk_id" not in out["evidence"][0]


def test_mixed_preserves_per_item_truth():
    out = build_response_evidence(
        retrieval_mode="mixed",
        units=[
            _chunk_unit(source=FILE_A, name="A.pdf", excerpt="chunk A", page=3),
            _prefix_unit(source=FILE_B, name="B.pdf", excerpt="prefix B"),
        ],
    )
    assert out["retrieval_mode"] == "mixed"
    assert [s["source_id"] for s in out["sources"]] == [str(FILE_A), str(FILE_B)]
    chunk, prefix = out["evidence"]
    assert chunk["page"] == 3 and chunk["chunk_id"] == str(CHUNK_A)
    assert "page" not in prefix and "chunk_id" not in prefix


def test_injected_only_sources():
    out = build_response_evidence(
        retrieval_mode="chunks",
        units=[_chunk_unit(source=FILE_A, name="A.pdf")],
    )
    ids = {s["source_id"] for s in out["sources"]}
    assert str(FILE_A) in ids
    assert str(FILE_C) not in ids


def test_empty_units_are_none():
    assert build_response_evidence(retrieval_mode="chunks", units=[]) is None
    assert build_response_evidence(retrieval_mode="off", units=[_chunk_unit()]) is None


def test_caps_sources_items_and_total_excerpt():
    units = []
    for i in range(10):
        sid = uuid.UUID(int=i + 1)
        units.append(
            EvidenceUnit(
                source_id=str(sid),
                display_name=f"F{i}.pdf",
                excerpt="n" * 400,
                chunk_id=str(uuid.UUID(int=100 + i)),
                page=1,
            )
        )
    out = build_response_evidence(retrieval_mode="chunks", units=units)
    assert len(out["sources"]) == MAX_SOURCES
    assert len(out["evidence"]) <= MAX_EVIDENCE_ITEMS
    assert sum(len(e["excerpt"]) for e in out["evidence"]) <= MAX_TOTAL_EXCERPT_CHARS


def test_gate3d_context_builds_prefix_evidence():
    eligible = [
        EligibleFile(
            id=FILE_A,
            created_at=None,
            display_name="Proposal A.pdf",
            original_filename="Proposal A.pdf",
            text="alpha total 306",
        ),
        EligibleFile(
            id=FILE_B,
            created_at=None,
            display_name="Proposal B.pdf",
            original_filename="Proposal B.pdf",
            text="beta total 193",
        ),
    ]
    ctx = _context_from_gate3d(eligible, "שתי ההצעות", 8000, None)
    assert ctx.used_files
    ev = ctx.response_evidence
    assert ev is not None
    assert ev["retrieval_mode"] == "prefix_fallback"
    ids = {s["source_id"] for s in ev["sources"]}
    assert str(FILE_A) in ids and str(FILE_B) in ids
    for item in ev["evidence"]:
        assert "page" not in item
        assert "chunk_id" not in item
        assert item["excerpt"]


def test_empty_gate3d_has_no_evidence():
    ctx = _context_from_gate3d([], "q", 8000, None)
    assert ctx.response_evidence is None
    assert ctx.used_files == ()


def test_units_from_chunk_hits_use_hit_text():
    grouped = [
        (
            FILE_A,
            [
                ChunkHit(
                    chunk_id=CHUNK_A,
                    file_id=FILE_A,
                    page_number=4,
                    document_chunk_index=0,
                    text="clipped injected",
                    char_count=16,
                    rank=0.9,
                )
            ],
        )
    ]
    by_id = {
        str(FILE_A): ReadyFile(
            id=FILE_A,
            created_at=None,
            display_name="A.pdf",
            original_filename="orig.pdf",
            text="FULL DOCUMENT SHOULD NOT BE USED",
            index_status="indexed",
            indexed_chunk_count=1,
            extraction_status="complete",
            extraction_truncated=False,
        )
    }
    units = units_from_chunk_hits(grouped, by_id)
    out = build_response_evidence(retrieval_mode="chunks", units=units)
    assert out["evidence"][0]["excerpt"] == "clipped injected"
    assert out["evidence"][0]["page"] == 4
    assert "FULL DOCUMENT" not in json.dumps(out)


def _sample_evidence():
    return build_response_evidence(
        retrieval_mode="chunks",
        units=[_chunk_unit(excerpt="injected A", page=2)],
    )


def test_encode_decode_roundtrip():
    evidence = _sample_evidence()
    raw = encode_chat_assistant(
        "answer",
        model_used="gpt-4o-mini",
        cost_usd=0.01,
        provider_id="gpt",
        used_files=[{"id": str(FILE_A), "name": "A.pdf"}],
        response_evidence=evidence,
    )
    payload = json.loads(raw)
    assert payload["used_files"] == [{"id": str(FILE_A), "name": "A.pdf"}]
    assert payload["response_evidence"] == evidence
    decoded = decode_message("assistant", raw)
    assert decoded["used_files"] == [{"id": str(FILE_A), "name": "A.pdf"}]
    assert decoded["response_evidence"] == evidence


def test_old_envelope_without_response_evidence():
    raw = encode_chat_assistant(
        "legacy",
        model_used="gpt-4o-mini",
        cost_usd=0.01,
        used_files=[{"id": str(FILE_A), "name": "A.pdf"}],
    )
    payload = json.loads(raw)
    assert "response_evidence" not in payload
    decoded = decode_message("assistant", raw)
    assert "response_evidence" not in decoded
    assert decoded["used_files"][0]["name"] == "A.pdf"


def test_malformed_evidence_is_dropped():
    raw = encode_chat_assistant(
        "answer",
        model_used="m",
        cost_usd=0.01,
        used_files=[{"id": str(FILE_A), "name": "A.pdf"}],
        response_evidence={"retrieval_mode": "nope", "sources": [], "evidence": []},
    )
    payload = json.loads(raw)
    assert "response_evidence" not in payload
    decoded = decode_message("assistant", raw)
    assert "response_evidence" not in decoded
    assert decoded["used_files"] == [{"id": str(FILE_A), "name": "A.pdf"}]


def test_sanitize_strips_prefix_page_and_foreign_source():
    dirty = {
        "retrieval_mode": "mixed",
        "sources": [
            {"source_id": str(FILE_A), "source_type": "workspace_file", "display_name": "A.pdf"},
        ],
        "evidence": [
            {
                "evidence_id": f"prefix:{FILE_A}",
                "source_id": str(FILE_A),
                "excerpt": "ok",
                "origin": "ben_retrieval",
                "page": 7,
                "chunk_id": str(CHUNK_A),
            },
            {
                "evidence_id": f"prefix:{FILE_C}",
                "source_id": str(FILE_C),
                "excerpt": "foreign",
                "origin": "ben_retrieval",
            },
        ],
    }
    clean = sanitize_response_evidence(dirty)
    assert len(clean["sources"]) == 1
    # chunk_id present → treated as chunk row; foreign source dropped
    assert all(e["source_id"] == str(FILE_A) for e in clean["evidence"])


def _patch_stream_pipeline(monkeypatch, captured):
    async def fake_stream(message, tenant_id, tier, *, provider_id=None, model_override=None, system=None):
        captured["message"] = message
        yield ("ok", "model-x", "openai")

    async def _aid(*a, **k):
        return uuid.UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")

    def capture_sqlite(*a, **k):
        captured["assistant_content"] = k.get("assistant_content") or (a[2] if len(a) > 2 else None)
        return (1, 2)

    async def _ctx(_o, _t, m):
        return m

    async def _knowledge(_m, payload):
        return payload

    monkeypatch.setattr("services.chat_service.resolve_thread_id", lambda *a, **k: _aid())
    monkeypatch.setattr("services.chat_service.is_project_setup_thread", lambda _tid: False)
    monkeypatch.setattr("services.chat_service.build_chat_message_with_thread_context", _ctx)
    monkeypatch.setattr("services.chat_service.inject_knowledge_few_shot", _knowledge)
    monkeypatch.setattr("services.chat_service.apply_language_context", lambda msg, _lang: msg)
    monkeypatch.setattr("services.chat_service.route_request_stream", fake_stream)
    monkeypatch.setattr("services.chat_service.persist_chat_exchange_sqlite", capture_sqlite)
    monkeypatch.setattr("services.chat_service._schedule_chat_persist", lambda *_a, **_k: None)
    monkeypatch.setattr("services.chat_service.run_copilot_preamble", AsyncMock(return_value=[]))
    monkeypatch.setattr("services.chat_service.record_standard_chat_turn", AsyncMock())


@pytest.mark.asyncio
async def test_stream_done_matches_persisted_envelope(monkeypatch):
    captured: dict = {}
    _patch_stream_pipeline(monkeypatch, captured)
    evidence = _sample_evidence()
    used = [{"id": str(FILE_A), "name": "A.pdf"}]

    async def fake_ctx(_org, _ws, *, max_chars, user_query=None, **_k):
        return WorkspaceFilesContext(
            block='<workspace_files>\n[file name="A.pdf"]\nCANARY\n[/file]\n</workspace_files>',
            count=1,
            chars=6,
            truncated=False,
            used_files=tuple(used),
            response_evidence=evidence,
        )

    monkeypatch.setattr("services.chat_service.load_ready_files_context", fake_ctx)
    events = []
    async for line in chat_service.stream_chat_response(
        "What is in A.pdf?",
        "user-1",
        str(ORG_A),
        "free",
        thread_id=uuid.uuid4(),
        provider_id="gpt",
        project_id=WS_A,
    ):
        events.append(json.loads(line))
    done = next(e for e in events if e["type"] == "done")
    stored = decode_message("assistant", captured["assistant_content"])
    assert done["response_evidence"] == stored["response_evidence"] == evidence
    assert done["workspace_files_used"] == used
    assert stored["used_files"] == used


@pytest.mark.asyncio
async def test_add_opinion_does_not_inherit_evidence(monkeypatch):
    captured: dict = {}
    _patch_stream_pipeline(monkeypatch, captured)

    async def fake_ctx(*_a, **_k):
        raise AssertionError("Add Opinion must not load workspace files")

    monkeypatch.setattr("services.chat_service.load_ready_files_context", fake_ctx)
    async def rolling(_o, _t, m):
        return m
    monkeypatch.setattr("services.chat_service.build_rolling_stream_prompt", rolling)
    events = []
    async for line in chat_service.stream_chat_response(
        "second opinion",
        "user-1",
        str(ORG_A),
        "free",
        thread_id=uuid.uuid4(),
        provider_id="claude",
        project_id=WS_A,
        expert_opinion=True,
    ):
        events.append(json.loads(line))
    done = next(e for e in events if e["type"] == "done")
    stored = decode_message("assistant", captured["assistant_content"])
    assert "response_evidence" not in done
    assert "response_evidence" not in stored


@pytest.mark.asyncio
async def test_clarification_has_no_evidence(monkeypatch):
    captured: dict = {}
    _patch_stream_pipeline(monkeypatch, captured)
    monkeypatch.setattr("services.chat_service.mutate_source_state", AsyncMock())
    monkeypatch.setattr("services.chat_service.load_source_state", AsyncMock(return_value={}))
    monkeypatch.setattr(
        "services.chat_service.resolve_turn_sources",
        lambda *_a, **_k: SourceResolution(mode=MODE_CLARIFY, file_ids=(), reason="test"),
    )
    monkeypatch.setattr(
        "services.chat_service.clarification_text",
        lambda *_a, **_k: "Which files?",
    )

    async def fake_ctx(*_a, **_k):
        raise AssertionError("clarify must not inject evidence")

    monkeypatch.setattr("services.chat_service.load_ready_files_context", fake_ctx)
    events = []
    async for line in chat_service.stream_chat_response(
        "שתי ההצעות",
        "user-1",
        str(ORG_A),
        "free",
        thread_id=uuid.uuid4(),
        provider_id="gpt",
        project_id=WS_A,
    ):
        events.append(json.loads(line))
    done = next(e for e in events if e["type"] == "done")
    stored = decode_message("assistant", captured["assistant_content"])
    assert "response_evidence" not in done
    assert "response_evidence" not in stored
    assert done.get("workspace_files_used") == []
