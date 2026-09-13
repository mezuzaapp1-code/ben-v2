"""Replay measured I1/I2 fixtures into I3 Failure Records. Not product hardcoding."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from services.improvement.records import CandidateMeasurement, FailureRecord

_I1_FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "fixtures"
    / "gate_i3"
    / "i1_lexical_failure.json"
)

I1_FTS_BASELINE = {
    "answer_correctness": "48/50",
    "decisive_span_recall": "42/43",
    "unanswerable_precision": "7/7",
    "mrl": 1,
}

I1_PASSING_CANDIDATE = CandidateMeasurement(
    candidate_id="C1_PREFIX",
    change_summary=(
        "Append expander-generated stem:* atoms to the existing simple OR tsquery. "
        "Query-prep only."
    ),
    components_touched=["retrieval query preparation"],
    target_improved=True,
    benchmark_result={
        "answer_correctness": "49/50",
        "decisive_span_recall": "43/43",
        "unanswerable_precision": "7/7",
        "mrl": 0,
    },
    isolation_ok=True,
    security_invariants_ok=True,
    latency_within_budget=True,
    cost_within_budget=True,
    unrelated_behavior_changed=False,
    hardcoding=False,
    http_5xx=0,
    notes="I2 production canary kept expand scoped to the existing FTS allowlist.",
)

I1_REGRESSION_CANDIDATE = CandidateMeasurement(
    candidate_id="C_REGRESSION",
    change_summary="Local lexical hit with isolation or correctness regression.",
    components_touched=["retrieval query preparation"],
    target_improved=True,
    benchmark_result={
        "answer_correctness": "47/50",
        "decisive_span_recall": "42/43",
        "unanswerable_precision": "6/7",
        "mrl": 1,
    },
    isolation_ok=False,
    security_invariants_ok=True,
    latency_within_budget=True,
    cost_within_budget=True,
    unrelated_behavior_changed=True,
    hardcoding=False,
    http_5xx=0,
    notes="Fail-closed: local improvement with material regression elsewhere.",
)

I1_SCOPE_VIOLATION_CANDIDATE = CandidateMeasurement(
    candidate_id="C_SCOPE",
    change_summary="Touches auth / tenant isolation, which the Fix Contract forbids.",
    components_touched=["auth", "tenant isolation"],
    target_improved=True,
    benchmark_result={
        "answer_correctness": "49/50",
        "decisive_span_recall": "43/43",
        "unanswerable_precision": "7/7",
        "mrl": 0,
    },
    isolation_ok=True,
    security_invariants_ok=True,
    latency_within_budget=True,
    cost_within_budget=True,
    unrelated_behavior_changed=False,
    hardcoding=False,
    http_5xx=0,
    notes="Scope violation must REJECT/BLOCK even if scores look better.",
)

I1_HARDCODING_CANDIDATE = CandidateMeasurement(
    candidate_id="C_HARDCODE",
    change_summary="Special-cases one gold row instead of a generic expander.",
    components_touched=["retrieval query preparation"],
    target_improved=True,
    benchmark_result={
        "answer_correctness": "49/50",
        "decisive_span_recall": "43/43",
        "unanswerable_precision": "7/7",
        "mrl": 0,
    },
    isolation_ok=True,
    security_invariants_ok=True,
    latency_within_budget=True,
    cost_within_budget=True,
    unrelated_behavior_changed=False,
    hardcoding=True,
    http_5xx=0,
    notes="Benchmark-specific hardcoding is rejected.",
)

WEAK_CANDIDATE = CandidateMeasurement(
    candidate_id="C_WEAK",
    change_summary="Does not recover the measured lexical miss.",
    components_touched=["retrieval query preparation"],
    target_improved=False,
    benchmark_result=dict(I1_FTS_BASELINE),
    isolation_ok=True,
    security_invariants_ok=True,
    latency_within_budget=True,
    cost_within_budget=True,
)


def load_i1_lexical_failure(path: Path | None = None) -> FailureRecord:
    payload = json.loads((path or _I1_FIXTURE).read_text(encoding="utf-8"))
    return failure_from_i1_payload(payload)


def failure_from_i1_payload(payload: Mapping[str, Any]) -> FailureRecord:
    data = dict(payload)
    data.setdefault("source", data.get("benchmark_id") or "gate_i1_fixture")
    data.setdefault("task_class", "retrieval")
    data.setdefault("component", "retrieval query preparation")
    data.setdefault("environment", "isolated")
    data.setdefault("user_visible_impact", "missing_decisive_span")
    data.setdefault("benchmark_reference", data.get("benchmark_id"))
    data.setdefault("status", "open")
    data.setdefault("input_reference", data.get("user_query"))
    data.setdefault("observed_evidence", data.get("retrieved_evidence"))
    data.setdefault("expected_behavior", data.get("expected_answer"))
    data.setdefault("actual_behavior", data.get("actual_answer"))
    return FailureRecord.from_mapping(data)
