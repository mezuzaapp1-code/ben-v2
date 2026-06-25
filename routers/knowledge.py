"""Knowledge base API — async SQLite CRUD, decoupled from chat stream."""
from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile

from auth.beta_gate import build_project_tenant_context_from_request
from pydantic import BaseModel, ConfigDict, Field

from services.knowledge_service import (
    add_knowledge_document,
    create_knowledge_base,
    delete_knowledge_base,
    delete_knowledge_document,
    list_knowledge_bases,
    list_knowledge_documents,
)
from services.knowledge_store import (
    assert_thread_matches_project_slug,
    build_active_attention_focus,
    list_knowledge_files,
    stream_knowledge_upload,
)
from services.project_tools import slugify_project_name

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])
project_knowledge_router = APIRouter(prefix="/api/projects", tags=["project-knowledge"])


class KnowledgeBaseCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=128)


class KnowledgeDocumentBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field("", max_length=512)
    content: str = Field(..., min_length=1, max_length=64000)


@router.get("/bases")
async def get_bases():
    bases = await list_knowledge_bases()
    return {"bases": bases}


@router.post("/bases")
async def post_base(body: KnowledgeBaseCreateBody):
    try:
        base = await create_knowledge_base(body.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        if "UNIQUE" in str(exc).upper():
            raise HTTPException(status_code=409, detail="Knowledge base already exists") from exc
        raise
    return base


@router.delete("/bases/{base_id}")
async def remove_base(base_id: int):
    if not await delete_knowledge_base(base_id):
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    return {"ok": True}


@router.get("/bases/{base_id}/documents")
async def get_documents(base_id: int):
    try:
        docs = await list_knowledge_documents(base_id)
    except Exception:
        docs = []
    return {"documents": docs}


@router.post("/bases/{base_id}/documents")
async def post_document(base_id: int, body: KnowledgeDocumentBody):
    try:
        doc = await add_knowledge_document(base_id, title=body.title, content=body.content)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return doc


@router.delete("/documents/{doc_id}")
async def remove_document(doc_id: int):
    if not await delete_knowledge_document(doc_id):
        raise HTTPException(status_code=404, detail="Document not found")
    return {"ok": True}


def _validate_project_slug(project_slug: str) -> str:
    slug = slugify_project_name(project_slug)
    if not slug:
        raise HTTPException(status_code=422, detail="invalid project_slug")
    return slug


@project_knowledge_router.get("/{project_slug}/knowledge/files")
async def list_project_knowledge_files(request: Request, project_slug: str):
    await build_project_tenant_context_from_request(
        request,
        route_operation="GET /api/projects/{slug}/knowledge/files",
    )
    slug = _validate_project_slug(project_slug)
    files = list_knowledge_files(slug)
    return {"project_slug": slug, "files": files}


@project_knowledge_router.post("/{project_slug}/knowledge/upload-stream")
async def upload_project_knowledge_stream(
    request: Request,
    project_slug: str,
    file: UploadFile = File(...),
):
    await build_project_tenant_context_from_request(
        request,
        route_operation="POST /api/projects/{slug}/knowledge/upload-stream",
    )
    slug = _validate_project_slug(project_slug)
    try:
        record = await stream_knowledge_upload(slug, file)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"project_slug": slug, "file": record}


@project_knowledge_router.get("/{project_slug}/threads/{thread_id}/active-attention")
async def get_active_attention_focus(
    request: Request,
    project_slug: str,
    thread_id: str,
    query: str = Query(..., min_length=1, max_length=4096),
):
    await build_project_tenant_context_from_request(
        request,
        route_operation="GET /api/projects/{slug}/threads/{id}/active-attention",
    )
    slug = _validate_project_slug(project_slug)
    clean_query = query.strip()
    if not clean_query:
        raise HTTPException(status_code=422, detail="query is required")
    try:
        assert_thread_matches_project_slug(slug, thread_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    payload = build_active_attention_focus(slug, clean_query, limit_per_head=3)
    payload["thread_id"] = thread_id
    return payload
