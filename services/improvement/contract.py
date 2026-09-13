"""Build a Fix Contract from a classified failure. No question-id branches."""

from __future__ import annotations

from services.improvement.schema import FixContract
from services.workspace_files.query_expansion import ALLOWED_MODULES, FORBIDDEN_MODULES

_FTS_BUDGET = (
    "FTS statement timeout remains 200ms; mean fts_latency_ms must stay below "
    "that timeout and must not exceed 2× the same-harness control mean."
)

_SECURITY = [
    "same org/workspace/file filtering",
    "no broader unauthorized source set",
    "user tokens never inject tsquery operators",
    "prefix :* atoms originate only from the expander",
]

_TESTS = [
    "Gate M gold extractive benchmark (isolated FTS)",
    "Gate P BEN_EXISTING_FTS regression vs committed baseline",
    "retrieval isolation tests (cross-org, cross-workspace)",
    "no cross-workspace leakage",
    "no regression in unanswerable precision",
]

_ROLLBACK = (
    "Leave BEN_FTS_LEXICAL_EXPAND unset/off. Do not merge. Do not deploy. "
    "Production retrieval path is unchanged while the flag is off."
)


def contract_for(problem_class: str) -> FixContract:
    cls = (problem_class or "OTHER").strip().upper()
    if cls == "LEXICAL_VARIATION":
        return FixContract(
            problem_class=cls,
            hypothesis=(
                "Bounded query expansion over already-sanitized Latin tokens may "
                "recover inflectional variants (e.g. delivered↔delivery) on the "
                "existing simple FTS index without changing ranking or schema."
            ),
            expected_improvement=(
                "Lexical-variation misses recover the decisive span; total "
                "correctness, decisive recall, and unanswerable precision do not drop."
            ),
            allowed_modules=sorted(ALLOWED_MODULES),
            forbidden_modules=sorted(FORBIDDEN_MODULES),
            security_invariants=list(_SECURITY),
            performance_budget=_FTS_BUDGET,
            required_tests=list(_TESTS),
            rollback_strategy=_ROLLBACK,
            scope="query_prep",
            risk_level="low",
        )
    if cls == "RANKING_FAILURE":
        return FixContract(
            problem_class=cls,
            hypothesis=(
                "Chunks matching query tokens are returned but the decisive span "
                "ranks below the selection cap. A ranking change would be required."
            ),
            expected_improvement="Decisive span selected among existing matches.",
            allowed_modules=["retrieval ranking"],
            forbidden_modules=sorted(FORBIDDEN_MODULES),
            security_invariants=list(_SECURITY),
            performance_budget=_FTS_BUDGET,
            required_tests=list(_TESTS),
            rollback_strategy=_ROLLBACK,
            scope="ranking",
            risk_level="medium",
        )
    return FixContract(
        problem_class=cls,
        hypothesis=(
            "This failure class is outside the I1 lexical query-prep prototype. "
            "No candidate should be auto-applied."
        ),
        expected_improvement="Human-reviewed architecture decision.",
        allowed_modules=[],
        forbidden_modules=sorted(FORBIDDEN_MODULES | {"retrieval ranking", "schema", "providers"}),
        security_invariants=list(_SECURITY),
        performance_budget=_FTS_BUDGET,
        required_tests=list(_TESTS),
        rollback_strategy=_ROLLBACK,
        scope="architecture",
        risk_level="high",
    )
