"""Intelligence Engine — Event Understanding Phase 1 (deterministic foundation)."""

from services.intelligence.contracts import EventUnderstanding, materialize_event_understanding
from services.intelligence.taxonomy import (
    CLASSIFIER_VERSION,
    DOMAIN_TAGS,
    PRIMARY_EVENT_TYPES,
    TAXONOMY_VERSION,
    TEMPLATE_VERSION,
)

__all__ = [
    "CLASSIFIER_VERSION",
    "DOMAIN_TAGS",
    "PRIMARY_EVENT_TYPES",
    "TAXONOMY_VERSION",
    "TEMPLATE_VERSION",
    "EventUnderstanding",
    "materialize_event_understanding",
]
