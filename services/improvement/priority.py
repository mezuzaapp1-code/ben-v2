"""Priority for which failure clusters to evaluate. Not benchmark-score-only."""

from __future__ import annotations

from services.improvement.cluster import FailureCluster
from services.improvement.records import FailureRecord, FixContract
from services.improvement.taxonomy import CLASS_DEFAULT_RISK

_SEVERITY = {"low": 1, "medium": 2, "high": 3, "critical": 4}
_RISK = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}
_PATTERN = {"one-off": 1, "repeated": 2, "systemic": 3}


def priority_score(
    record: FailureRecord,
    cluster: FailureCluster,
    contract: FixContract,
    *,
    diagnosis_confidence: float = 0.7,
    expected_improvement: float = 0.5,
    estimated_cost: float = 1.0,
) -> dict:
    impact = _SEVERITY.get(record.severity, 2)
    if record.user_visible_impact and record.user_visible_impact != "none":
        impact += 1
    frequency = min(cluster.frequency, 5)
    severity = _SEVERITY.get(cluster.severity, 2)
    confidence = max(0.0, min(1.0, diagnosis_confidence))
    improve = max(0.0, min(1.0, expected_improvement))
    risk = _RISK.get(contract.risk_level or CLASS_DEFAULT_RISK.get(record.failure_class, "MEDIUM"), 2)
    cost = max(0.25, float(estimated_cost))
    # User impact / frequency / severity first; benchmark score is not an input.
    numer = (3 * impact) + (2 * frequency) + (2 * severity) + (2 * confidence) + improve
    denom = (2 * risk) + cost
    score = round(numer / denom, 3)
    return {
        "score": score,
        "inputs": {
            "user_impact": impact,
            "frequency": frequency,
            "severity": severity,
            "diagnosis_confidence": confidence,
            "expected_improvement": improve,
            "implementation_risk": risk,
            "estimated_cost": cost,
            "pattern_kind": cluster.pattern_kind,
        },
        "benchmark_score_used": False,
    }
