"""Workspace File Library API — Project/Workspace scoped uploads.

Domain-isolated from News: this router must not call News services, return News
records, or write SourceDocumentVersion / NewsArticle rows.
"""
from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse

from auth.beta_gate import maybe_beta_auditor_context
from auth.persistent_access import assert_persistent_customer_identity
from auth.tenant_binding import (
    authenticate_request,
    build_tenant_context,
    log_tenant_bound,
)
from services.ops.timing import measure
from services.workspace_files import service as file_service

router = APIRouter(prefix="/api/workspaces", tags=["workspace-files"])


def _org_from_ctx(ctx) -> uuid.UUID:
    return uuid.UUID(ctx.tenant_id)


def _principal(ctx) -> str | None:
    return getattr(ctx, "user_id", None) or getattr(ctx, "principal_id", None) or ctx.tenant_id


async def _require_files_tenant(request: Request, *, route_operation: str):
    """Persistent file operations require a customer identity (Gate A).

    Clerk personal/org JWT or isolated beta alias. Never the shared anonymous org,
    regardless of ENFORCE_AUTH.
    """
    outcome, claims, auth_present = authenticate_request(request)
    beta_ctx = maybe_beta_auditor_context(request)
    if beta_ctx:
        log_tenant_bound(route_operation=route_operation, ctx=beta_ctx)
        return beta_ctx

    if outcome == "auth_valid" and claims:
        ctx = assert_persistent_customer_identity(
            build_tenant_context(outcome, claims, auth_present)
        )
        log_tenant_bound(route_operation=route_operation, ctx=ctx)
        return ctx

    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unauthorized")


@router.post("/{workspace_id}/files")
async def upload_workspace_file(
    request: Request,
    workspace_id: uuid.UUID,
    file: UploadFile = File(...),
    source_chat_id: str | None = Form(None),
):
    ctx = await _require_files_tenant(
        request, route_operation="POST /api/workspaces/{workspace_id}/files"
    )
    async with measure(subsystem="workspace_files", operation="upload"):
        return await file_service.upload_file(
            org_id=_org_from_ctx(ctx),
            workspace_id=workspace_id,
            upload=file,
            uploaded_by=str(_principal(ctx)) if _principal(ctx) else None,
            source_chat_id=source_chat_id,
        )


@router.get("/{workspace_id}/files")
async def list_workspace_files(
    request: Request,
    workspace_id: uuid.UUID,
    status_filter: str | None = Query(None, alias="status"),
    q: str | None = Query(None),
    limit: int = Query(100, ge=1, le=200),
):
    ctx = await _require_files_tenant(
        request, route_operation="GET /api/workspaces/{workspace_id}/files"
    )
    async with measure(subsystem="workspace_files", operation="list"):
        return await file_service.list_files(
            org_id=_org_from_ctx(ctx),
            workspace_id=workspace_id,
            status_filter=status_filter,
            q=q,
            limit=limit,
        )


@router.get("/{workspace_id}/files/{file_id}")
async def get_workspace_file(
    request: Request,
    workspace_id: uuid.UUID,
    file_id: uuid.UUID,
    include_text_preview: bool = Query(False),
):
    ctx = await _require_files_tenant(
        request, route_operation="GET /api/workspaces/{workspace_id}/files/{file_id}"
    )
    async with measure(subsystem="workspace_files", operation="get"):
        return await file_service.get_file(
            org_id=_org_from_ctx(ctx),
            workspace_id=workspace_id,
            file_id=file_id,
            include_text_preview=include_text_preview,
        )


@router.get("/{workspace_id}/files/{file_id}/content")
async def download_workspace_file(
    request: Request,
    workspace_id: uuid.UUID,
    file_id: uuid.UUID,
    inline: bool = Query(True),
):
    ctx = await _require_files_tenant(
        request,
        route_operation="GET /api/workspaces/{workspace_id}/files/{file_id}/content",
    )
    path, media_type, name = await file_service.open_file_bytes(
        org_id=_org_from_ctx(ctx),
        workspace_id=workspace_id,
        file_id=file_id,
    )
    disposition = "inline" if inline else "attachment"
    return FileResponse(
        path=str(path),
        media_type=media_type or "application/octet-stream",
        filename=name,
        content_disposition_type=disposition,
    )


@router.post("/{workspace_id}/files/{file_id}/retry")
async def retry_workspace_file(
    request: Request,
    workspace_id: uuid.UUID,
    file_id: uuid.UUID,
):
    ctx = await _require_files_tenant(
        request,
        route_operation="POST /api/workspaces/{workspace_id}/files/{file_id}/retry",
    )
    async with measure(subsystem="workspace_files", operation="retry"):
        return await file_service.process_file(
            org_id=_org_from_ctx(ctx),
            workspace_id=workspace_id,
            file_id=file_id,
        )


@router.delete("/{workspace_id}/files/{file_id}")
async def delete_workspace_file(
    request: Request,
    workspace_id: uuid.UUID,
    file_id: uuid.UUID,
):
    ctx = await _require_files_tenant(
        request,
        route_operation="DELETE /api/workspaces/{workspace_id}/files/{file_id}",
    )
    async with measure(subsystem="workspace_files", operation="delete"):
        return await file_service.delete_file(
            org_id=_org_from_ctx(ctx),
            workspace_id=workspace_id,
            file_id=file_id,
        )


# Convenience alias under /api/projects/{id}/files for existing project UUID UX.
project_alias_router = APIRouter(prefix="/api/projects", tags=["workspace-files"])


@project_alias_router.post("/{project_id}/files")
async def upload_project_file(
    request: Request,
    project_id: uuid.UUID,
    file: UploadFile = File(...),
    source_chat_id: str | None = Form(None),
):
    return await upload_workspace_file(
        request, project_id, file=file, source_chat_id=source_chat_id
    )


@project_alias_router.get("/{project_id}/files")
async def list_project_files(
    request: Request,
    project_id: uuid.UUID,
    status_filter: str | None = Query(None, alias="status"),
    q: str | None = Query(None),
    limit: int = Query(100, ge=1, le=200),
):
    return await list_workspace_files(
        request, project_id, status_filter=status_filter, q=q, limit=limit
    )


@project_alias_router.get("/{project_id}/files/{file_id}")
async def get_project_file(
    request: Request,
    project_id: uuid.UUID,
    file_id: uuid.UUID,
    include_text_preview: bool = Query(False),
):
    return await get_workspace_file(
        request, project_id, file_id, include_text_preview=include_text_preview
    )


@project_alias_router.get("/{project_id}/files/{file_id}/content")
async def download_project_file(
    request: Request,
    project_id: uuid.UUID,
    file_id: uuid.UUID,
    inline: bool = Query(True),
):
    return await download_workspace_file(
        request, project_id, file_id, inline=inline
    )


@project_alias_router.post("/{project_id}/files/{file_id}/retry")
async def retry_project_file(
    request: Request,
    project_id: uuid.UUID,
    file_id: uuid.UUID,
):
    return await retry_workspace_file(request, project_id, file_id)


@project_alias_router.delete("/{project_id}/files/{file_id}")
async def delete_project_file(
    request: Request,
    project_id: uuid.UUID,
    file_id: uuid.UUID,
):
    return await delete_workspace_file(request, project_id, file_id)
