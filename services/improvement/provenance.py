"""Auditable provenance for an Improvement Run. No unexplained self-modification."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from services.improvement.records import CandidateResult, FailureRecord, FixContract


def provenance_answers(
    record: FailureRecord,
    contract: FixContract,
    results: Sequence[CandidateResult],
    *,
    decision: str,
    tests_run: Sequence[str] | None = None,
    not_tested: Sequence[str] | None = None,
) -> dict[str, Any]:
    passed = next((r for r in results if r.decision == "PASS"), None)
    failed = [r for r in results if r.decision != "PASS"]
    return {
        "what_failed": {
            "failure_id": record.failure_id,
            "failure_class": record.failure_class,
            "component": record.component,
        },
        "how_we_know": {
            "source": record.source,
            "benchmark_reference": record.benchmark_reference,
            "retrieval_mode": record.retrieval_mode,
            "prior_label": record.prior_label,
        },
        "diagnosis_evidence": {
            "input_reference": record.input_reference,
            "expected_evidence": record.expected_evidence,
            "observed_evidence": record.observed_evidence,
            "classified_as": record.failure_class,
        },
        "candidate_changed": None
        if passed is None
        else {
            "candidate_id": passed.candidate_id,
            "change_summary": passed.change_summary,
        },
        "tests_ran": list(tests_run or contract.required_regression_tests),
        "what_improved": None
        if passed is None
        else passed.benchmark_result,
        "what_regressed": [r.regressions for r in failed],
        "what_was_not_tested": list(
            not_tested
            or [
                "live production traffic beyond the measured canary fixtures",
                "model-reasoning failures (out of scope)",
            ]
        ),
        "production_authority_still_required": [
            "human review",
            "merge",
            "deploy",
            "production flags",
            "rollout",
        ],
        "decision": decision,
        "human_review_required": contract.human_review_required,
    }


def baseline_comparison(
    results: Sequence[CandidateResult],
    baseline: Mapping[str, Any],
) -> dict[str, Any]:
    passed = next((r for r in results if r.decision == "PASS"), None)
    return {
        "baseline": dict(baseline),
        "candidate": None if passed is None else passed.benchmark_result,
    }
