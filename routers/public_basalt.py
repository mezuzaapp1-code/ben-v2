"""Public Basalt corporate API — rate-limited, org-isolated (no client org injection)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from services.basalt_content_schema import build_corporate_content, normalize_lang
from services.basalt_public_service import (
    fetch_active_job_openings,
    fetch_verified_portfolio,
    resolve_basalt_org_id,
    submit_candidate_application,
)
from services.basalt_rate_limit import enforce_basalt_rate_limit
from services.ops.request_context import attach_request_id

router = APIRouter(prefix="/api/public/basalt", tags=["public-basalt"])


class CertificationUpload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cert_type: str = Field(..., description="height_safety | electrical_credentials | classified_zone | welding_safety")
    filename: str = Field(..., min_length=1, max_length=256)
    content_base64: str | None = Field(None, description="Optional base64 payload (metadata stored server-side)")


class BasaltApplyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_name: str = Field(..., min_length=1, max_length=256)
    email: str | None = Field(None, max_length=320)
    phone: str | None = Field(None, max_length=64)
    resume_text: str | None = Field(None, max_length=16000)
    desired_role: str | None = Field(None, max_length=128)
    certifications: list[CertificationUpload] = Field(default_factory=list)
    lang: str | None = Field(None, description="en (default) or he")


@router.get("/content")
async def get_basalt_content(
    request: Request,
    lang: str | None = Query(None, description="en or he"),
) -> dict[str, Any]:
    enforce_basalt_rate_limit(request, "content")
    org_id = resolve_basalt_org_id()
    content = build_corporate_content(lang)
    return attach_request_id({"org_id": str(org_id), **content})


@router.get("/jobs")
async def get_basalt_jobs(
    request: Request,
    lang: str | None = Query(None, description="en or he"),
) -> dict[str, Any]:
    enforce_basalt_rate_limit(request, "jobs")
    org_id = resolve_basalt_org_id()
    result = await fetch_active_job_openings(org_id, lang=lang)
    return attach_request_id(result)


@router.post("/apply")
async def post_basalt_apply(request: Request, body: BasaltApplyBody) -> dict[str, Any]:
    enforce_basalt_rate_limit(request, "apply")
    org_id = resolve_basalt_org_id()
    try:
        result = await submit_candidate_application(
            org_id,
            candidate_name=body.candidate_name,
            email=body.email,
            phone=body.phone,
            resume_text=body.resume_text,
            desired_role=body.desired_role,
            certifications=[c.model_dump() for c in body.certifications],
            lang=body.lang,
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e
    return attach_request_id(result)


@router.get("/portfolio")
async def get_basalt_portfolio(
    request: Request,
    lang: str | None = Query(None, description="en or he"),
) -> dict[str, Any]:
    enforce_basalt_rate_limit(request, "portfolio")
    org_id = resolve_basalt_org_id()
    result = await fetch_verified_portfolio(org_id, lang=lang)
    return attach_request_id(result)
