"""Gate I3 — reusable Improvement Loop framework (internal, no production authority).

Proves:
1. I1 lexical failure replay → valid Failure Record
2. Classification → appropriate Fix Contract
3. Passing candidate → PASS
4. Regression → REJECT
5. Scope violation → REJECT/BLOCKED
6. Security-relevant → NEEDS_REVIEW
7. Candidate limit stops the loop
8. Insufficient evidence stops safely
9. No merge/deploy/production mutation capability
10. Existing expander tests remain green (run alongside this module)
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from services.improvement.authority import (
    FORBIDDEN_ACTIONS,
    allowed_outputs,
    assert_no_production_authority,
    has_production_authority,
    scan_package_for_mutation_hooks,
)
from services.improvement.classify import classify_and_apply, classify_failure_record, token_jaccard
from services.improvement.cluster import assign_cluster, should_open_engineering
from services.improvement.contract import contract_for
from services.improvement.evaluate import DEFAULT_BASELINE, evaluate_candidate
from services.improvement.metrics import reset as reset_metrics, snapshot
from services.improvement.priority import priority_score
from services.improvement.records import (
    REQUIRED_CONTRACT_FIELDS,
    REQUIRED_FAILURE_FIELDS,
    CandidateMeasurement,
    FailureRecord,
    FixContract,
)
from services.improvement.replay import (
    I1_HARDCODING_CANDIDATE,
    I1_PASSING_CANDIDATE,
    I1_REGRESSION_CANDIDATE,
    I1_SCOPE_VIOLATION_CANDIDATE,
    WEAK_CANDIDATE,
    failure_from_i1_payload,
    load_i1_lexical_failure,
)
from services.improvement.run import run_improvement, write_improvement_run
from services.improvement.stop import DEFAULT_MAX_CANDIDATES
from services.improvement.taxonomy import (
    FAILURE_CLASSES,
    NO_AUTO_PROMOTE_RISKS,
    RISK_POLICY,
)
from services.workspace_files.chunk_retriever import chunk_retrieval_enabled
from services.workspace_files.query_expansion import ALLOWED_MODULES, FORBIDDEN_MODULES

FIXTURE_DIR = Path(__file__).resolve().parents[0] / "fixtures" / "gate_i3"
PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "services" / "improvement"
EXPAND_SRC = (
    Path(__file__).resolve().parents[1]
    / "services"
    / "workspace_files"
    / "query_expansion.py"
)


def test_i1_replay_produces_valid_failure_record():
    rec = load_i1_lexical_failure()
    keys = set(rec.to_dict())
    for field in REQUIRED_FAILURE_FIELDS:
        assert field in keys
    for extra in ("retrieval_mode", "model", "tool"):
        assert extra in keys
    assert rec.failure_id == "i1-replay-lexical-fts"
    assert rec.input_reference
    assert rec.expected_evidence
    assert rec.security_relevant is False
    payload = json.loads((FIXTURE_DIR / "i1_lexical_failure.json").read_text(encoding="utf-8"))
    assert payload.get("task_id") == "M09"
    a = classify_failure_record(failure_from_i1_payload({**payload, "task_id": "M09"}))
    b = classify_failure_record(failure_from_i1_payload({**payload, "task_id": "ZZZ"}))
    assert a == b == "LEXICAL_VARIATION"
    assert token_jaccard(rec.input_reference, rec.expected_evidence) < 0.20
    assert "task_id" not in rec.to_dict()


def test_classification_produces_lexical_fix_contract():
    rec = classify_and_apply(load_i1_lexical_failure())
    contract = contract_for(rec, fix_id="fix-i1-replay")
    dumped = contract.to_dict()
    for field in REQUIRED_CONTRACT_FIELDS:
        assert field in dumped
        assert dumped[field] not in (None, "")
    assert contract.human_review_required is True
    assert contract.failure_class == "LEXICAL_VARIATION"
    assert contract.risk_level == "LOW"
    assert contract.scope == "query_prep"
    assert set(ALLOWED_MODULES).issubset(set(contract.allowed_components))
    forbidden = {c.casefold() for c in contract.forbidden_components}
    for item in ("auth", "tenant isolation", "rls", "secrets", "payments"):
        assert item in forbidden
    assert {m.casefold() for m in FORBIDDEN_MODULES}.issubset(forbidden)
    assert contract.max_candidates == DEFAULT_MAX_CANDIDATES
    assert contract.rollback_strategy


def test_passing_candidate_becomes_pass(tmp_path):
    rec = load_i1_lexical_failure()
    run = run_improvement(rec, [I1_PASSING_CANDIDATE], run_id="ir-i3-i1-replay")
    assert run.decision == "PASS"
    assert run.candidates[0]["candidate_id"] == "C1_PREFIX"
    assert run.fix_contract["human_review_required"] is True
    assert run.production_authority_used is False
    assert run.risk_assessment["auto_promote"] is False
    assert "human review" in run.human_action_required.casefold()
    prov = run.provenance
    for key in (
        "what_failed",
        "how_we_know",
        "diagnosis_evidence",
        "candidate_changed",
        "tests_ran",
        "what_improved",
        "what_regressed",
        "what_was_not_tested",
        "production_authority_still_required",
    ):
        assert key in prov
    artifact = run.to_dict()
    for key in (
        "run_id",
        "failure_cluster",
        "classification",
        "fix_contract",
        "candidates",
        "test_results",
        "baseline_comparison",
        "risk_assessment",
        "decision",
        "human_action_required",
    ):
        assert key in artifact
    dest = write_improvement_run(run, tmp_path / "improvement_run.json")
    written = json.loads(dest.read_text(encoding="utf-8"))
    assert written["decision"] == "PASS"


def test_regression_causes_reject():
    rec = load_i1_lexical_failure()
    run = run_improvement(rec, [I1_REGRESSION_CANDIDATE])
    assert run.decision == "REJECT"
    reasons = " ".join(run.candidates[0]["regressions"])
    assert "isolation_invariant" in reasons or "correctness" in reasons


def test_scope_violation_causes_reject_or_blocked():
    rec = load_i1_lexical_failure()
    run = run_improvement(rec, [I1_SCOPE_VIOLATION_CANDIDATE])
    assert run.decision in {"REJECT", "BLOCKED"}
    assert "scope_violation" in run.candidates[0]["regressions"]
    assert run.stop_reason == "scope_exceeded"


def test_security_relevant_escalates_to_needs_review():
    rec = FailureRecord.from_mapping(
        {
            "failure_id": "sec-1",
            "source": "synthetic",
            "task_class": "auth",
            "user_query": "show invoices",
            "expected_evidence": "caller tenant invoices only",
            "actual_answer": "query leaked another tenant row",
            "component": "rls",
            "security_relevant": True,
            "severity": "critical",
        }
    )
    assert classify_failure_record(rec) == "PERMISSION_FAILURE"
    contract = contract_for(classify_and_apply(rec))
    assert contract.risk_level == "CRITICAL"
    assert contract.human_review_required is True
    run = run_improvement(rec, [I1_PASSING_CANDIDATE])
    assert run.decision == "NEEDS_REVIEW"
    assert run.candidates == []
    assert run.stop_reason == "security_relevant"


def test_candidate_limit_stops_the_loop():
    rec = load_i1_lexical_failure()
    run = run_improvement(
        rec,
        [WEAK_CANDIDATE, WEAK_CANDIDATE, WEAK_CANDIDATE, I1_PASSING_CANDIDATE],
    )
    assert len(run.candidates) == 3
    assert run.decision == "REJECT"
    assert run.stop_reason == "max_candidates_exhausted"


def test_insufficient_evidence_stops_safely():
    rec = FailureRecord.from_mapping(
        {
            "failure_id": "thin",
            "source": "prod",
            "component": "unknown",
        }
    )
    run = run_improvement(rec, [I1_PASSING_CANDIDATE])
    assert run.decision == "BLOCKED"
    assert run.stop_reason == "insufficient_evidence"
    assert run.candidates == []


def test_no_merge_deploy_production_mutation_capability():
    assert assert_no_production_authority() is True
    assert scan_package_for_mutation_hooks() == []
    for action in FORBIDDEN_ACTIONS:
        assert has_production_authority(action) is False
    outputs = allowed_outputs()
    assert "improvement_run.json" in outputs
    assert "deploy" not in outputs
    src = "\n".join(p.read_text(encoding="utf-8") for p in PACKAGE_ROOT.glob("*.py"))
    for name in (
        "def merge_pull_request",
        "def deploy_production",
        "def set_production_flag",
        "def run_alembic",
        "def disable_rls",
        "def grant_permission",
        "def railway_variable_set",
    ):
        assert name not in src


def test_human_review_required_cannot_be_disabled():
    forced = FixContract.from_mapping(
        {
            "fix_id": "x",
            "failure_class": "LEXICAL_VARIATION",
            "hypothesis": "h",
            "scope": "query_prep",
            "risk_level": "LOW",
            "expected_improvement": "e",
            "allowed_components": ["retrieval query preparation"],
            "forbidden_components": ["auth"],
            "security_invariants": ["no rls change"],
            "data_invariants": ["no extra persist"],
            "performance_budget": "200ms",
            "required_targeted_tests": ["span"],
            "required_regression_tests": ["isolation"],
            "required_benchmarks": ["gate m"],
            "rollback_strategy": "do not merge",
            "human_review_required": False,
        }
    )
    assert forced.human_review_required is True


def test_high_critical_never_auto_promote():
    rec = FailureRecord.from_mapping(
        {
            "failure_id": "tool-fail",
            "source": "agent",
            "user_query": "send invoice",
            "expected_evidence": "draft invoice id",
            "actual_answer": "tool posted a payment",
            "component": "tools.payment",
            "notes": "tool_failure posted payment",
        }
    )
    cl = classify_and_apply(rec)
    assert cl.failure_class == "TOOL_FAILURE"
    contract = contract_for(cl)
    assert contract.risk_level in NO_AUTO_PROMOTE_RISKS
    assert RISK_POLICY[contract.risk_level]["auto_promote"] is False
    run = run_improvement(rec, [I1_PASSING_CANDIDATE])
    assert run.decision == "NEEDS_REVIEW"
    assert run.risk_assessment["auto_promote"] is False
    assert run.candidates == []


def test_clustering_does_not_open_work_on_one_off():
    rec = classify_and_apply(load_i1_lexical_failure())
    clusters: list = []
    c1 = assign_cluster(rec, clusters)
    assert c1.pattern_kind == "one-off"
    assert should_open_engineering(c1) is False
    rec2 = classify_and_apply(load_i1_lexical_failure())
    rec2.failure_id = "i1-replay-2"
    rec2.timestamp = "2026-09-13T09:00:00+00:00"
    c2 = assign_cluster(rec2, clusters)
    assert c2.cluster_id == c1.cluster_id
    assert c2.frequency == 2
    assert c2.pattern_kind == "repeated"
    assert should_open_engineering(c2) is True


def test_priority_not_benchmark_score_only():
    rec = classify_and_apply(load_i1_lexical_failure())
    contract = contract_for(rec)
    cluster = assign_cluster(rec, [])
    scored = priority_score(rec, cluster, contract)
    assert scored["benchmark_score_used"] is False
    assert scored["score"] > 0
    assert "user_impact" in scored["inputs"]
    assert "frequency" in scored["inputs"]


def test_hardcoding_rejected():
    rec = classify_and_apply(load_i1_lexical_failure())
    contract = contract_for(rec)
    result = evaluate_candidate(I1_HARDCODING_CANDIDATE, contract)
    assert result.decision == "REJECT"
    assert "benchmark_specific_hardcoding" in result.regressions


def test_metrics_and_baseline_shape():
    reset_metrics()
    rec = load_i1_lexical_failure()
    run_improvement(rec, [I1_PASSING_CANDIDATE], run_id="ir-metrics")
    snap = snapshot()
    assert snap["failures_observed"] >= 1
    assert snap["failures_classified"] >= 1
    assert snap["candidates_evaluated"] >= 1
    assert snap["decision_pass"] >= 1
    assert snap["production_incidents_attributable_to_promoted_fixes"] == 0
    assert snap["mean_evaluation_duration_ms"] is not None
    assert DEFAULT_BASELINE["answer_correctness"] == "48/50"


def test_model_reasoning_failure_not_auto_fixed():
    rec = FailureRecord.from_mapping(
        {
            "failure_id": "reasoning-out-of-scope",
            "source": "GATE_M",
            "user_query": "policy refund window",
            "expected_evidence": "refund within 30 days",
            "retrieved_evidence": "Customers may refund within 30 days per policy.md",
            "expected_answer": "30 days",
            "actual_answer": "90 days",
            "component": "generator",
            "retrieval_mode": "chunks",
        }
    )
    assert classify_failure_record(rec) == "MODEL_REASONING_FAILURE"
    run = run_improvement(rec, [I1_PASSING_CANDIDATE])
    assert run.decision == "NEEDS_REVIEW"
    assert run.candidates == []
    assert run.stop_reason == "class_not_auto_evaluable"


def test_taxonomy_is_extensible_and_not_all_auto_fixable():
    required = {
        "LEXICAL_VARIATION",
        "RANKING_FAILURE",
        "CONTEXT_LOSS",
        "EXCEPTION_MISSED",
        "MULTI_HOP",
        "MODEL_REASONING_FAILURE",
        "TABLE_LAYOUT",
        "WRONG_SOURCE",
        "CROSS_FILE",
        "ROUTING_FAILURE",
        "TOOL_FAILURE",
        "STRUCTURED_OUTPUT_FAILURE",
        "BUSINESS_MEMORY_FAILURE",
        "AGENT_ACTION_FAILURE",
        "PERMISSION_FAILURE",
        "OTHER",
    }
    assert required.issubset(set(FAILURE_CLASSES))
    contract = contract_for("CROSS_FILE")
    assert contract.scope == "architecture"
    assert contract.allowed_components == []
    assert contract.human_review_required is True


def test_framework_does_not_enable_production_fts_or_expand():
    assert chunk_retrieval_enabled(uuid.uuid4()) is False
    body = EXPAND_SRC.read_text(encoding="utf-8").casefold()
    for needle in ("m09", "hamelacha", "ben-gold"):
        assert needle not in body


def test_weak_candidate_does_not_pass_baseline_hold():
    rec = classify_and_apply(load_i1_lexical_failure())
    result = evaluate_candidate(
        CandidateMeasurement(
            candidate_id="cost",
            change_summary="over budget",
            components_touched=["retrieval query preparation"],
            target_improved=True,
            benchmark_result={
                "answer_correctness": "49/50",
                "decisive_span_recall": "43/43",
                "unanswerable_precision": "7/7",
            },
            cost_within_budget=False,
        ),
        contract_for(rec),
    )
    assert result.decision == "REJECT"
    assert "cost_budget" in result.regressions
