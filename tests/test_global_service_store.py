"""Global service infrastructure — system_main.db activations and project access."""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

import main
from auth.beta_gate import derive_beta_org_id
from services.global_service_store import (
    connect_global_channel,
    get_global_channel,
    global_channel_has_credentials,
    init_global_service_schema,
    list_active_global_channels,
    list_global_channels,
    toggle_global_channel,
)
from services.project_feature_access import resolve_project_workspace_features
from services.repository_store import connect_repository, list_repositories


@pytest.fixture
def global_env(tmp_path, monkeypatch):
    projects_dir = tmp_path / "projects"
    system_db = tmp_path / "system_main.db"
    monkeypatch.setenv("BEN_PROJECTS_DATA_DIR", str(projects_dir))
    monkeypatch.setenv("BEN_SYSTEM_DB_PATH", str(system_db))
    monkeypatch.setenv("BEN_LOCAL_BETA_MODE", "true")
    monkeypatch.setenv("BEN_BETA_PASSCODE", "beta-test-pass")
    init_global_service_schema()
    return {
        "org_id": str(derive_beta_org_id("global-user")),
        "project_a": "alpha-site",
        "project_b": "beta-site",
    }


def _beta_headers(alias: str = "global-user") -> dict[str, str]:
    return {
        "X-Basalt-Beta-Passcode": "beta-test-pass",
        "X-Basalt-Beta-Alias": alias,
    }


def test_connect_engine_activation_persists_in_system_main(global_env):
    org_id = global_env["org_id"]
    channel = connect_global_channel(
        org_id,
        name="Grok Compute Grid",
        source_type="external_library",
        source_metadata={"catalog_key": "engine-grok", "tier": "fast"},
        feature_flags={"streaming": True},
    )
    assert channel["channel_kind"] == "engine"
    assert channel["status"] == "active"
    assert channel["source_metadata"]["catalog_key"] == "engine-grok"
    assert channel["feature_flags"]["streaming"] is True
    assert "access_token" not in channel["source_metadata"]


def test_connect_integration_stores_credentials_separately(global_env):
    org_id = global_env["org_id"]
    channel = connect_global_channel(
        org_id,
        name="Gmail Live Stream",
        source_type="external_library",
        source_metadata={
            "catalog_key": "repo-gmail",
            "sync_mode": "metadata_only",
            "access_token": "secret-mail-token",
        },
    )
    assert channel["channel_kind"] == "integration"
    assert channel["source_type"] == "gmail"
    assert "access_token" not in channel["source_metadata"]
    assert global_channel_has_credentials(org_id, channel["id"]) is True


def test_toggle_global_channel_scrubs_credentials(global_env):
    org_id = global_env["org_id"]
    channel = connect_global_channel(
        org_id,
        name="Google Drive",
        source_type="google_drive",
        source_metadata={
            "catalog_key": "repo-gdrive",
            "folder_id": "abc",
            "refresh_token": "rotate-me",
        },
    )
    toggled = toggle_global_channel(org_id, channel["id"])
    assert toggled["status"] == "disconnected"
    assert "refresh_token" not in toggled["source_metadata"]
    assert toggled["source_metadata"]["folder_id"] == "abc"


def test_global_channels_shared_across_projects(global_env):
    org_id = global_env["org_id"]
    connect_repository(
        org_id,
        global_env["project_a"],
        name="Claude Core",
        source_type="external_library",
        source_metadata={"catalog_key": "engine-claude"},
    )
    alpha = list_repositories(org_id)
    beta = list_repositories(org_id)
    assert len(alpha) == 1
    assert alpha[0]["catalog_key"] == "engine-claude"
    assert beta == alpha


def test_resolve_project_workspace_features(global_env):
    org_id = global_env["org_id"]
    connect_global_channel(
        org_id,
        name="Grok",
        source_type="external_library",
        source_metadata={"catalog_key": "engine-grok"},
    )
    connect_global_channel(
        org_id,
        name="Sonar",
        source_type="sovereign_sonar",
        source_metadata={"catalog_key": "sonar-sovereign"},
    )
    payload = resolve_project_workspace_features(org_id, global_env["project_a"])
    assert payload["project_slug"] == global_env["project_a"]
    assert payload["org_id"] == org_id
    assert "engine-grok" in payload["catalog_keys"]
    assert "sonar-sovereign" in payload["catalog_keys"]
    assert len(payload["engines"]) == 1
    assert len(payload["integrations"]) == 1


