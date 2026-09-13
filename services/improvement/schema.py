"""Failure Record and Fix Contract schemas for Gate I1."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Mapping, Sequence

FAILURE_CLASSES = (
    "LEXICAL_VARIATION",
    "RANKING_FAILURE",
    "CONTEXT_LOSS",
    "EXCEPTION_MISSED",
    "MULTI_HOP",
    "MODEL_REASONING_FAILURE",
    "TABLE_LAYOUT",
    "WRONG_SOURCE",
    "CROSS_FILE",
    "OTHER",
)

SEVERITIES = ("low", "medium", "high")
RISK_LEVELS = ("low", "medium", "high")
SCOPES = ("query_prep", "retrieval", "ranking", "architecture")
DECISIONS = ("PASS", "REJECT", "NEEDS_REVIEW")
CAPABILITY_VERDICTS = ("USE", "ADAPT", "LEARN", "REJECT")

Decision = Literal["PASS", "REJECT", "NEEDS_REVIEW"]


def _list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value]


@dataclass
class FailureRecord:
    failure_id: str
    benchmark_id: str
    task_id: str
    failure_class: str
    user_query: str
    expected_evidence: str
    retrieved_evidence: str
    actual_answer: str
    expected_answer: str
    source_files: list[str]
    retrieval_mode: str
    timestamp: str
    severity: str
    security_relevant: bool
    repeat_count: int
    prior_label: str | None = None
    retrieved_empty: bool = False
    evidence_pages: list[int] = field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "FailureRecord":
        return cls(
            failure_id=str(data["failure_id"]),
            benchmark_id=str(data["benchmark_id"]),
            task_id=str(data["task_id"]),
            failure_class=str(data.get("failure_class") or "OTHER"),
            user_query=str(data.get("user_query") or ""),
            expected_evidence=str(data.get("expected_evidence") or ""),
            retrieved_evidence=str(data.get("retrieved_evidence") or ""),
            actual_answer=str(data.get("actual_answer") or ""),
            expected_answer=str(data.get("expected_answer") or ""),
            source_files=_list(data.get("source_files")),
            retrieval_mode=str(data.get("retrieval_mode") or ""),
            timestamp=str(data.get("timestamp") or ""),
            severity=str(data.get("severity") or "medium"),
            security_relevant=bool(data.get("security_relevant")),
            repeat_count=int(data.get("repeat_count") or 1),
            prior_label=(str(data["prior_label"]) if data.get("prior_label") else None),
            retrieved_empty=bool(data.get("retrieved_empty")),
            evidence_pages=[int(p) for p in (data.get("evidence_pages") or [])],
            notes=str(data.get("notes") or ""),
        )


@dataclass
class FixContract:
    problem_class: str
    hypothesis: str
    expected_improvement: str
    allowed_modules: list[str]
    forbidden_modules: list[str]
    security_invariants: list[str]
    performance_budget: str
    required_tests: list[str]
    rollback_strategy: str
    scope: str
    risk_level: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "FixContract":
        return cls(
            problem_class=str(data["problem_class"]),
            hypothesis=str(data.get("hypothesis") or ""),
            expected_improvement=str(data.get("expected_improvement") or ""),
            allowed_modules=_list(data.get("allowed_modules")),
            forbidden_modules=_list(data.get("forbidden_modules")),
            security_invariants=_list(data.get("security_invariants")),
            performance_budget=str(data.get("performance_budget") or ""),
            required_tests=_list(data.get("required_tests")),
            rollback_strategy=str(data.get("rollback_strategy") or ""),
            scope=str(data.get("scope") or "query_prep"),
            risk_level=str(data.get("risk_level") or "low"),
        )


@dataclass
class CandidateResult:
    candidate_id: str
    change_summary: str
    expand_mode: str
    benchmark_result: dict[str, Any]
    latency_delta: dict[str, Any]
    regressions: list[str]
    decision: str
    targeted: dict[str, Any] = field(default_factory=dict)
    isolation: dict[str, Any] = field(default_factory=dict)
    hardcoding: dict[str, Any] = field(default_factory=dict)
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CapabilityItem:
    name: str
    verdict: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def required_failure_fields() -> Sequence[str]:
    return (
        "failure_id",
        "benchmark_id",
        "task_id",
        "failure_class",
        "user_query",
        "expected_evidence",
        "retrieved_evidence",
        "actual_answer",
        "expected_answer",
        "source_files",
        "retrieval_mode",
        "timestamp",
        "severity",
        "security_relevant",
        "repeat_count",
    )


def required_contract_fields() -> Sequence[str]:
    return (
        "problem_class",
        "hypothesis",
        "expected_improvement",
        "allowed_modules",
        "forbidden_modules",
        "security_invariants",
        "performance_budget",
        "required_tests",
        "rollback_strategy",
        "scope",
        "risk_level",
    )
