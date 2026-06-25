"""Project creation privileges — Admin/Owner within org tenants."""
from __future__ import annotations

from fastapi import HTTPException, status

from auth.tenant_binding import TenantContext

_ADMIN_ROLES = frozenset({"org:admin", "admin", "owner", "org:owner"})


def _normalize_role(role: str | None) -> str:
    return (role or "").strip().lower()


def can_create_project(ctx: TenantContext) -> bool:
    """Personal workspace owners and org admins/owners may create projects."""
    if ctx.tenant_type == "personal":
        return True
    if ctx.tenant_type == "organization":
        role = _normalize_role(ctx.org_role)
        if role in _ADMIN_ROLES or role.endswith(":admin"):
            return True
        return False
    return False


def assert_can_create_project(ctx: TenantContext) -> None:
    if ctx.auth_source == "beta_passcode":
        return
    if not can_create_project(ctx):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="Admin or owner role required to create projects in this organization",
        )
