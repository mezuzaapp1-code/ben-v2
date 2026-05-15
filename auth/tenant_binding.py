"""Server-authoritative tenant / org context from Clerk JWT (R-014)."""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from typing import Any, Literal

from clerk_backend_api.security import VerifyTokenOptions, verify_token as _clerk_verify_token
from clerk_backend_api.security.types import TokenVerificationError
from fastapi import HTTPException, Request, status

from auth.config import get_anonymous_org_id
from services.ops.structured_log import log_info

AuthOutcome = Literal["auth_missing", "auth_valid", "auth_invalid", "auth_error"]


@dataclass(frozen=True)
class TenantContext:
    """Normalized tenant context; never sourced from client JSON."""

    org_id: str
    user_id: str | None
    email: str | None
    auth_source: Literal["clerk_jwt", "anonymous"]
    auth_present: bool
    org_bound: bool


def extract_bearer_token(authorization: str | None) -> str | None:
    if not authorization or not authorization.strip():
        return None
    parts = authorization.strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None


def authenticate_from_authorization(
    authorization: str | None,
) -> tuple[AuthOutcome, dict[str, Any] | None, bool]:
    """
    Classify auth and return verified claims when valid.
    auth_present: client sent a non-empty Authorization header (may be invalid).
    """
    auth_present = bool((authorization or "").strip())
    token = extract_bearer_token(authorization)
    if token is None:
        if not authorization or not authorization.strip():
            return "auth_missing", None, False
        return "auth_invalid", None, auth_present

    sk = os.getenv("CLERK_SECRET_KEY", "").strip()
    if not sk:
        return "auth_error", None, auth_present

    try:
        p = _clerk_verify_token(token, VerifyTokenOptions(secret_key=sk))
        o = p.get("o")
        org_id = p.get("org_id") or (o.get("id") if isinstance(o, dict) else None)
        claims: dict[str, Any] = {
            "user_id": p.get("sub"),
            "email": p.get("email"),
            "org_id": org_id,
        }
        return "auth_valid", claims, auth_present
    except TokenVerificationError:
        return "auth_invalid", None, auth_present
    except Exception:
        return "auth_error", None, auth_present


def authenticate_request(request: Request) -> tuple[AuthOutcome, dict[str, Any] | None, bool]:
    return authenticate_from_authorization(request.headers.get("Authorization"))


def build_tenant_context(
    outcome: AuthOutcome,
    claims: dict[str, Any] | None,
    auth_present: bool,
) -> TenantContext:
    """Map auth outcome + claims to TenantContext. Raises 400 when JWT valid but org missing."""
    if outcome == "auth_valid":
        oid = (claims or {}).get("org_id")
        if not oid or not str(oid).strip():
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail="Organization context missing from token; select an organization in Clerk.",
            )
        oid_str = str(oid).strip()
        try:
            uuid.UUID(oid_str)
        except ValueError as e:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail="Invalid organization id in token",
            ) from e
        c = claims or {}
        uid = c.get("user_id")
        em = c.get("email")
        return TenantContext(
            org_id=oid_str,
            user_id=str(uid) if uid is not None else None,
            email=str(em) if em is not None else None,
            auth_source="clerk_jwt",
            auth_present=auth_present,
            org_bound=True,
        )

    anon = get_anonymous_org_id()
    try:
        uuid.UUID(anon)
    except ValueError as e:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Server anonymous org misconfigured (BEN_ANONYMOUS_ORG_ID)",
        ) from e

    return TenantContext(
        org_id=anon,
        user_id=None,
        email=None,
        auth_source="anonymous",
        auth_present=auth_present,
        org_bound=False,
    )


def validate_body_tenant_matches_context(body: Any, ctx: TenantContext) -> None:
    """
    When JWT-bound, reject forged tenant_id in body (optional field).
    Anonymous sessions ignore body tenant_id (server uses BEN_ANONYMOUS_ORG_ID).
    """
    if ctx.auth_source != "clerk_jwt":
        return
    tid = getattr(body, "tenant_id", None)
    if tid is None or str(tid).strip() == "":
        return
    try:
        body_u = uuid.UUID(str(tid).strip())
        ctx_u = uuid.UUID(str(ctx.org_id).strip())
    except ValueError as e:
        raise HTTPException(
            status_code=422,
            detail="Invalid tenant_id in request body",
        ) from e
    if body_u != ctx_u:
        raise HTTPException(
            status_code=422,
            detail="tenant_id does not match authenticated organization",
        )


def log_tenant_bound(*, route_operation: str, ctx: TenantContext) -> None:
    log_info(
        f"tenant bound for {route_operation}",
        subsystem="auth",
        operation="tenant_bind",
        auth_present=ctx.auth_present,
        org_bound=ctx.org_bound,
        auth_source=ctx.auth_source,
    )
