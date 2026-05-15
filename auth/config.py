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


def auth_config_for_health() -> dict[str, bool]:
    """Safe booleans for /health and /ready (no secrets)."""
    return {
        "auth_enforcement": is_enforce_auth(),
        "auth_shadow_mode": is_auth_shadow_mode(),
        "clerk_secret_configured": bool(os.getenv("CLERK_SECRET_KEY", "").strip()),
    }
