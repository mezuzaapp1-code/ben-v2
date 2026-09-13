"""Typed Failure Record, Fix Contract, candidate, and Improvement Run artifacts."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Sequence

from services.improvement.taxonomy import DECISIONS, FAILURE_CLASSES, RISK_LEVELS

_MAX_TEXT = 400


def _clip(value: Any, limit: int = _MAX_TEXT) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[:limit]


def _list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value]


def _text(value: Any, limit: int = _MAX_TEXT) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        value = " ".join(str(v) for v in value if str(v).strip())
    return _clip(value, limit)


def _opt(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


@dataclass
class FailureRecord:
    failure_id: str
    source: str
    timestamp: str
    task_class: str
    failure_class: str
    severity: str
    repeat_count: int
    user_visible_impact: str
    security_relevant: bool
    input_reference: str
    expected_behavior: str
    actual_behavior: str
    expected_evidence: str
    observed_evidence: str
    component: str
    environment: str
    status: str = "open"
    retrieval_mode: str | None = None
    model: str | None = None
    tool: str | None = None
    benchmark_reference: str | None = None
    prior_label: str | None = None
    retrieved_empty: bool = False
    evidence_pages: list[int] = field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "FailureRecord":
        """Accept I3 fields and I1 aliases (user_query, retrieved_evidence, …)."""
        input_ref = data.get("input_reference") or data.get("user_query") or ""
        expected_ev = data.get("expected_evidence") or ""
        observed = (
            data.get("observed_evidence")
            or data.get("retrieved_evidence")
            or ""
        )
        expected_behavior = (
            data.get("expected_behavior")
            or data.get("expected_answer")
            or expected_ev
        )
        actual_behavior = data.get("actual_behavior") or data.get("actual_answer") or ""
        impact = data.get("user_visible_impact")
        if impact is None or impact is True:
            impact_text = "incorrect_or_missing_answer"
        elif impact is False:
            impact_text = "none"
        else:
            impact_text = str(impact)
        return cls(
            failure_id=str(data.get("failure_id") or ""),
            source=str(data.get("source") or data.get("benchmark_id") or "unknown"),
            timestamp=str(data.get("timestamp") or ""),
            task_class=str(data.get("task_class") or data.get("component") or "retrieval"),
            failure_class=str(data.get("failure_class") or "OTHER"),
            severity=str(data.get("severity") or "medium"),
            repeat_count=int(data.get("repeat_count") or 1),
            user_visible_impact=impact_text,
            security_relevant=bool(data.get("security_relevant")),
            input_reference=_text(input_ref, 500),
            expected_behavior=_text(expected_behavior),
            actual_behavior=_text(actual_behavior),
            expected_evidence=_text(expected_ev),
            observed_evidence=_text(observed),
            component=str(data.get("component") or "retrieval.query_prep"),
            environment=str(data.get("environment") or "isolated"),
            status=str(data.get("status") or "open"),
            retrieval_mode=_opt(data.get("retrieval_mode")),
            model=_opt(data.get("model")),
            tool=_opt(data.get("tool")),
            benchmark_reference=_opt(
                data.get("benchmark_reference") or data.get("benchmark_id")
            ),
            prior_label=_opt(data.get("prior_label")),
            retrieved_empty=bool(data.get("retrieved_empty")),
            evidence_pages=[int(p) for p in (data.get("evidence_pages") or [])],
            notes=str(data.get("notes") or ""),
        )

    def evidence_sufficient(self) -> bool:
        return bool(self.input_reference and self.expected_evidence)


REQUIRED_FAILURE_FIELDS: Sequence[str] = (
    "failure_id",
    "source",
    "timestamp",
    "task_class",
    "failure_class",
    "severity",
    "repeat_count",
    "user_visible_impact",
    "security_relevant",
    "input_reference",
    "expected_behavior",
    "actual_behavior",
    "expected_evidence",
    "observed_evidence",
    "component",
    "environment",
    "benchmark_reference",
    "status",
)


@dataclass
class FixContract:
    fix_id: str
    failure_class: str
    hypothesis: str
    scope: str
    risk_level: str
    expected_improvement: str
    allowed_components: list[str]
    forbidden_components: list[str]
    security_invariants: list[str]
    data_invariants: list[str]
    performance_budget: str
    required_targeted_tests: list[str]
    required_regression_tests: list[str]
    required_benchmarks: list[str]
    rollback_strategy: str
    max_candidates: int = 3
    stop_conditions: list[str] = field(default_factory=list)
    human_review_required: bool = True

    def __post_init__(self) -> None:
        # Human-reviewed gates remain required. The framework cannot opt out.
        self.human_review_required = True
        if self.max_candidates < 1:
            self.max_candidates = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "FixContract":
        risk = str(data.get("risk_level") or "LOW").upper()
        if risk not in RISK_LEVELS:
            risk = "MEDIUM"
        return cls(
            fix_id=str(data.get("fix_id") or ""),
            failure_class=str(data.get("failure_class") or data.get("problem_class") or "OTHER"),
            hypothesis=str(data.get("hypothesis") or ""),
            scope=str(data.get("scope") or "query_prep"),
            risk_level=risk,
            expected_improvement=str(data.get("expected_improvement") or ""),
            allowed_components=_list(
                data.get("allowed_components") or data.get("allowed_modules")
            ),
            forbidden_components=_list(
                data.get("forbidden_components") or data.get("forbidden_modules")
            ),
            security_invariants=_list(data.get("security_invariants")),
            data_invariants=_list(data.get("data_invariants")),
            performance_budget=str(data.get("performance_budget") or ""),
            required_targeted_tests=_list(data.get("required_targeted_tests")),
            required_regression_tests=_list(
                data.get("required_regression_tests") or data.get("required_tests")
            ),
            required_benchmarks=_list(data.get("required_benchmarks")),
            rollback_strategy=str(data.get("rollback_strategy") or ""),
            max_candidates=int(data.get("max_candidates") or 3),
            stop_conditions=_list(data.get("stop_conditions")),
            human_review_required=True,
        )


REQUIRED_CONTRACT_FIELDS: Sequence[str] = (
    "fix_id",
    "failure_class",
    "hypothesis",
    "scope",
    "risk_level",
    "expected_improvement",
    "allowed_components",
    "forbidden_components",
    "security_invariants",
    "data_invariants",
    "performance_budget",
    "required_targeted_tests",
    "required_regression_tests",
    "required_benchmarks",
    "rollback_strategy",
    "max_candidates",
    "stop_conditions",
    "human_review_required",
)


@dataclass
class CandidateMeasurement:
    """Already-measured candidate. The framework does not apply the patch."""

    candidate_id: str
    change_summary: str
    components_touched: list[str]
    target_improved: bool
    benchmark_result: dict[str, Any]
    isolation_ok: bool = True
    security_invariants_ok: bool = True
    latency_within_budget: bool = True
    cost_within_budget: bool | None = True
    unrelated_behavior_changed: bool = False
    hardcoding: bool = False
    http_5xx: int = 0
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "CandidateMeasurement":
        return cls(
            candidate_id=str(data.get("candidate_id") or ""),
            change_summary=str(data.get("change_summary") or ""),
            components_touched=_list(data.get("components_touched")),
            target_improved=bool(data.get("target_improved")),
            benchmark_result=dict(data.get("benchmark_result") or {}),
            isolation_ok=data.get("isolation_ok", True) is not False,
            security_invariants_ok=data.get("security_invariants_ok", True) is not False,
            latency_within_budget=data.get("latency_within_budget", True) is not False,
            cost_within_budget=None
            if data.get("cost_within_budget") is None
            else bool(data.get("cost_within_budget")),
            unrelated_behavior_changed=bool(data.get("unrelated_behavior_changed")),
            hardcoding=bool(data.get("hardcoding")),
            http_5xx=int(data.get("http_5xx") or 0),
            notes=str(data.get("notes") or ""),
        )


@dataclass
class CandidateResult:
    candidate_id: str
    change_summary: str
    benchmark_result: dict[str, Any]
    latency_delta: dict[str, Any]
    regressions: list[str]
    decision: str
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ImprovementRun:
    run_id: str
    failure_cluster: dict[str, Any]
    classification: str
    fix_contract: dict[str, Any]
    candidates: list[dict[str, Any]]
    test_results: dict[str, Any]
    baseline_comparison: dict[str, Any]
    risk_assessment: dict[str, Any]
    decision: str
    human_action_required: str
    provenance: dict[str, Any]
    priority: dict[str, Any] = field(default_factory=dict)
    stop_reason: str = ""
    production_authority_used: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def assert_known_class(label: str) -> str:
    if label not in FAILURE_CLASSES:
        return "OTHER"
    return label


def assert_known_decision(label: str) -> str:
    if label not in DECISIONS:
        return "BLOCKED"
    return label
