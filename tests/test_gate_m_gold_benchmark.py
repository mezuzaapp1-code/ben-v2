"""Gate M gold-file benchmark — measurement harness, not a quality gate."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.workspace_files.chunk_retriever import chunk_retrieval_enabled
from tests.gate_m.gold_document import PAGES
from tests.gate_m.gold_questions import QUESTIONS
from tests.gate_m.measure import (
    WS,
    extract_gold_pdf,
    locator_in_text,
    run_benchmark,
)

FIXTURE_DIR = Path(__file__).resolve().parents[0] / "fixtures" / "gate_m"


def test_question_pack_is_complete():
    ids = [q["question_id"] for q in QUESTIONS]
    assert len(QUESTIONS) == 50
    assert len(set(ids)) == 50
    cats = {q["category"] for q in QUESTIONS}
    assert {
        "SIMPLE_LOCAL",
        "LEXICAL_VARIATION",
        "NUMERIC",
        "EXCEPTION",
        "TABLE",
        "MULTI_HOP",
        "GLOBAL",
        "NEGATIVE",
    } <= cats
    assert any(not q["answerable"] for q in QUESTIONS)
    assert any(q["answerable"] for q in QUESTIONS)


def test_gold_locators_exist_in_authored_pages():
    blob = "\n".join(PAGES)
    for q in QUESTIONS:
        for span in q["decisive_evidence"]:
            loc = span["text_or_locator"]
            assert locator_in_text(loc, blob), f"{q['question_id']} missing {loc!r}"


def test_pdf_extraction_preserves_pages_and_locators(tmp_path):
    extracted = extract_gold_pdf(tmp_path / "gold.pdf")
    assert extracted["source_page_count"] == 18
    assert extracted["legacy_status"] == "ready"
    assert extracted["extracted_chars"] > extracted["prefix_budget"]
    assert "BEN-GOLD-SA-2026-001" in extracted["extracted_text"]
    assert "EXCEPTION-PARTIAL-SHIP-48H" in extracted["extracted_text"]
    assert "[[PAGE 1]]" in extracted["extracted_text"]
    assert "[[PAGE 18]]" in extracted["extracted_text"]


def test_chunk_retrieval_remains_off_for_benchmark_workspace():
    assert chunk_retrieval_enabled(WS) is False


@pytest.mark.asyncio
async def test_gate_m_benchmark_runs_and_early_facts_are_injected(tmp_path):
    result = await run_benchmark(tmp_path / "gold.pdf")
    by_id = {r["question_id"]: r for r in result["rows"]}
    assert result["summary"]["questions"] == 50
    assert result["summary"]["retrieval_mode_observed"] in {"off", "prefix_fallback"}
    # Title-page facts must survive the 2000-char prefix.
    for qid in ("M01", "M02", "M03", "M04"):
        assert by_id[qid]["decisive_in_injected"] is True, qid
        assert by_id[qid]["pass"] is True, qid
    # Late exception must be in the source and is the measurement target.
    assert by_id["M23"]["decisive_in_source"] is True
    json_path = FIXTURE_DIR / "gold_questions.json"
    assert json_path.is_file()
    packed = json.loads(json_path.read_text(encoding="utf-8"))
    assert packed["question_count"] == 50
