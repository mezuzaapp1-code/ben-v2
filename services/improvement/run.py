"""Orchestrate one Improvement Run from a measured failure + candidate measurements.

Does not patch code, merge, deploy, or flip production flags.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Mapping, Sequence

from services.improvement.authority import has_production_authority
from services.improvement.classify import classify_and_apply
from services.improvement.cluster import assign_cluster
from services.improvement.contract import contract_for
from services.improvement.evaluate import DEFAULT_BASELINE, evaluate_candidate
from services.improvement.metrics import incr, timed
from services.improvement.priority import priority_score
from services.improvement.provenance import baseline_comparison, provenance_answers
from services.improvement.records import (
    CandidateMeasurement,
    CandidateResult,
    FailureRecord,
    ImprovementRun,
)
from services.improvement.stop import stop_reason, terminal_decision
from services.improvement.taxonomy import NO_AUTO_PROMOTE_RISKS


def run_improvement(
    failure: FailureRecord | Mapping[str, Any],
    candidates: Sequence[CandidateMeasurement | Mapping[str, Any]] = (),
    *,
    baseline: Mapping[str, Any] | None = None,
    run_id: str | None = None,
    existing_clusters: list | None = None,
) -> ImprovementRun:
    with timed():
        incr("failures_observed")
        record = (
            failure
            if isinstance(failure, FailureRecord)
            else FailureRecord.from_mapping(failure)
        )
        clusters = existing_clusters if existing_clusters is not None else []
        results: list[CandidateResult] = []
        measurements = [
            c if isinstance(c, CandidateMeasurement) else CandidateMeasurement.from_mapping(c)
            for c in candidates
        ]
        base = dict(DEFAULT_BASELINE)
        base.update(dict(baseline or {}))

        architectural = False
        if not record.evidence_sufficient():
            reason = "insufficient_evidence"
            record.failure_class = record.failure_class or "OTHER"
        else:
            classify_and_apply(record)
            incr("failures_classified")
            reason = None

        contract = contract_for(record, fix_id=f"fix-{record.failure_id}")
        known_clusters = {c.cluster_id for c in clusters}
        cluster = assign_cluster(record, clusters)
        if cluster.cluster_id not in known_clusters:
            incr("clusters_created")
        priority = priority_score(record, cluster, contract)

        if reason is None:
            reason = stop_reason(record, contract, results, architectural=architectural)

        if reason is None:
            limit = int(contract.max_candidates or 3)
            for measurement in measurements[:limit]:
                incr("candidates_evaluated")
                result = evaluate_candidate(measurement, contract, baseline=base)
                results.append(result)
                if result.regressions:
                    incr("regressions_caught")
                reason = stop_reason(record, contract, results)
                if reason:
                    break

        decision = terminal_decision(reason, results)
        if decision == "PASS" and contract.risk_level in NO_AUTO_PROMOTE_RISKS:
            decision = "NEEDS_REVIEW"
            reason = "risk_blocks_auto_pass"
        incr(f"decision_{decision.lower()}")

        human = (
            "No production action. Human review required before any merge, deploy, "
            "flag change, or rollout. This run has no production authority "
            f"(merge={has_production_authority('merge_code')})."
        )
        tests_run = list(contract.required_targeted_tests) + list(contract.required_regression_tests)
        run = ImprovementRun(
            run_id=run_id or f"ir-{uuid.uuid4()}",
            failure_cluster=cluster.to_dict(),
            classification=record.failure_class,
            fix_contract=contract.to_dict(),
            candidates=[r.to_dict() for r in results],
            test_results={"evaluated": [r.candidate_id for r in results], "tests_declared": tests_run},
            baseline_comparison=baseline_comparison(results, base),
            risk_assessment={
                "risk_level": contract.risk_level,
                "human_review_required": True,
                "auto_promote": False,
                "security_relevant": record.security_relevant,
            },
            decision=decision,
            human_action_required=human,
            provenance=provenance_answers(
                record,
                contract,
                results,
                decision=decision,
                tests_run=tests_run,
            ),
            priority=priority,
            stop_reason=reason or "complete",
            production_authority_used=False,
        )
        return run


def write_improvement_run(run: ImprovementRun, path: str | Path) -> Path:
    """Write an auditable improvement_run.json. Does not merge or deploy."""
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        json.dumps(run.to_dict(), indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    return dest
