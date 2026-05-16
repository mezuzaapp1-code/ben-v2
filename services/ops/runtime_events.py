"""Standard runtime lifecycle event names (observability v1)."""
from __future__ import annotations

REQUEST_STARTED = "request_started"
REQUEST_COMPLETED = "request_completed"
REQUEST_FAILED = "request_failed"

COUNCIL_STARTED = "council_started"
COUNCIL_COMPLETED = "council_completed"

PROVIDER_TIMEOUT = "provider_timeout"
OVERLOAD_REJECTED = "overload_rejected"
PERSISTENCE_FAILED = "persistence_failed"
RUNTIME_SNAPSHOT = "runtime_snapshot"

ALL_EVENTS = frozenset(
    {
        REQUEST_STARTED,
        REQUEST_COMPLETED,
        REQUEST_FAILED,
        COUNCIL_STARTED,
        COUNCIL_COMPLETED,
        PROVIDER_TIMEOUT,
        OVERLOAD_REJECTED,
        PERSISTENCE_FAILED,
        RUNTIME_SNAPSHOT,
    }
)
