"""Master Switchboard engine gating — org-scoped active engines."""
from __future__ import annotations

import pytest

from services.engine_capability_gate import (
    active_engine_catalog_keys,
    assert_provider_engine_active,
    is_provider_engine_active,
)
from services.global_service_store import connect_global_channel, init_global_service_schema, toggle_global_channel


@pytest.fixture
def gate_env(tmp_path, monkeypatch):
    system_db = tmp_path / "system_main.db"
    monkeypatch.setenv("BEN_SYSTEM_DB_PATH", str(system_db))
    init_global_service_schema()
    return {"org_id": "org-gate-test"}


def test_inactive_engine_rejected(gate_env):
    org_id = gate_env["org_id"]
    assert is_provider_engine_active(org_id, "claude") is False
    with pytest.raises(ValueError, match="not active in workspace"):
        assert_provider_engine_active(org_id, "claude")


def test_active_engine_allowed(gate_env):
    org_id = gate_env["org_id"]
    connect_global_channel(
        org_id,
        name="Claude",
        source_type="external_library",
        source_metadata={"catalog_key": "engine-claude"},
    )
    assert "engine-claude" in active_engine_catalog_keys(org_id)
    assert is_provider_engine_active(org_id, "claude") is True
    assert_provider_engine_active(org_id, "claude") is None


def test_toggle_off_blocks_routing(gate_env):
    org_id = gate_env["org_id"]
    channel = connect_global_channel(
        org_id,
        name="Gemini",
        source_type="external_library",
        source_metadata={"catalog_key": "engine-gemini"},
    )
    assert is_provider_engine_active(org_id, "gemini") is True
    toggle_global_channel(org_id, channel["id"])
    assert is_provider_engine_active(org_id, "gemini") is False
    with pytest.raises(ValueError, match="Gemini"):
        assert_provider_engine_active(org_id, "gemini")


def test_omitted_provider_skips_gate(gate_env):
    org_id = gate_env["org_id"]
    assert_provider_engine_active(org_id, None) is None
    assert_provider_engine_active(org_id, "") is None
