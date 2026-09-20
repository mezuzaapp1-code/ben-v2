"""Default-off latency audit must not change chat behavior."""
from __future__ import annotations

from services.ops.latency_path_audit import audit_enabled, configure_audit_for_process, mark, audit_snapshot


def test_latency_audit_disabled_by_default():
    configure_audit_for_process(enabled=False)
    assert audit_enabled() is False
    mark("t0_accepted")
    assert audit_snapshot() == {}
