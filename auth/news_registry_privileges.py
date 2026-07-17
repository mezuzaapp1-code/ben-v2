"""Privileges for the global (non-RLS) News source registry."""
from __future__ import annotations

from fastapi import HTTPException, status

from auth.tenant_binding import TenantContext

_ADMIN_ROLES = frozenset({"org:admin", "admin", "owner", "org:owner"})


def _normalize_role(role: str | None) -> str:
    return (role or "").strip().lower()


def can_manage_news_sources(ctx: TenantContext) -> bool:
    """Beta operators, or organization admin/owner roles, may manage system news sources."""
    if ctx.auth_source == "beta_passcode":
        return True
    if ctx.tenant_type == "organization":
        role = _normalize_role(ctx.org_role)
        if role in _ADMIN_ROLES or role.endswith(":admin"):
            return True
        return False
    return False


def assert_can_manage_news_sources(ctx: TenantContext) -> None:
    if can_manage_news_sources(ctx):
        return
    raise HTTPException(
        status.HTTP_403_FORBIDDEN,
        detail="Admin or owner role required to manage news sources",
    )
