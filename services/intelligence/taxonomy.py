"""Versioned Event Understanding taxonomy: act types vs domain tags."""
from __future__ import annotations

from typing import Final

TAXONOMY_VERSION: Final = "event_taxonomy_v1"
CLASSIFIER_VERSION: Final = "event_classifier_v1"
TEMPLATE_VERSION: Final = "question_templates_v1"

# Primary act types (closed set). Domains must never appear here.
PRIMARY_EVENT_TYPES: Final[tuple[str, ...]] = (
    "acquisition",
    "funding_round",
    "regulation",
    "government_policy",
    "ai_model_release",
    "product_launch",
    "partnership",
    "research_publication",
    "security_incident",
    "open_source_release",
    "earnings",
    "leadership_change",
    "infrastructure_expansion",
    "unknown",
)

PRIMARY_EVENT_TYPE_LABELS: Final[dict[str, str]] = {
    "acquisition": "Acquisition",
    "funding_round": "Funding Round",
    "regulation": "Regulation",
    "government_policy": "Government Policy",
    "ai_model_release": "AI Model Release",
    "product_launch": "Product Launch",
    "partnership": "Partnership",
    "research_publication": "Research Publication",
    "security_incident": "Security Incident",
    "open_source_release": "Open Source Release",
    "earnings": "Earnings",
    "leadership_change": "Leadership Change",
    "infrastructure_expansion": "Infrastructure Expansion",
    "unknown": "Unknown",
}

# Tie-break precedence when event-type scores are equal (earlier = higher priority).
EVENT_TYPE_PRECEDENCE: Final[tuple[str, ...]] = (
    "acquisition",
    "funding_round",
    "earnings",
    "security_incident",
    "regulation",
    "government_policy",
    "ai_model_release",
    "open_source_release",
    "research_publication",
    "partnership",
    "product_launch",
    "leadership_change",
    "infrastructure_expansion",
    "unknown",
)

DOMAIN_TAGS: Final[tuple[str, ...]] = (
    "artificial_intelligence",
    "robotics",
    "semiconductors",
    "cloud",
    "data_centers",
    "security",
    "open_source",
    "startups",
    "science",
    "business",
)

DOMAIN_TAG_LABELS: Final[dict[str, str]] = {
    "artificial_intelligence": "Artificial Intelligence",
    "robotics": "Robotics",
    "semiconductors": "Semiconductors",
    "cloud": "Cloud",
    "data_centers": "Data Centers",
    "security": "Security",
    "open_source": "Open Source",
    "startups": "Startups",
    "science": "Science",
    "business": "Business",
}

_PRIMARY_SET = frozenset(PRIMARY_EVENT_TYPES)
_DOMAIN_SET = frozenset(DOMAIN_TAGS)


def is_primary_event_type(value: str) -> bool:
    return value in _PRIMARY_SET


def is_domain_tag(value: str) -> bool:
    return value in _DOMAIN_SET


def assert_not_domain_as_event_type(value: str) -> None:
    if value in _DOMAIN_SET:
        raise ValueError(f"domain tag cannot be used as event type: {value}")
    if value not in _PRIMARY_SET:
        raise ValueError(f"unknown primary event type: {value}")


def precedence_index(event_type: str) -> int:
    try:
        return EVENT_TYPE_PRECEDENCE.index(event_type)
    except ValueError:
        return len(EVENT_TYPE_PRECEDENCE)
