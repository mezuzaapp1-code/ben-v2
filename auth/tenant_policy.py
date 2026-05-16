"""Plan-aware org requirement policy for signed-in tenants (tenant mode v2)."""
from __future__ import annotations

import os


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def tenant_modes_enabled() -> bool:
    """Personal + organization + anonymous tenant derivation is active."""
    return _env_bool("TENANT_MODES_ENABLED", True)


def require_org_for_signed_in() -> bool:
    """
    When True, signed-in JWT without org_id receives clerk_org_required (team/business mode).
    Default False: personal workspace allowed without Clerk organization.
    """
    return _env_bool("REQUIRE_ORG_FOR_SIGNED_IN", False)
