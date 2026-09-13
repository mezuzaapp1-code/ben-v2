"""Gate I3 — BEN Improvement Loop (internal framework).

Observe → classify → Failure Record → Fix Contract → evaluate candidates
→ recommendation. Has no merge, deploy, or production-mutation authority.
Human review is always required before any production change.
"""

from services.improvement.authority import (
    FORBIDDEN_ACTIONS,
    assert_no_production_authority,
    has_production_authority,
)
from services.improvement.records import (
    CandidateResult,
    FailureRecord,
    FixContract,
    ImprovementRun,
)
from services.improvement.run import run_improvement, write_improvement_run
from services.improvement.taxonomy import DECISIONS, FAILURE_CLASSES, RISK_LEVELS

__all__ = [
    "FORBIDDEN_ACTIONS",
    "DECISIONS",
    "FAILURE_CLASSES",
    "RISK_LEVELS",
    "CandidateResult",
    "FailureRecord",
    "FixContract",
    "ImprovementRun",
    "assert_no_production_authority",
    "has_production_authority",
    "run_improvement",
    "write_improvement_run",
]
