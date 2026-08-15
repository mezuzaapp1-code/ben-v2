"""Security Gate A — persistent customer state requires a real customer identity.

Rejects the shared anonymous tenant (BEN_ANONYMOUS_ORG_ID) for projects, workspace
files, threads, and chat. Does not introduce session-scoped anonymous tenants.
Does not change Personal vs Organization derivation.
"""
from __future__ import annotations

from fastapi import HTTPException, status

from auth.tenant_binding import TenantContext

_CUSTOMER_TENANT_TYPES = frozenset({"personal", "organization"})


def is_persistent_customer_identity(ctx: TenantContext) -> bool:
    """True when ctx may own persistent projects/files/threads.

    Allowed:
    - Clerk JWT personal or organization tenants
    - Closed-beta passcode alias (isolated uuid5, not the shared anonymous org)
    Rejected:
    - Shared anonymous tenant (auth_missing / invalid → BEN_ANONYMOUS_ORG_ID)
    """
    if ctx.auth_source == "beta_passcode":
        return True
    if ctx.auth_source == "clerk_jwt" and ctx.tenant_type in _CUSTOMER_TENANT_TYPES:
        return True
    return False


def assert_persistent_customer_identity(ctx: TenantContext) -> TenantContext:
    """401 unless the caller is a persistent customer identity."""
    if is_persistent_customer_identity(ctx):
        return ctx
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unauthorized")
