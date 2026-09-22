"""Default-off internal pilot. Server configuration, never request fields, grants access."""
import json
import os
import uuid

from fastapi import HTTPException, Request
from auth.beta_gate import build_project_tenant_context_from_request


def pilot_principals() -> set[tuple[uuid.UUID, str]]:
    if os.getenv("BEN_MEDIA_INTERNAL_ENABLED") != "1":
        return set()
    try:
        entries = json.loads(os.getenv("BEN_MEDIA_INTERNAL_PRINCIPALS", "[]"))
        if not isinstance(entries, list) or len(entries) > 32:
            return set()
        return {(uuid.UUID(e["org_id"]), e["user_id"]) for e in entries
                if isinstance(e, dict) and isinstance(e.get("user_id"), str) and e["user_id"]}
    except (ValueError, KeyError, TypeError):
        return set()


async def require_pilot(request: Request):
    ctx = await build_project_tenant_context_from_request(request, route_operation="media_internal")
    org, user = uuid.UUID(ctx.tenant_id), ctx.user_id
    if (org, user) not in pilot_principals():
        raise HTTPException(404, "Media unavailable")
    return org, user
