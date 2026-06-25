"""Master Switchboard enforcement — org-scoped engine activations gate chat routing."""
from __future__ import annotations

from services.global_service_store import list_active_global_channels
from services.providers.speaking_registry import provider_label

# Mirrors frontend PROVIDER_ENGINE_CATALOG_KEYS in globalFeatureCatalog.js
PROVIDER_ENGINE_CATALOG_KEYS: dict[str, str] = {
    "gpt": "engine-grok",
    "claude": "engine-claude",
    "gemini": "engine-gemini",
}

_CATALOG_TO_PROVIDER = {v: k for k, v in PROVIDER_ENGINE_CATALOG_KEYS.items()}


def active_engine_catalog_keys(org_id: str) -> frozenset[str]:
    """Active compute-engine catalog keys for an org (system_main.db)."""
    active = list_active_global_channels(org_id)
    keys = {
        str(row.get("catalog_key") or "").strip()
        for row in active
        if row.get("channel_kind") == "engine" and row.get("catalog_key")
    }
    return frozenset(key for key in keys if key)


def catalog_key_for_provider(provider_id: str | None) -> str | None:
    token = str(provider_id or "").strip().lower()
    if not token:
        return None
    return PROVIDER_ENGINE_CATALOG_KEYS.get(token)


def is_provider_engine_active(org_id: str, provider_id: str | None) -> bool:
    """True when the speaking provider is enabled in the platform capability catalog."""
    catalog_key = catalog_key_for_provider(provider_id)
    if not catalog_key:
        return True
    return catalog_key in active_engine_catalog_keys(org_id)


def assert_provider_engine_active(org_id: str, provider_id: str | None) -> None:
    """Raise ValueError when a gated engine is inactive in the workspace."""
    token = str(provider_id or "").strip().lower()
    if not token:
        return
    if is_provider_engine_active(org_id, token):
        return
    label = provider_label(token) or token.upper()
    raise ValueError(
        f"{label} is not active in workspace. Enable it in the Capability Catalog switchboard."
    )


def assert_resolved_chat_provider_active(org_id: str, provider_id: str | None) -> None:
    """Validate provider after NL intent resolution — blocks cross-engine handoff to inactive engines."""
    assert_provider_engine_active(org_id, provider_id)
