"""Data repository layer — virtual cloud connections and chunked local ingestion."""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field, field_validator

from auth.beta_gate import build_project_tenant_context_from_request
from services.global_service_store import global_channel_has_credentials
from services.ops.timing import measure
from services.project_feature_access import resolve_project_workspace_features
from services.project_tools import slugify_project_name
from services.repository_store import (
    connect_repository,
    get_repository,
    list_repositories,
    metadata_has_sensitive_tokens,
    stream_repository_upload,
    toggle_repository,
)

router = APIRouter(prefix="/api/projects", tags=["project-repositories"])

SourceTypeLiteral = Literal[
    "local",
    "google_drive",
    "external_library",
    "gmail",
    "sovereign_sonar",
]


class RepositoryConnectBody(BaseModel):
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


class RepositoryToggleResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_slug: str
    repository: dict[str, Any]
    tokens_scrubbed: bool


class ActiveFeaturesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_slug: str
    org_id: str
    active_features: list[dict[str, Any]]
    engines: list[dict[str, Any]]
    integrations: list[dict[str, Any]]
    catalog_keys: list[str]
    total_configured: int
    total_active: int


def _validate_project_slug(project_slug: str) -> str:
    slug = slugify_project_name(project_slug)
    if not slug:
        raise HTTPException(status_code=422, detail="invalid project_slug")
    return slug


def _org_id_from_request(ctx) -> str:
    return str(ctx.tenant_id)


def _parse_repository_id(repo_id: str) -> int:
    try:
        parsed = int(str(repo_id).strip())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="invalid repository_id") from exc
    if parsed <= 0:
        raise HTTPException(status_code=422, detail="invalid repository_id")
    return parsed


@router.get("/{project_slug}/active-features")
async def api_project_active_features(request: Request, project_slug: str):
    ctx = await build_project_tenant_context_from_request(
        request,
        route_operation="GET /api/projects/{slug}/active-features",
    )
    slug = _validate_project_slug(project_slug)
    org_id = _org_id_from_request(ctx)
    async with measure(subsystem="repositories", operation="GET /active-features"):
        try:
            payload = resolve_project_workspace_features(org_id, slug)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ActiveFeaturesResponse(**payload).model_dump()


@router.get("/{project_slug}/repositories")
async def api_list_repositories(request: Request, project_slug: str):
    ctx = await build_project_tenant_context_from_request(
        request,
        route_operation="GET /api/projects/{slug}/repositories",
    )
    slug = _validate_project_slug(project_slug)
    org_id = _org_id_from_request(ctx)
    async with measure(subsystem="repositories", operation="GET /repositories"):
        repositories = list_repositories(org_id)
    return {"project_slug": slug, "repositories": repositories}


@router.post("/{project_slug}/repositories/connect")
async def api_connect_repository(
    request: Request,
    project_slug: str,
    body: RepositoryConnectBody,
):
    ctx = await build_project_tenant_context_from_request(
        request,
        route_operation="POST /api/projects/{slug}/repositories/connect",
    )
    slug = _validate_project_slug(project_slug)
    org_id = _org_id_from_request(ctx)
    async with measure(subsystem="repositories", operation="POST /repositories/connect"):
        try:
            repository = connect_repository(
                org_id,
                slug,
                name=body.name,
                source_type=body.source_type,
                source_metadata=body.source_metadata,
                feature_flags=body.feature_flags,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"project_slug": slug, "repository": repository}


@router.post("/{project_slug}/repositories/upload")
async def api_upload_repository_file(
    request: Request,
    project_slug: str,
    file: UploadFile = File(...),
    repository_id: int = Form(..., gt=0),
):
    ctx = await build_project_tenant_context_from_request(
        request,
        route_operation="POST /api/projects/{slug}/repositories/upload",
    )
    slug = _validate_project_slug(project_slug)
    org_id = _org_id_from_request(ctx)
    async with measure(subsystem="repositories", operation="POST /repositories/upload"):
        try:
            record = await stream_repository_upload(org_id, slug, repository_id, file)
        except ValueError as exc:
            message = str(exc)
            status = 404 if "not found" in message.lower() else 400
            raise HTTPException(status_code=status, detail=message) from exc
    return {"project_slug": slug, "repository_id": repository_id, "file": record}


@router.post("/{project_slug}/repositories/{repo_id}/toggle")
async def api_toggle_repository(
    request: Request,
    project_slug: str,
    repo_id: str,
):
    ctx = await build_project_tenant_context_from_request(
        request,
        route_operation="POST /api/projects/{slug}/repositories/{id}/toggle",
    )
    slug = _validate_project_slug(project_slug)
    org_id = _org_id_from_request(ctx)
    repository_id = _parse_repository_id(repo_id)
    async with measure(subsystem="repositories", operation="POST /repositories/toggle"):
        before = get_repository(org_id, repository_id)
        if before is None:
            raise HTTPException(status_code=404, detail="repository not found")
        had_sensitive = metadata_has_sensitive_tokens(
            before.get("source_metadata", {})
        ) or global_channel_has_credentials(org_id, repository_id)
        try:
            repository = toggle_repository(org_id, repository_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RepositoryToggleResponse(
        project_slug=slug,
        repository=repository,
        tokens_scrubbed=had_sensitive,
    ).model_dump()
