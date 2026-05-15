"""Shadow-mode auth checks: log outcomes, enforce only when ENFORCE_AUTH=true."""
from __future__ import annotations

from fastapi import HTTPException, Request, status

from auth.config import is_auth_shadow_mode, is_enforce_auth
from auth.tenant_binding import AuthOutcome, authenticate_request
from services.ops.structured_log import log_info


def evaluate_auth_outcome(authorization: str | None) -> AuthOutcome:
    """Classify Authorization header without logging token or header value."""
    outcome, _claims, _ap = authenticate_from_authorization(authorization)
    return outcome


def log_shadow_auth_check(*, route_operation: str, outcome: AuthOutcome) -> None:
    log_info(
        f"shadow auth check for {route_operation}",
        subsystem="auth",
        operation="shadow_auth_check",
        outcome=outcome,
    )


async def apply_auth_policy(
    request: Request, *, route_operation: str
) -> tuple[AuthOutcome, dict | None, bool]:
    """
    Shadow log when AUTH_SHADOW_MODE=true; reject with 401 only when ENFORCE_AUTH=true
    and outcome is not auth_valid.

    Returns (auth outcome, claims dict or None, auth header present).
    """
    outcome, claims, auth_present = authenticate_request(request)

    if is_auth_shadow_mode():
        log_shadow_auth_check(route_operation=route_operation, outcome=outcome)

    if is_enforce_auth() and outcome != "auth_valid":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unauthorized")
    return outcome, claims, auth_present
