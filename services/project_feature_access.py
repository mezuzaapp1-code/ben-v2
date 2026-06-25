"""Project workspace feature exposure — reads global system_main activations."""
from __future__ import annotations

from typing import Any

from services.global_service_store import list_active_global_channels, list_global_channels
from services.project_tools import slugify_project_name


def resolve_project_workspace_features(org_id: str, project_slug: str) -> dict[str, Any]:
    """
    When a project context resolves, expose globally enabled tools without re-init.

    Engines and integrations are org-scoped in system_main.db; all projects under the
    same org inherit active channels instantly.
    """
    slug = slugify_project_name(project_slug)
    if not slug:
        raise ValueError("invalid project_slug")

    active = list_active_global_channels(org_id)
    all_channels = list_global_channels(org_id)
    engines = [row for row in active if row.get("channel_kind") == "engine"]
    integrations = [row for row in active if row.get("channel_kind") == "integration"]

    return {
        "project_slug": slug,
        "org_id": str(org_id),
        "active_features": active,
        "engines": engines,
        "integrations": integrations,
        "catalog_keys": [str(row.get("catalog_key") or "") for row in active if row.get("catalog_key")],
        "total_configured": len(all_channels),
        "total_active": len(active),
    }
