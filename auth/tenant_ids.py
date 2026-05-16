"""Stable tenant identifiers for DB/RLS (UUID-backed; never from client JSON)."""
from __future__ import annotations

import uuid

# Deterministic namespace for personal workspaces (logical id: user:{sub})
_PERSONAL_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_DNS, "ben-v2.personal-tenant")


def personal_tenant_uuid(user_id: str) -> uuid.UUID:
    uid = str(user_id).strip()
    if not uid:
        raise ValueError("user_id required for personal tenant")
    return uuid.uuid5(_PERSONAL_NAMESPACE, f"user:{uid}")


def personal_tenant_id(user_id: str) -> str:
    return str(personal_tenant_uuid(user_id))


def personal_tenant_logical_id(user_id: str) -> str:
    return f"user:{str(user_id).strip()}"
