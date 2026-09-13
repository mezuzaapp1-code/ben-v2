"""Gate I1 improvement-loop prototype tests. Does not enable production FTS."""
from __future__ import annotations

import json
from pathlib import Path

from services.improvement.classify import classify_failure_record, token_jaccard
from services.improvement.contract import contract_for
from services.improvement.evaluate import COMMITTED_BASELINE, compare_candidate, parse_frac
from services.improvement.schema import (
    FailureRecord,
    required_contract_fields,
    required_failure_fields,
)
from services.workspace_files.chunk_retriever import chunk_retrieval_enabled
from tests.gate_m.measure import WS

FIXTURE = Path(__file__).resolve().parents[0] / "fixtures" / "gate_i1"


def _record(**overrides) -> FailureRecord:
    base = dict(
        failure_id="i1-synth",
        benchmark_id="synth",
        task_id="unused",
        failure_class="OTHER",
        user_query="Where must the goods be delivered?",
        expected_evidence="named delivery place is 12 Example Street",
        retrieved_evidence="pages=[7, 11] inspection of delivered goods",
        actual_answer="",
        expected_answer="12 Example Street",
        source_files=["agreement.pdf"],
        retrieval_mode="chunks",
        timestamp="2026-09-13T00:00:00+00:00",
        severity="medium",
        security_relevant=False,
        repeat_count=1,
        retrieved_empty=False,
    )
    base.update(overrides)
    return FailureRecord.from_mapping(base)


def test_failure_schema_fields_present():
    rec = _record()
    keys = set(rec.to_dict())
    for field in required_failure_fields():
        assert field in keys


def test_classifier_ignores_task_id_and_labels_lexical():
    a = classify_failure_record(_record(task_id="ZZZ"))
    b = classify_failure_record(_record(task_id="M09"))
    assert a == b == "LEXICAL_VARIATION"
    assert token_jaccard(
        "Where must the goods be delivered?",
        "named delivery place is 12 Example Street",
    ) < 0.2


def test_classifier_high_overlap_missing_span_is_ranking():
    rec = _record(
        user_query="named delivery place street",
        expected_evidence="named delivery place is 12 Example Street",
        retrieved_evidence="other matching tokens named delivery elsewhere",
    )
    assert classify_failure_record(rec) == "RANKING_FAILURE"


def test_classifier_span_present_wrong_answer_is_reasoning():
    rec = _record(
        expected_evidence="12 Example Street",
        retrieved_evidence="The named place is 12 Example Street on page 2",
        actual_answer="warehouse B",
        expected_answer="12 Example Street",
    )
    assert classify_failure_record(rec) == "MODEL_REASONING_FAILURE"


def test_lexical_contract_forbids_security_modules():
    contract = contract_for("LEXICAL_VARIATION")
    for field in required_contract_fields():
        assert getattr(contract, field)
    forbidden = {m.lower() for m in contract.forbidden_modules}
    for item in ("auth", "tenant isolation", "rls", "file ownership", "provider routing", "database schema"):
        assert item in forbidden
    assert contract.scope == "query_prep"
    assert contract.risk_level == "low"


def test_compare_rejects_correctness_drop():
    decision, regs, _lat = compare_candidate(
        summary={
            "answer_correctness": "47/50",
            "decisive_span_recall": "42/43",
            "unanswerable_precision": "7/7",
            "mrl": 1,
        },
        rows=[],
        control_latency={"mean_fts_latency_ms": 5.0},
        targeted={"ok": True},
        isolation={"ok": True},
        hardcoding={"ok": True},
    )
    assert decision == "REJECT"
    assert any("correctness" in r for r in regs)


def test_compare_passes_when_all_gates_hold():
    decision, regs, _lat = compare_candidate(
        summary={
            "answer_correctness": "49/50",
            "decisive_span_recall": "43/43",
            "unanswerable_precision": "7/7",
            "mrl": 0,
        },
        rows=[{"fts_latency_ms": 4.0, "used_files": [{"name": "ben_gold_supply_agreement.pdf"}]}],
        control_latency={"mean_fts_latency_ms": 5.0},
        targeted={"ok": True},
        isolation={"ok": True},
        hardcoding={"ok": True},
    )
    assert decision == "PASS"
    assert regs == []


def test_committed_baseline_matches_gate_p():
    assert COMMITTED_BASELINE["answer_correctness"] == "48/50"
    assert COMMITTED_BASELINE["decisive_span_recall"] == "42/43"
    assert COMMITTED_BASELINE["unanswerable_precision"] == "7/7"
    assert parse_frac("42/43") == (42, 43)


def test_chunk_fts_remains_off_in_default_env():
    assert chunk_retrieval_enabled(WS) is False


def test_fixture_readme_exists():
    assert (FIXTURE / "README.md").is_file()


def test_saved_result_is_one_of_allowed_statuses():
    path = FIXTURE / "i1_result.json"
    if not path.is_file():
        return
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload.get("gate_status") in {"PASS", "REJECT", "NEEDS_REVIEW"}
    assert payload.get("merged") is False
    assert payload.get("deployed") is False
    assert payload.get("production_changed") is False
    rec = payload.get("failure_record") or {}
    for field in required_failure_fields():
        assert field in rec
