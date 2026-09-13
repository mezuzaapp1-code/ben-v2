"""Knowledge base API — contained. Existing data is not deleted."""
from __future__ import annotations

from fastapi import APIRouter, File, Query, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from services.knowledge_containment import raise_legacy_knowledge_contained

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])
project_knowledge_router = APIRouter(prefix="/api/projects", tags=["project-knowledge"])

# Decoded-character ceiling for GET active-attention ?query=. Keep in sync with
# frontend/src/lib/attentionQuery.js ATTENTION_QUERY_SERVER_MAX_CHARS.
# Chat /chat/stream is intentionally NOT bounded by this value.
ACTIVE_ATTENTION_QUERY_MAX_CHARS = 4096


class KnowledgeBaseCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=128)


class KnowledgeDocumentBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field("", max_length=512)
    content: str = Field(..., min_length=1, max_length=64000)


@router.get("/bases")
async def get_bases():
    raise_legacy_knowledge_contained()


@router.post("/bases")
async def post_base(body: KnowledgeBaseCreateBody):
    del body
    raise_legacy_knowledge_contained()


@router.delete("/bases/{base_id}")
async def remove_base(base_id: int):
    del base_id
    raise_legacy_knowledge_contained()


@router.get("/bases/{base_id}/documents")
async def get_documents(base_id: int):
    del base_id
    raise_legacy_knowledge_contained()


@router.post("/bases/{base_id}/documents")
async def post_document(base_id: int, body: KnowledgeDocumentBody):
    del base_id, body
    raise_legacy_knowledge_contained()


@router.delete("/documents/{doc_id}")
async def remove_document(doc_id: int):
    del doc_id
    raise_legacy_knowledge_contained()


@project_knowledge_router.get("/{project_slug}/knowledge/files")
async def list_project_knowledge_files(request: Request, project_slug: str):
    del request, project_slug
    raise_legacy_knowledge_contained()


@project_knowledge_router.post("/{project_slug}/knowledge/upload-stream")
async def upload_project_knowledge_stream(
    request: Request,
    project_slug: str,
    file: UploadFile = File(...),
):
    del request, project_slug, file
    raise_legacy_knowledge_contained()


@project_knowledge_router.get("/{project_slug}/threads/{thread_id}/active-attention")
async def get_active_attention_focus(
    request: Request,
    project_slug: str,
    thread_id: str,
    query: str = Query(..., min_length=1, max_length=ACTIVE_ATTENTION_QUERY_MAX_CHARS),
):
    del request, project_slug, thread_id, query
    raise_legacy_knowledge_contained()
