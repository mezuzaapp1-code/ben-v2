"""Auth feature flags (defaults: observe only, do not enforce)."""
from __future__ import annotations

import os


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def is_enforce_auth() -> bool:
    """When True, /chat and /council require valid Bearer token."""
    return _env_bool("ENFORCE_AUTH", False)


def is_auth_shadow_mode() -> bool:
    """When True, log auth outcomes without blocking (unless ENFORCE_AUTH is also True)."""
    return _env_bool("AUTH_SHADOW_MODE", True)


def get_anonymous_org_id() -> str:
    """Server-side org UUID for unsigned requests (never take this from client JSON)."""
    return os.getenv("BEN_ANONYMOUS_ORG_ID", "00000000-0000-0000-0000-000000000001").strip()


def auth_config_for_health() -> dict[str, bool]:
    """Safe booleans for /health and /ready (no secrets)."""
    from auth.tenant_policy import require_org_for_signed_in, tenant_modes_enabled

    en = is_enforce_auth()
    return {
        "auth_enforcement": en,
        "enforce_auth": en,
        "tenant_binding_enabled": True,
        "tenant_modes_enabled": tenant_modes_enabled(),
        "require_org_for_signed_in": require_org_for_signed_in(),
        "auth_shadow_mode": is_auth_shadow_mode(),
        "clerk_secret_configured": bool(os.getenv("CLERK_SECRET_KEY", "").strip()),
    }
