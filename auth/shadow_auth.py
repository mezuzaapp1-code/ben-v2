"""Shadow-mode auth checks: log outcomes, enforce only when ENFORCE_AUTH=true."""
from __future__ import annotations

import os
from typing import Literal

from fastapi import HTTPException, Request, status
from clerk_backend_api.security import VerifyTokenOptions, verify_token as _clerk_verify_token
from clerk_backend_api.security.types import TokenVerificationError

from auth.config import is_auth_shadow_mode, is_enforce_auth
from services.ops.structured_log import log_info

AuthOutcome = Literal["auth_missing", "auth_valid", "auth_invalid", "auth_error"]

# TODO(R-014): bind body tenant_id to verified org_id from claims; body tenant_id is temporary.


def _extract_bearer(authorization: str | None) -> str | None:
    if not authorization or not authorization.strip():
        return None
    parts = authorization.strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None


def evaluate_auth_outcome(authorization: str | None) -> AuthOutcome:
    """Classify Authorization header without logging token or header value."""
    token = _extract_bearer(authorization)
    if token is None:
        if not authorization or not authorization.strip():
            return "auth_missing"
        return "auth_invalid"

    sk = os.getenv("CLERK_SECRET_KEY", "").strip()
    if not sk:
        return "auth_error"

    try:
        _clerk_verify_token(token, VerifyTokenOptions(secret_key=sk))
        return "auth_valid"
    except TokenVerificationError:
        return "auth_invalid"
    except Exception:
        return "auth_error"


def log_shadow_auth_check(*, route_operation: str, outcome: AuthOutcome) -> None:
    log_info(
        f"shadow auth check for {route_operation}",
        subsystem="auth",
        operation="shadow_auth_check",
        outcome=outcome,
    )


async def apply_auth_policy(request: Request, *, route_operation: str) -> None:
    """
    Shadow log when AUTH_SHADOW_MODE=true; reject with 401 only when ENFORCE_AUTH=true
    and outcome is not auth_valid.
    """
    outcome = evaluate_auth_outcome(request.headers.get("Authorization"))

    if is_auth_shadow_mode():
        log_shadow_auth_check(route_operation=route_operation, outcome=outcome)

    if is_enforce_auth() and outcome != "auth_valid":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unauthorized")
