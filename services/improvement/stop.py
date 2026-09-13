"""Adaptive stopping for Improvement Runs. No endless repair loops."""

from __future__ import annotations

from services.improvement.records import CandidateResult, FailureRecord, FixContract
from services.improvement.taxonomy import AUTO_EVALUABLE_CLASSES, NO_AUTO_PROMOTE_RISKS

DEFAULT_MAX_CANDIDATES = 3


def stop_reason(
    record: FailureRecord,
    contract: FixContract,
    results: list[CandidateResult],
    *,
    architectural: bool = False,
) -> str | None:
    """Return a stop reason, or None if another candidate may be evaluated."""
    if not record.evidence_sufficient():
        return "insufficient_evidence"
    if record.security_relevant:
        return "security_relevant"
    if contract.risk_level in NO_AUTO_PROMOTE_RISKS:
        return "risk_blocks_auto_pass"
    if record.failure_class not in AUTO_EVALUABLE_CLASSES:
        return "class_not_auto_evaluable"
    if architectural:
        return "architectural_change_required"
    if any(r.decision == "PASS" for r in results):
        return "candidate_satisfied_contract"
    limit = int(contract.max_candidates or DEFAULT_MAX_CANDIDATES)
    if len(results) >= limit:
        return "max_candidates_exhausted"
    if results and any("scope_violation" in (r.regressions or []) for r in results[-1:]):
        return "scope_exceeded"
    return None


def terminal_decision(reason: str | None, results: list[CandidateResult]) -> str:
    if reason == "insufficient_evidence":
        return "BLOCKED"
    if reason in {
        "security_relevant",
        "risk_blocks_auto_pass",
        "class_not_auto_evaluable",
        "architectural_change_required",
    }:
        return "NEEDS_REVIEW"
    if reason == "scope_exceeded":
        return "REJECT"
    if any(r.decision == "PASS" for r in results):
        return "PASS"
    if reason == "max_candidates_exhausted":
        return "REJECT"
    if reason == "candidate_satisfied_contract":
        return "PASS"
    return "REJECT" if results else "BLOCKED"
