"""Platform-wide capability catalog — global engine and channel switchboard."""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from auth.beta_gate import build_project_tenant_context_from_request
from services.global_service_store import global_channel_has_credentials, metadata_has_sensitive_tokens
from services.ops.timing import measure
from services.platform_feature_access import (
    connect_platform_capability,
    resolve_platform_features,
    toggle_platform_capability,
)

router = APIRouter(prefix="/api/platform", tags=["platform-capabilities"])

SourceTypeLiteral = Literal[
    "local",
    "google_drive",
    "external_library",
    "gmail",
    "sovereign_sonar",
]


class CapabilityConnectBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=256)
    source_type: SourceTypeLiteral
    source_metadata: dict[str, Any] = Field(default_factory=dict)
    feature_flags: dict[str, bool] = Field(default_factory=dict)

    @field_validator("source_metadata")
    @classmethod
    def validate_metadata_size(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(value) > 32:
            raise ValueError("source_metadata accepts at most 32 keys")
        return value

    @field_validator("feature_flags")
    @classmethod
    def validate_feature_flags(cls, value: dict[str, bool]) -> dict[str, bool]:
        if len(value) > 16:
            raise ValueError("feature_flags accepts at most 16 keys")
        return value


class PlatformActiveFeaturesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    org_id: str
    active_features: list[dict[str, Any]]
    engines: list[dict[str, Any]]
    integrations: list[dict[str, Any]]
    catalog_keys: list[str]
    total_configured: int
    total_active: int


class CapabilityToggleResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capability: dict[str, Any]
    tokens_scrubbed: bool


def _org_id_from_request(ctx) -> str:
    return str(ctx.tenant_id)


def _parse_channel_id(channel_id: str) -> int:
    try:
        parsed = int(str(channel_id).strip())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="invalid channel_id") from exc
    if parsed <= 0:
        raise HTTPException(status_code=422, detail="invalid channel_id")
    return parsed


@router.get("/active-features")
async def api_platform_active_features(request: Request):
    ctx = await build_project_tenant_context_from_request(
        request,
        route_operation="GET /api/platform/active-features",
    )
    org_id = _org_id_from_request(ctx)
    async with measure(subsystem="platform", operation="GET /active-features"):
        payload = resolve_platform_features(org_id)
    return PlatformActiveFeaturesResponse(**payload).model_dump()


@router.post("/capabilities/connect")
async def api_connect_platform_capability(request: Request, body: CapabilityConnectBody):
    ctx = await build_project_tenant_context_from_request(
        request,
        route_operation="POST /api/platform/capabilities/connect",
    )
    org_id = _org_id_from_request(ctx)
    async with measure(subsystem="platform", operation="POST /capabilities/connect"):
        try:
            capability = connect_platform_capability(
                org_id,
                name=body.name,
                source_type=body.source_type,
                source_metadata=body.source_metadata,
                feature_flags=body.feature_flags,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"capability": capability}


@router.post("/capabilities/{channel_id}/toggle")
async def api_toggle_platform_capability(request: Request, channel_id: str):
    ctx = await build_project_tenant_context_from_request(
        request,
        route_operation="POST /api/platform/capabilities/{id}/toggle",
    )
    org_id = _org_id_from_request(ctx)
    parsed_id = _parse_channel_id(channel_id)
    async with measure(subsystem="platform", operation="POST /capabilities/toggle"):
        from services.global_service_store import get_global_channel

        before = get_global_channel(org_id, parsed_id)
        if before is None:
            raise HTTPException(status_code=404, detail="capability not found")
        had_sensitive = metadata_has_sensitive_tokens(
            before.get("source_metadata", {})
        ) or global_channel_has_credentials(org_id, parsed_id)
        try:
            capability = toggle_platform_capability(org_id, parsed_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    return CapabilityToggleResponse(
        capability=capability,
        tokens_scrubbed=had_sensitive,
    ).model_dump()
