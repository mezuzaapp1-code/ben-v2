"""Closed-beta passcode gate and per-auditor sandbox isolation (Task 010)."""
from __future__ import annotations

import hmac
import os
import re
import uuid

from fastapi import HTTPException, Request, status

from auth.config import _env_bool
from auth.tenant_binding import TenantContext, authenticate_request, build_tenant_context, log_tenant_bound

BETA_PASSCODE_HEADER = "X-Basalt-Beta-Passcode"
BETA_ALIAS_HEADER = "X-Basalt-Beta-Alias"
BETA_THEME_HEADER = "X-Basalt-Beta-Theme"
BETA_PROJECT_NAME_HEADER = "X-Basalt-Beta-Project-Name"

_BETA_ORG_NAMESPACE = uuid.UUID("ba10a100-0000-4000-8000-000000000010")
_ALIAS_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9 _.-]{0,63}$")


def is_local_beta_mode() -> bool:
    """When True, unsigned requests with passcode + alias use isolated auditor org scopes."""
    return _env_bool("BEN_LOCAL_BETA_MODE", False)


def configured_beta_passcode() -> str:
    return os.getenv("BEN_BETA_PASSCODE", "").strip()


def verify_beta_passcode(supplied: str | None) -> bool:
    expected = configured_beta_passcode()
    if not expected or not supplied:
        return False
    return hmac.compare_digest(supplied.strip(), expected)


def normalize_beta_alias(raw: str | None) -> str | None:
    if raw is None:
        return None
    alias = raw.strip()
    if not alias or not _ALIAS_RE.match(alias):
        return None
    return alias


def derive_beta_org_id(alias: str) -> uuid.UUID:
    """Deterministic UUID v5 sandbox org — one isolated scope per auditor alias."""
    normalized = normalize_beta_alias(alias)
    if not normalized:
        raise ValueError("invalid beta alias")
    return uuid.uuid5(_BETA_ORG_NAMESPACE, f"basalt-beta-auditor:{normalized.lower()}")


def extract_beta_alias(request: Request) -> str | None:
    if not is_local_beta_mode() or not verify_beta_passcode(request.headers.get(BETA_PASSCODE_HEADER)):
        return None
    return normalize_beta_alias(request.headers.get(BETA_ALIAS_HEADER))


def build_beta_auditor_context(alias: str) -> TenantContext:
    org_id = str(derive_beta_org_id(alias))
    return TenantContext(
        tenant_id=org_id,
        tenant_type="anonymous",
        user_id=alias,
        org_id=None,
        org_role=None,
        email=None,
        auth_source="beta_passcode",
        auth_present=True,
        org_bound=False,
    )


def maybe_beta_auditor_context(request: Request) -> TenantContext | None:
    alias = extract_beta_alias(request)
    if not alias:
        return None
    return build_beta_auditor_context(alias)


def extract_beta_feedback_meta(request: Request) -> dict[str, str] | None:
    alias = extract_beta_alias(request)
    if not alias:
        return None
    theme = (request.headers.get(BETA_THEME_HEADER) or "dark").strip().lower()
    if theme not in ("light", "dark"):
        theme = "dark"
    project_name = (request.headers.get(BETA_PROJECT_NAME_HEADER) or "").strip()[:512]
    return {
        "tester_alias": alias,
        "org_id": str(derive_beta_org_id(alias)),
        "theme": theme,
        "project_name": project_name,
    }


async def build_project_tenant_context_from_request(
    request: Request, *, route_operation: str
) -> TenantContext:
    """Clerk JWT when valid; else closed-beta passcode + alias-isolated org when enabled."""
    outcome, claims, auth_present = authenticate_request(request)
    if outcome == "auth_valid" and claims:
        ctx = build_tenant_context(outcome, claims, auth_present)
        if ctx.auth_source == "clerk_jwt" and ctx.tenant_type != "anonymous":
            log_tenant_bound(route_operation=route_operation, ctx=ctx)
            return ctx

    beta_ctx = maybe_beta_auditor_context(request)
    if beta_ctx:
        log_tenant_bound(route_operation=route_operation, ctx=beta_ctx)
        return beta_ctx

    if is_local_beta_mode() and verify_beta_passcode(request.headers.get(BETA_PASSCODE_HEADER)):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Beta alias required. Provide X-Basalt-Beta-Alias header.",
        )

    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unauthorized")
