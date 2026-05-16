"""Standard runtime lifecycle event names (observability v1)."""
from __future__ import annotations

REQUEST_STARTED = "request_started"
REQUEST_COMPLETED = "request_completed"
REQUEST_FAILED = "request_failed"

COUNCIL_PENDING = "council_pending"
COUNCIL_RUNNING = "council_running"
COUNCIL_STARTED = "council_started"
COUNCIL_COMPLETED = "council_completed"

PROVIDER_TIMEOUT = "provider_timeout"
OVERLOAD_REJECTED = "overload_rejected"
PERSISTENCE_FAILED = "persistence_failed"
RUNTIME_SNAPSHOT = "runtime_snapshot"

IDEMPOTENCY_REJECTED = "idempotency_rejected"
REPLAY_DETECTED = "replay_detected"
STALE_RUNTIME_STATE_RECOVERED = "stale_runtime_state_recovered"
PERSISTENCE_RECOVERY = "persistence_recovery"

ALL_EVENTS = frozenset(
    {
        REQUEST_STARTED,
        REQUEST_COMPLETED,
        REQUEST_FAILED,
        COUNCIL_PENDING,
        COUNCIL_RUNNING,
        COUNCIL_STARTED,
        COUNCIL_COMPLETED,
        PROVIDER_TIMEOUT,
        OVERLOAD_REJECTED,
        PERSISTENCE_FAILED,
        RUNTIME_SNAPSHOT,
        IDEMPOTENCY_REJECTED,
        REPLAY_DETECTED,
        STALE_RUNTIME_STATE_RECOVERED,
        PERSISTENCE_RECOVERY,
    }
)
