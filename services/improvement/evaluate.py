"""Fail-closed candidate evaluation against a Fix Contract and baseline."""

from __future__ import annotations

import re
from typing import Any, Mapping

from services.improvement.records import CandidateMeasurement, CandidateResult, FixContract
from services.improvement.taxonomy import ALWAYS_FORBIDDEN_COMPONENTS

_FRAC = re.compile(r"^(\d+)\s*/\s*(\d+)$")

DEFAULT_BASELINE = {
    "answer_correctness": "48/50",
    "decisive_span_recall": "42/43",
    "unanswerable_precision": "7/7",
}


def parse_frac(value: Any) -> tuple[int, int] | None:
    if value is None:
        return None
    m = _FRAC.match(str(value).strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def _frac_decreased(candidate: Any, baseline: Any) -> bool:
    c = parse_frac(candidate)
    b = parse_frac(baseline)
    if not c or not b:
        return False
    if c[1] != b[1]:
        return True
    return c[0] < b[0]


def _scope_ok(measurement: CandidateMeasurement, contract: FixContract) -> bool:
    allowed = {c.casefold() for c in contract.allowed_components}
    forbidden = {c.casefold() for c in contract.forbidden_components} | {
        c.casefold() for c in ALWAYS_FORBIDDEN_COMPONENTS
    }
    if not measurement.components_touched:
        return False
    for component in measurement.components_touched:
        token = component.casefold()
        if token in forbidden:
            return False
        if allowed and token not in allowed:
            return False
    return True


def evaluate_candidate(
    measurement: CandidateMeasurement,
    contract: FixContract,
    *,
    baseline: Mapping[str, Any] | None = None,
) -> CandidateResult:
    base = dict(DEFAULT_BASELINE)
    base.update(dict(baseline or {}))
    regressions: list[str] = []
    bench = measurement.benchmark_result or {}

    if not _scope_ok(measurement, contract):
        regressions.append("scope_violation")
    if measurement.hardcoding:
        regressions.append("benchmark_specific_hardcoding")
    if not measurement.target_improved:
        regressions.append("target_failure_not_improved")
    if not measurement.isolation_ok:
        regressions.append("isolation_invariant")
    if not measurement.security_invariants_ok:
        regressions.append("security_invariant")
    if not measurement.latency_within_budget:
        regressions.append("latency_budget")
    if measurement.cost_within_budget is False:
        regressions.append("cost_budget")
    if measurement.unrelated_behavior_changed:
        regressions.append("unrelated_behavior_change")
    if measurement.http_5xx:
        regressions.append(f"http_5xx:{measurement.http_5xx}")

    if _frac_decreased(bench.get("answer_correctness"), base.get("answer_correctness")):
        regressions.append(
            f"correctness {bench.get('answer_correctness')} < {base.get('answer_correctness')}"
        )
    if _frac_decreased(bench.get("decisive_span_recall"), base.get("decisive_span_recall")):
        regressions.append(
            f"recall {bench.get('decisive_span_recall')} < {base.get('decisive_span_recall')}"
        )
    unans = bench.get("unanswerable_precision")
    if unans is not None and str(unans) != str(base.get("unanswerable_precision")):
        if _frac_decreased(unans, base.get("unanswerable_precision")) or str(unans) != str(
            base.get("unanswerable_precision")
        ):
            if parse_frac(unans) and parse_frac(base.get("unanswerable_precision")):
                c, b = parse_frac(unans), parse_frac(base.get("unanswerable_precision"))
                if c and b and (c[0] < b[0] or c[1] != b[1]):
                    regressions.append(f"unanswerable {unans} != {base.get('unanswerable_precision')}")

    if "scope_violation" in regressions:
        decision = "REJECT"
    else:
        decision = "PASS" if not regressions else "REJECT"

    return CandidateResult(
        candidate_id=measurement.candidate_id,
        change_summary=measurement.change_summary,
        benchmark_result=dict(bench),
        latency_delta={"within_budget": measurement.latency_within_budget},
        regressions=regressions,
        decision=decision,
        notes=measurement.notes,
    )
