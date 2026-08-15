"""Shared Clerk JWT test doubles for Security Gate A and route tests."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

from auth.tenant_binding import TenantContext

AUTH_HEADER = {"Authorization": "Bearer test-token"}


def persistent_customer_context(tenant_id: str, *, user_id: str = "tester") -> TenantContext:
    """Authenticated org tenant — used so routing tests survive Gate A."""
    return TenantContext(
        tenant_id=str(tenant_id),
        tenant_type="organization",
        user_id=user_id,
        org_id=str(tenant_id),
        org_role="org:admin",
        email="tester@example.com",
        auth_source="clerk_jwt",
        auth_present=True,
        org_bound=True,
    )


def patch_main_persistent_tenant(tenant_id: str):
    import main

    return patch.object(
        main,
        "_tenant_ctx_from_request",
        new=AsyncMock(return_value=persistent_customer_context(tenant_id)),
    )


def autouse_persistent_tenant(tenant_id: str):
    """Context manager for pytest autouse fixtures in routing tests."""
    return patch_main_persistent_tenant(tenant_id)


def clerk_claims(user_id: str = "user_a", org_id: str | None = None, org_role: str | None = None) -> dict:
    claims: dict = {"sub": user_id, "email": f"{user_id}@example.com"}
    if org_id:
        claims["org_id"] = org_id
        if org_role:
            claims["org_role"] = org_role
    return claims


def patch_clerk_user(user_id: str = "user_a", org_id: str | None = None, org_role: str | None = None):
    return patch(
        "auth.tenant_binding._clerk_verify_token",
        return_value=clerk_claims(user_id=user_id, org_id=org_id, org_role=org_role),
    )
