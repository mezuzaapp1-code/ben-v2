"""Gate P measurement harness tests. Does not enable production chunk FTS."""
from __future__ import annotations

import os

import pytest

from services.workspace_files.chunk_retriever import chunk_retrieval_enabled
from services.workspace_files.file_resolver import PER_FILE_MAX_CHARS
from tests.gate_m.gold_questions import QUESTIONS
from tests.gate_m.measure import WS, extract_gold_pdf
from tests.gate_p.capability import capability_matrix
from tests.gate_p.fts_isolated import retrieve_for_question, run_isolated_fts, structured_from_pages
from tests.gate_p.position import EARLY, LATE, MIDDLE, SPAN, question_position
from tests.gate_p.providers import run_claude_provider_retrieval, run_openai_native
from tests.gate_p.score import model_answer_ok, parse_answer_json
from services.workspace_files.chunking import chunk_structured_document


def test_gold_pack_unchanged():
    assert len(QUESTIONS) == 50
    assert sum(1 for q in QUESTIONS if q["answerable"]) == 43
    assert sum(1 for q in QUESTIONS if not q["answerable"]) == 7


def test_position_bands_cover_late_exception():
    by_id = {q["question_id"]: q for q in QUESTIONS}
    assert question_position(by_id["M01"]) == EARLY
    assert question_position(by_id["M11"]) == MIDDLE
    assert question_position(by_id["M23"]) == LATE
    assert question_position(by_id["M39"]) == SPAN
    assert question_position(by_id["M48"]) == "NONE"


def test_production_invariants_hold():
    assert PER_FILE_MAX_CHARS == 2000
    assert chunk_retrieval_enabled(WS) is False
    assert (os.getenv("BEN_WORKSPACE_CHUNK_RETRIEVAL") or "").lower() not in {"1", "true", "yes", "on"}


def test_json_answer_scoring():
    q = next(x for x in QUESTIONS if x["question_id"] == "M01")
    parsed = parse_answer_json('{"answer":"BEN-GOLD-SA-2026-001","answerable":true,"supporting_evidence":["Agreement ID: BEN-GOLD-SA-2026-001"],"page":1}')
    assert model_answer_ok(q, parsed) is True
    unans = next(x for x in QUESTIONS if x["question_id"] == "M48")
    parsed_un = parse_answer_json('{"answer":"not established","answerable":false,"supporting_evidence":[],"page":null}')
    assert model_answer_ok(unans, parsed_un) is True


def test_capability_matrix_marks_missing_keys_blocked(monkeypatch):
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY", "XAI_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    matrix = {row["provider"]: row for row in capability_matrix()}
    assert matrix["OPENAI"]["safe_to_benchmark_now"] is False
    assert matrix["ANTHROPIC / CLAUDE"]["safe_to_benchmark_now"] is False
    assert matrix["GOOGLE / GEMINI"]["safe_to_benchmark_now"] is False
    assert matrix["GROK / XAI"]["safe_to_benchmark_now"] is False
    assert matrix["BEN local (prefix / FTS)"]["safe_to_benchmark_now"] is True
    claude_ret = run_claude_provider_retrieval()
    assert claude_ret["status"] == "BLOCKED"
    assert "No applicable" in claude_ret["blocked_reason"]
    native = run_openai_native(b"%PDF", {"extracted_text": "", "pages": []})
    assert native["status"] == "BLOCKED"


def test_isolated_fts_recovers_late_exception(tmp_path):
    extracted = extract_gold_pdf(tmp_path / "gold.pdf")
    doc = structured_from_pages(extracted["pages"])
    chunks = chunk_structured_document(doc)
    q = next(x for x in QUESTIONS if x["question_id"] == "M23")
    block, selected, tokens, _latency = retrieve_for_question(chunks, q["question"])
    assert tokens
    joined = "\n".join(hit.text for hit in selected)
    assert "EXCEPTION-PARTIAL-SHIP-48H" in joined
    assert selected
    result = run_isolated_fts(extracted)
    by_id = {r["question_id"]: r for r in result["rows"]}
    assert result["status"] == "MEASURED_ISOLATED"
    assert by_id["M23"]["decisive_in_injected"] is True
    assert by_id["M01"]["decisive_in_injected"] is True
    # Production flag still off.
    assert chunk_retrieval_enabled(WS) is False


@pytest.mark.asyncio
async def test_prefix_still_misses_late_exception(tmp_path):
    from tests.gate_m.measure import run_benchmark

    result = await run_benchmark(tmp_path / "gold.pdf")
    by_id = {r["question_id"]: r for r in result["rows"]}
    assert by_id["M01"]["pass"] is True
    assert by_id["M23"]["decisive_in_injected"] is False
    assert by_id["M23"]["failure_category"] == "EXCEPTION_MISSED"
    assert result["summary"]["retrieval_mode_observed"] in {"off", "prefix_fallback"}
    assert chunk_retrieval_enabled(WS) is False