def test_active_features_api(global_env):
    client = TestClient(main.app)
    connect_global_channel(
        global_env["org_id"],
        name="Gemini",
        source_type="external_library",
        source_metadata={"catalog_key": "engine-gemini"},
    )
    response = client.get(
        f"/api/projects/{global_env['project_a']}/active-features",
        headers=_beta_headers(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["project_slug"] == global_env["project_a"]
    assert "engine-gemini" in body["catalog_keys"]
    assert body["total_active"] >= 1


def test_platform_active_features_api(global_env):
    client = TestClient(main.app)
    connect_global_channel(
        global_env["org_id"],
        name="Claude",
        source_type="external_library",
        source_metadata={"catalog_key": "engine-claude"},
    )
    response = client.get("/api/platform/active-features", headers=_beta_headers())
    assert response.status_code == 200
    body = response.json()
    assert body["org_id"] == global_env["org_id"]
    assert "engine-claude" in body["catalog_keys"]
    assert body["total_active"] >= 1


def test_platform_connect_and_toggle_api(global_env):
    client = TestClient(main.app)
    connect_response = client.post(
        "/api/platform/capabilities/connect",
        headers=_beta_headers(),
        json={
            "name": "Grok Compute Grid",
            "source_type": "external_library",
            "source_metadata": {"catalog_key": "engine-grok", "tier": "fast"},
        },
    )
    assert connect_response.status_code == 200
    capability = connect_response.json()["capability"]
    assert capability["catalog_key"] == "engine-grok"
    assert capability["status"] == "active"

    toggle_response = client.post(
        f"/api/platform/capabilities/{capability['id']}/toggle",
        headers=_beta_headers(),
    )
    assert toggle_response.status_code == 200
    toggled = toggle_response.json()["capability"]
    assert toggled["status"] == "disconnected"


def test_connect_requires_catalog_key(global_env):
    with pytest.raises(ValueError, match="catalog_key"):
        connect_global_channel(
            global_env["org_id"],
            name="Missing Key",
            source_type="local",
            source_metadata={},
        )


def test_get_global_channel_round_trip(global_env):
    org_id = global_env["org_id"]
    created = connect_global_channel(
        org_id,
        name="Local Vault",
        source_type="local",
        source_metadata={"catalog_key": "repo-local"},
    )
    loaded = get_global_channel(org_id, created["id"])
    assert loaded is not None
    assert loaded["id"] == created["id"]
    assert loaded["catalog_key"] == "repo-local"


def test_list_active_global_channels_filters_disconnected(global_env):
    org_id = global_env["org_id"]
    active = connect_global_channel(
        org_id,
        name="Active",
        source_type="local",
        source_metadata={"catalog_key": "repo-local"},
    )
    disabled = connect_global_channel(
        org_id,
        name="Disabled",
        source_type="google_drive",
        source_metadata={"catalog_key": "repo-gdrive"},
    )
    toggle_global_channel(org_id, disabled["id"])
    keys = {row["catalog_key"] for row in list_active_global_channels(org_id)}
    assert "repo-local" in keys
    assert "repo-gdrive" not in keys
    assert get_global_channel(org_id, active["id"])["status"] == "active"


def test_chat_rejects_inactive_engine(global_env):
    client = TestClient(main.app)
    response = client.post(
        "/chat",
        headers=_beta_headers(),
        json={"message": "hi", "tier": "free", "provider_id": "claude"},
    )
    assert response.status_code == 403
    detail = response.json().get("detail")
    assert isinstance(detail, dict)
    assert detail.get("error") == "CapabilityInactiveException"
    message = str(detail.get("message") or "").lower()
    assert "deactivated" in message or "switchboard" in message


def test_chat_allows_active_engine(global_env):
    client = TestClient(main.app)
    connect_global_channel(
        global_env["org_id"],
        name="Claude",
        source_type="external_library",
        source_metadata={"catalog_key": "engine-claude"},
    )

    async def fake_chat(message, user_id, tenant_id, tier, *, thread_id=None, provider_id=None, model_override=None, preferred_language=None):
        return {
            "thread_id": str(uuid.uuid4()),
            "response": "ok",
            "model_used": "m",
            "cost_usd": 0.0,
        }

    with patch.object(main, "handle_chat", side_effect=fake_chat):
        response = client.post(
            "/chat",
            headers=_beta_headers(),
            json={"message": "hi", "tier": "free", "provider_id": "claude"},
        )
    assert response.status_code == 200
