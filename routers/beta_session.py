"""Beta session resolution for auditor sandboxes."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from auth.beta_gate import derive_beta_org_id, normalize_beta_alias, verify_beta_passcode
from services.ops.request_context import attach_request_id

router = APIRouter(prefix="/api/beta", tags=["beta"])


class BetaSessionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passcode: str = Field(..., min_length=1, max_length=256)
    alias: str = Field(..., min_length=1, max_length=64)


@router.post("/session")
async def resolve_beta_session(body: BetaSessionBody) -> dict:
    if not verify_beta_passcode(body.passcode):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid beta passcode")
    alias = normalize_beta_alias(body.alias)
    if not alias:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid alias format")
    org_id = derive_beta_org_id(alias)
    return attach_request_id(
        {
            "alias": alias,
            "org_id": str(org_id),
        }
    )
