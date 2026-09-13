"""Failure taxonomy, risk levels, and decisions for the Improvement Loop."""

from __future__ import annotations

from typing import Literal

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
    "ROUTING_FAILURE",
    "TOOL_FAILURE",
    "STRUCTURED_OUTPUT_FAILURE",
    "BUSINESS_MEMORY_FAILURE",
    "AGENT_ACTION_FAILURE",
    "PERMISSION_FAILURE",
    "OTHER",
)

# Not every class is automatically fixable by query-prep candidates.
AUTO_EVALUABLE_CLASSES = frozenset({"LEXICAL_VARIATION"})

RISK_LEVELS = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
RiskLevel = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]

DECISIONS = ("PASS", "REJECT", "NEEDS_REVIEW", "BLOCKED")
Decision = Literal["PASS", "REJECT", "NEEDS_REVIEW", "BLOCKED"]

SEVERITIES = ("low", "medium", "high", "critical")
PATTERN_KINDS = ("one-off", "repeated", "systemic")

# HIGH and CRITICAL never eligible for automatic production promotion.
NO_AUTO_PROMOTE_RISKS = frozenset({"HIGH", "CRITICAL"})

RISK_POLICY = {
    "LOW": {
        "examples": [
            "bounded query preparation",
            "non-security prompt formatting",
            "observability",
        ],
        "auto_promote": False,
        "may_recommend_pass": True,
    },
    "MEDIUM": {
        "examples": [
            "retrieval ranking",
            "context assembly",
            "model routing",
        ],
        "auto_promote": False,
        "may_recommend_pass": True,
    },
    "HIGH": {
        "examples": [
            "external tool actions",
            "business-memory writes",
            "customer communication automation",
        ],
        "auto_promote": False,
        "may_recommend_pass": False,
    },
    "CRITICAL": {
        "examples": [
            "auth",
            "RLS",
            "tenant isolation",
            "payments",
            "secrets",
            "permission escalation",
            "financial commitments",
        ],
        "auto_promote": False,
        "may_recommend_pass": False,
    },
}

CLASS_DEFAULT_RISK: dict[str, str] = {
    "LEXICAL_VARIATION": "LOW",
    "RANKING_FAILURE": "MEDIUM",
    "CONTEXT_LOSS": "MEDIUM",
    "EXCEPTION_MISSED": "MEDIUM",
    "MULTI_HOP": "MEDIUM",
    "MODEL_REASONING_FAILURE": "MEDIUM",
    "TABLE_LAYOUT": "MEDIUM",
    "WRONG_SOURCE": "HIGH",
    "CROSS_FILE": "MEDIUM",
    "ROUTING_FAILURE": "MEDIUM",
    "TOOL_FAILURE": "HIGH",
    "STRUCTURED_OUTPUT_FAILURE": "MEDIUM",
    "BUSINESS_MEMORY_FAILURE": "HIGH",
    "AGENT_ACTION_FAILURE": "HIGH",
    "PERMISSION_FAILURE": "CRITICAL",
    "OTHER": "MEDIUM",
}

ALWAYS_FORBIDDEN_COMPONENTS = (
    "auth",
    "tenant isolation",
    "RLS",
    "secrets",
    "payments",
    "permission escalation",
    "database schema",
    "production flags",
    "provider routing",
    "file ownership",
)
