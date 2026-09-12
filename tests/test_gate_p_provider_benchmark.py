"""Gate P harness tests. Not a retrieval-quality gate. Does not enable production FTS."""
from __future__ import annotations

from pathlib import Path

import pytest

from services.workspace_files.chunk_retriever import chunk_retrieval_enabled
from tests.gate_m.gold_questions import QUESTIONS
from tests.gate_m.measure import WS
from tests.gate_p.capability import capability_matrix, key_present
from tests.gate_p.fts_isolated import run_fts_isolated
from tests.gate_p.position import position_bucket
from tests.gate_p.prefix_baseline import load_prefix_mode
from tests.gate_p.prompt import parse_answer_payload, user_prompt
from tests.gate_p.report import comparison_rows

FIXTURE = Path(__file__).resolve().parents[0] / "fixtures" / "gate_p"


def test_gold_set_is_unmodified_fifty_questions():
    assert len(QUESTIONS) == 50
    assert {q["question_id"] for q in QUESTIONS} == {f"M{i:02d}" for i in range(1, 51)}


def test_position_buckets_cover_early_middle_late():
    buckets = {position_bucket(q) for q in QUESTIONS}
    assert {"EARLY", "MIDDLE", "LATE"} <= buckets


def test_capability_matrix_covers_four_providers():
    names = {row["provider"] for row in capability_matrix()}
    assert names == {"OpenAI", "Anthropic / Claude", "Google / Gemini", "Grok / xAI"}
    for row in capability_matrix():
        assert "safe_to_benchmark_now" in row
        assert "existing_credentials_usable" in row


def test_answer_contract_does_not_leak_gold():
    q = QUESTIONS[0]
    prompt = user_prompt(q["question"])
    assert q["gold_answer"] not in prompt
    for span in q["decisive_evidence"]:
        loc = span["text_or_locator"]
        if loc and loc not in q["question"]:
            assert loc not in prompt


def test_parse_answer_payload_json():
    parsed = parse_answer_payload('{"answer":"not established","answerable":false,"supporting_evidence":[],"page":null}')
    assert parsed["parse_ok"] is True
    assert parsed["answerable"] is False


def test_prefix_baseline_loads_committed_gate_m():
    mode = load_prefix_mode()
    assert mode["summary"]["questions"] == 50
    assert mode["summary"]["answer_correctness"] == "27/50"
    assert mode["summary"]["mrl"] == 22
    assert mode["rows"][0]["position"] in {"EARLY", "MIDDLE", "LATE", "NONE"}


def test_chunk_fts_remains_off_in_default_env():
    assert chunk_retrieval_enabled(WS) is False


def test_missing_keys_are_not_invented():
    # This environment may or may not have keys; the helper must not throw.
    for prov in ("openai", "anthropic", "google", "xai"):
        assert key_present(prov) in {True, False}


def test_comparison_marks_blocked_modes():
    rows = comparison_rows(
        {
            "BEN_PREFIX_2000": {"summary": {"mode": "BEN_PREFIX_2000", "status": "measured", "answer_correctness": "27/50", "decisive_span_recall": "21/43", "mrl": 22, "unanswerable_precision": "7/7", "early": "1/1", "middle": "1/1", "late": "0/1", "exceptions": "1/5", "multi_hop": "1/6", "global": "2/5", "mean_latency_ms": 0.2, "input_tokens": 0, "output_tokens": 0, "approx_cost_usd": 0}},
            "OPENAI_NATIVE_DOCUMENT": {"summary": {"mode": "OPENAI_NATIVE_DOCUMENT", "status": "blocked", "blocked_reason": "no key"}},
        }
    )
    by = {r["mode"]: r for r in rows}
    assert by["BEN_PREFIX_2000"]["answer_correctness"] == "27/50"
    assert by["OPENAI_NATIVE_DOCUMENT"]["answer_correctness"] == "BLOCKED"


@pytest.mark.asyncio
async def test_isolated_fts_runs_without_leaving_flag_on(tmp_path):
    result = await run_fts_isolated(tmp_path / "gold.pdf")
    assert result["summary"]["status"] == "measured"
    assert result["summary"]["questions"] == 50
    assert result["summary"]["retrieval_kind"] == "fts"
    assert chunk_retrieval_enabled(WS) is False
    # Early title-page locators should be retrievable by lexical FTS.
    by_id = {r["question_id"]: r for r in result["rows"]}
    assert by_id["M01"]["decisive_in_injected"] is True

