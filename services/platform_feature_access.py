"""Platform-wide capability catalog — org-scoped activations in system_main.db."""
from __future__ import annotations

from typing import Any

from services.global_service_store import (
    connect_global_channel,
    list_active_global_channels,
    list_global_channels,
    toggle_global_channel,
)


def resolve_platform_features(org_id: str) -> dict[str, Any]:
    """Expose globally enabled engines and integrations for the signed-in org."""
    active = list_active_global_channels(org_id)
    all_channels = list_global_channels(org_id)
    engines = [row for row in active if row.get("channel_kind") == "engine"]
    integrations = [row for row in active if row.get("channel_kind") == "integration"]

    return {
        "org_id": str(org_id),
        "active_features": active,
        "engines": engines,
        "integrations": integrations,
        "catalog_keys": [
            str(row.get("catalog_key") or "") for row in active if row.get("catalog_key")
        ],
        "total_configured": len(all_channels),
        "total_active": len(active),
    }


def connect_platform_capability(
    org_id: str,
    *,
    name: str,
    source_type: str,
    source_metadata: dict[str, Any],
    feature_flags: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Activate a built-in capability globally for the org."""
    return connect_global_channel(
        org_id,
        name=name,
        source_type=source_type,
        source_metadata=source_metadata,
        feature_flags=feature_flags,
    )


def toggle_platform_capability(org_id: str, channel_id: int) -> dict[str, Any]:
    """Deactivate a built-in capability globally for the org."""
    return toggle_global_channel(org_id, channel_id)
