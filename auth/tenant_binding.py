"""Server-authoritative tenant context from Clerk JWT (R-014, tenant mode v2)."""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from typing import Any, Literal

from clerk_backend_api.security import VerifyTokenOptions, verify_token as _clerk_verify_token
from clerk_backend_api.security.types import TokenVerificationError
from fastapi import HTTPException, Request, status

from auth.config import get_anonymous_org_id
from auth.org_errors import raise_clerk_org_required
from auth.tenant_ids import personal_tenant_id
from auth.tenant_policy import require_org_for_signed_in
from services.ops.structured_log import log_info, log_warning

AuthOutcome = Literal["auth_missing", "auth_valid", "auth_invalid", "auth_error"]
TenantType = Literal["personal", "organization", "anonymous"]


@dataclass(frozen=True)
class TenantContext:
    """
    Normalized tenant context; never sourced from client JSON.

    tenant_id: UUID string used for DB RLS and thread isolation (effective scope).
    org_id: Clerk organization UUID when tenant_type is organization; else None.
    """

    tenant_id: str
    tenant_type: TenantType
    user_id: str | None
    org_id: str | None
    email: str | None
    auth_source: Literal["clerk_jwt", "anonymous"]
    auth_present: bool
    org_bound: bool

    @property
    def scope_org_id(self) -> str:
        """Backward-compatible alias: effective tenant scope for persistence APIs."""
        return self.tenant_id


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


def _validate_uuid_tenant(value: str, *, field_name: str) -> str:
    try:
        return str(uuid.UUID(str(value).strip()))
    except ValueError as e:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid {field_name}",
        ) from e


def build_tenant_context(
    outcome: AuthOutcome,
    claims: dict[str, Any] | None,
    auth_present: bool,
) -> TenantContext:
    """Derive tenant from verified JWT only; personal workspace when no org (default policy)."""
    if outcome == "auth_valid":
        c = claims or {}
        uid = c.get("user_id")
        if uid is None or not str(uid).strip():
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Invalid user id in token")
        uid_str = str(uid).strip()
        em = c.get("email")
        email = str(em) if em is not None else None

        oid = c.get("org_id")
        if oid and str(oid).strip():
            oid_str = _validate_uuid_tenant(str(oid).strip(), field_name="organization id in token")
            return TenantContext(
                tenant_id=oid_str,
                tenant_type="organization",
                user_id=uid_str,
                org_id=oid_str,
                email=email,
                auth_source="clerk_jwt",
                auth_present=auth_present,
                org_bound=True,
            )

        if require_org_for_signed_in():
            log_warning(
                "signed-in user missing Clerk organization (org required by policy)",
                subsystem="auth",
                operation="clerk_org_required",
                category="config_error",
                outcome="error",
            )
            raise_clerk_org_required()

        personal_id = _validate_uuid_tenant(personal_tenant_id(uid_str), field_name="personal tenant id")
        return TenantContext(
            tenant_id=personal_id,
            tenant_type="personal",
            user_id=uid_str,
            org_id=None,
            email=email,
            auth_source="clerk_jwt",
            auth_present=auth_present,
            org_bound=False,
        )

    anon = get_anonymous_org_id()
    anon_str = _validate_uuid_tenant(anon, field_name="anonymous org id")

    return TenantContext(
        tenant_id=anon_str,
        tenant_type="anonymous",
        user_id=None,
        org_id=None,
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
    body_scope = _validate_uuid_tenant(str(tid).strip(), field_name="tenant_id in request body")
    ctx_scope = _validate_uuid_tenant(ctx.tenant_id, field_name="tenant scope")
    if body_scope != ctx_scope:
        raise HTTPException(
            status_code=422,
            detail="tenant_id does not match authenticated tenant scope",
        )


def log_tenant_bound(*, route_operation: str, ctx: TenantContext) -> None:
    log_info(
        f"tenant bound for {route_operation}",
        subsystem="auth",
        operation="tenant_bind",
        auth_present=ctx.auth_present,
        org_bound=ctx.org_bound,
        auth_source=ctx.auth_source,
        tenant_type=ctx.tenant_type,
    )