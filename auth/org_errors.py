"""Structured recoverable errors for Clerk organization context (no silent org fallback)."""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException

CLERK_ORG_REQUIRED_CODE = "clerk_org_required"

CLERK_ORG_REQUIRED_MESSAGE = (
    "Please select or create an organization in Clerk to continue."
)

CLERK_ORG_REQUIRED_HINT = (
    "Sign out and continue anonymously, or select an organization using the switcher above."
)


def clerk_org_required_detail() -> dict[str, Any]:
    return {
        "code": CLERK_ORG_REQUIRED_CODE,
        "message": CLERK_ORG_REQUIRED_MESSAGE,
        "hint": CLERK_ORG_REQUIRED_HINT,
        "recoverable": True,
    }


def raise_clerk_org_required() -> None:
    """Signed-in JWT valid but missing org claim — never fall back to anonymous org."""
    raise HTTPException(status_code=403, detail=clerk_org_required_detail())


def is_clerk_org_required_detail(detail: Any) -> bool:
    if isinstance(detail, dict):
        return detail.get("code") == CLERK_ORG_REQUIRED_CODE
    if isinstance(detail, str):
        return "organization context missing" in detail.lower()
    return False
