"""Build a Fix Contract from a classified failure. Always requires human review."""

from __future__ import annotations

from services.improvement.records import FixContract
from services.improvement.taxonomy import (
    ALWAYS_FORBIDDEN_COMPONENTS,
    AUTO_EVALUABLE_CLASSES,
    CLASS_DEFAULT_RISK,
    NO_AUTO_PROMOTE_RISKS,
)
from services.workspace_files.query_expansion import ALLOWED_MODULES, FORBIDDEN_MODULES

_DEFAULT_STOP = [
    "one candidate satisfies the full Fix Contract",
    "maximum candidates fail",
    "required change exceeds allowed scope",
    "failure becomes security relevant",
    "evidence is insufficient",
    "architectural change is required",
]

_SECURITY = [
    "same org/workspace/file filtering",
    "no broader unauthorized source set",
    "no auth/RLS/tenant-isolation change",
    "no secret or permission change",
]

_DATA = [
    "do not persist unnecessary customer content",
    "do not broaden authorized source set",
]

_ROLLBACK = (
    "Do not merge. Do not deploy. Do not change production flags. "
    "Leave candidate artifacts isolated. Rollback is unset any unmerged flag."
)


def contract_for(record_or_class, *, fix_id: str = "fix-unspecified") -> FixContract:
    if hasattr(record_or_class, "failure_class"):
        cls = str(record_or_class.failure_class or "OTHER").upper()
        security = bool(getattr(record_or_class, "security_relevant", False))
    else:
        cls = str(record_or_class or "OTHER").upper()
        security = False
    risk = "CRITICAL" if security else CLASS_DEFAULT_RISK.get(cls, "MEDIUM")
    allowed = (
        sorted(ALLOWED_MODULES)
        if cls in AUTO_EVALUABLE_CLASSES and risk not in NO_AUTO_PROMOTE_RISKS
        else []
    )
    forbidden = sorted(set(ALWAYS_FORBIDDEN_COMPONENTS) | set(FORBIDDEN_MODULES))
    if cls == "LEXICAL_VARIATION":
        hypothesis = (
            "Bounded query expansion over sanitized tokens may recover "
            "inflectional variants on existing simple FTS without ranking or schema changes."
        )
        expected = (
            "Lexical-variation misses recover the decisive span; correctness, "
            "decisive recall, and unanswerable precision do not drop."
        )
        scope = "query_prep"
        perf = "FTS latency remains below 200ms timeout and within 2× control mean."
        targeted = ["targeted span recovery for the measured query"]
        regression = [
            "retrieval isolation tests (cross-org, cross-workspace)",
            "no unanswerable precision regression",
        ]
        benchmarks = ["Gate M extractive benchmark", "Gate P BEN_EXISTING_FTS baseline"]
    elif cls == "RANKING_FAILURE":
        hypothesis = "Decisive span matches query tokens but ranks below the selection cap."
        expected = "Decisive span selected among existing matches."
        scope = "ranking"
        perf = "FTS latency remains below 200ms."
        targeted = ["decisive span among existing FTS hits"]
        regression = ["Gate M/P regression", "isolation tests"]
        benchmarks = ["Gate P BEN_EXISTING_FTS baseline"]
        allowed = ["retrieval ranking"]
    else:
        hypothesis = (
            f"{cls} is not an auto-evaluable query-prep class in this framework. "
            "Human-reviewed architecture decision required."
        )
        expected = "Human-reviewed diagnosis; no automatic candidate promotion."
        scope = "architecture"
        perf = "no production latency change permitted without review"
        targeted = ["insufficient for automatic repair"]
        regression = ["do not run mutating candidates"]
        benchmarks = []
        allowed = []

    return FixContract(
        fix_id=fix_id,
        failure_class=cls,
        hypothesis=hypothesis,
        scope=scope,
        risk_level=risk,
        expected_improvement=expected,
        allowed_components=allowed,
        forbidden_components=forbidden,
        security_invariants=list(_SECURITY),
        data_invariants=list(_DATA),
        performance_budget=perf,
        required_targeted_tests=targeted,
        required_regression_tests=regression,
        required_benchmarks=benchmarks,
        rollback_strategy=_ROLLBACK,
        max_candidates=3,
        stop_conditions=list(_DEFAULT_STOP),
        human_review_required=True,
    )
