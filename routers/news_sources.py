"""Internal News API — registry (N2), collect (N3), article ops (N4), EventPackage (E0), claims (E1).

Product consumers (Feed, Ask BEN, Alerts, Daily Brief) MUST use EventPackage routes only.
Raw article list/detail and claim extraction are operator/acquisition inspection —
not product feed sources. E1 does not create Events from claims.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from auth.beta_gate import build_project_tenant_context_from_request
from auth.news_registry_privileges import assert_can_manage_news_sources
from services.news import (
    article_read_service,
    claim_extraction_service,
    event_package_service,
    source_registry,
)
from services.news.collect_service import collect_source
from services.news.event_package import EventPackage
from services.news.feed_url import validate_feed_url
from services.ops.request_context import get_request_id

router = APIRouter(prefix="/api/internal/news", tags=["news-sources"])


def _trim(value: str) -> str:
    return value.strip()


class NewsSourceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=256)
    feed_url: str = Field(..., min_length=1, max_length=2048)
    category: str = Field(..., min_length=1, max_length=64)
    language: str = Field("en", min_length=2, max_length=8)
    enabled: bool = True

    @field_validator("name", "feed_url", "category", "language", mode="before")
    @classmethod
    def _strip_strings(cls, v: Any) -> Any:
        if isinstance(v, str):
            return _trim(v)
        return v


class NewsSourceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(None, min_length=1, max_length=256)
    feed_url: str | None = Field(None, min_length=1, max_length=2048)
    category: str | None = Field(None, min_length=1, max_length=64)
    language: str | None = Field(None, min_length=2, max_length=8)

    @field_validator("name", "feed_url", "category", "language", mode="before")
    @classmethod
    def _strip_optional(cls, v: Any) -> Any:
        if isinstance(v, str):
            return _trim(v)
        return v

    @model_validator(mode="after")
    def _reject_empty_patch(self) -> NewsSourceUpdate:
        if (
            self.name is None
            and self.feed_url is None
            and self.category is None
            and self.language is None
        ):
            raise ValueError("At least one of name, feed_url, category, language is required")
        return self


class NewsSourceValidateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feed_url: str = Field(..., min_length=1, max_length=2048)

    @field_validator("feed_url", mode="before")
    @classmethod
    def _strip_url(cls, v: Any) -> Any:
        if isinstance(v, str):
            return _trim(v)
        return v


class NewsSourceOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    feed_url: str
    category: str
    language: str
    enabled: bool
    created_at: datetime | str | None = None
    updated_at: datetime | str | None = None


class NewsArticleOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    source_id: str
    guid: str
    title: str
    url: str
    summary: str | None = None
    image_url: str | None = None
    published_at: datetime | str | None = None
    category: str
    created_at: datetime | str | None = None


async def _require_news_admin(request: Request, *, route_operation: str):
    ctx = await build_project_tenant_context_from_request(
        request, route_operation=route_operation
    )
    assert_can_manage_news_sources(ctx)
    return ctx


@router.get("/sources")
async def list_news_sources(
    request: Request,
    enabled: bool | None = Query(None),
    category: str | None = Query(None, max_length=64),
    language: str | None = Query(None, max_length=8),
):
    await _require_news_admin(request, route_operation="news_sources_list")
    return await source_registry.list_sources(
        enabled=enabled,
        category=category,
        language=language,
    )


@router.post("/sources", status_code=status.HTTP_201_CREATED)
async def create_news_source(request: Request, body: NewsSourceCreate):
    await _require_news_admin(request, route_operation="news_sources_create")
    return await source_registry.create_source(
        name=body.name,
        feed_url=body.feed_url,
        category=body.category,
        language=body.language,
        enabled=body.enabled,
    )


@router.post("/sources/validate")
async def validate_news_source_url(request: Request, body: NewsSourceValidateBody):
    await _require_news_admin(request, route_operation="news_sources_validate")
    return validate_feed_url(body.feed_url)


@router.get("/sources/{source_id}")
async def get_news_source(request: Request, source_id: uuid.UUID):
    await _require_news_admin(request, route_operation="news_sources_get")
    return await source_registry.get_source(source_id)


@router.patch("/sources/{source_id}")
async def update_news_source(request: Request, source_id: uuid.UUID, body: NewsSourceUpdate):
    await _require_news_admin(request, route_operation="news_sources_update")
    return await source_registry.update_source(
        source_id,
        name=body.name,
        feed_url=body.feed_url,
        category=body.category,
        language=body.language,
    )


@router.post("/sources/{source_id}/enable")
async def enable_news_source(request: Request, source_id: uuid.UUID):
    await _require_news_admin(request, route_operation="news_sources_enable")
    return await source_registry.set_enabled(source_id, enabled=True)


@router.post("/sources/{source_id}/disable")
async def disable_news_source(request: Request, source_id: uuid.UUID):
    await _require_news_admin(request, route_operation="news_sources_disable")
    return await source_registry.set_enabled(source_id, enabled=False)


def _collect_http_status(result) -> int:
    if result.status == "succeeded":
        return status.HTTP_200_OK
    err = result.error
    error_class = err.error_class if err else "internal_error"
    if result.status == "rejected":
        if error_class == "source_not_found":
            return status.HTTP_404_NOT_FOUND
        if error_class == "source_disabled":
            return status.HTTP_409_CONFLICT
        return status.HTTP_422_UNPROCESSABLE_ENTITY
    if error_class == "concurrency_conflict":
        return status.HTTP_409_CONFLICT
    if error_class in ("timeout",) or (
        error_class == "http_error" and err and err.retryable
    ):
        return status.HTTP_502_BAD_GATEWAY
    if error_class in ("persist_error", "internal_error"):
        return status.HTTP_500_INTERNAL_SERVER_ERROR
    return status.HTTP_422_UNPROCESSABLE_ENTITY


@router.post("/sources/{source_id}/collect")
async def collect_news_source(request: Request, source_id: uuid.UUID):
    await _require_news_admin(request, route_operation="news_sources_collect")
    result = await collect_source(source_id, request_id=get_request_id())
    return JSONResponse(status_code=_collect_http_status(result), content=result.to_dict())


@router.get("/articles")
async def list_news_articles(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    cursor: str | None = Query(None),
    source_id: uuid.UUID | None = Query(None),
    category: str | None = Query(None, max_length=64),
):
    """Operator inspection of raw articles — not a product consumer path."""
    await _require_news_admin(request, route_operation="news_articles_list")
    return await article_read_service.list_articles(
        limit=limit,
        cursor=cursor,
        source_id=source_id,
        category=category,
    )


@router.get("/articles/{article_id}")
async def get_news_article(request: Request, article_id: uuid.UUID):
    """Operator inspection of raw articles — not a product consumer path."""
    await _require_news_admin(request, route_operation="news_articles_get")
    return await article_read_service.get_article(article_id)


@router.post("/articles/{article_id}/claims/extract")
async def extract_news_article_claims(request: Request, article_id: uuid.UUID):
    """Operator: extract atomic claims for one article (E1). Does not create Events."""
    await _require_news_admin(request, route_operation="news_claims_extract")
    return await claim_extraction_service.extract_article_claims(article_id)


@router.get("/articles/{article_id}/claims/extraction")
async def get_news_article_claim_extraction(request: Request, article_id: uuid.UUID):
    """Operator: inspect claim extraction status/errors for one article."""
    await _require_news_admin(request, route_operation="news_claims_extraction_status")
    return await claim_extraction_service.get_article_extraction(article_id)


@router.get("/articles/{article_id}/claims")
async def list_news_article_claims(
    request: Request,
    article_id: uuid.UUID,
    include_superseded: bool = Query(False),
):
    """Operator: list claims for one article. Not a product consumer path."""
    await _require_news_admin(request, route_operation="news_claims_list")
    return await claim_extraction_service.list_article_claims(
        article_id,
        include_superseded=include_superseded,
    )

@router.get("/events")
async def list_news_event_packages(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    lifecycle: str | None = Query(None, max_length=32),
    brief_eligible: bool | None = Query(None),
    alert_worthy: bool | None = Query(None),
):
    """Product-consumer list: current EventPackage v1 payloads only."""
    await _require_news_admin(request, route_operation="news_events_list")
    return await event_package_service.list_event_packages(
        limit=limit,
        lifecycle=lifecycle,
        brief_eligible=brief_eligible,
        alert_worthy=alert_worthy,
    )


@router.get("/events/{event_id}")
async def get_news_event_package(request: Request, event_id: uuid.UUID):
    """Product-consumer detail: current EventPackage v1 for an event."""
    await _require_news_admin(request, route_operation="news_events_get")
    return await event_package_service.get_event_package(event_id)


@router.put("/events/{event_id}/package")
async def publish_news_event_package(
    request: Request,
    event_id: uuid.UUID,
    body: dict[str, Any],
):
    """Pipeline/admin write: validate and publish next EventPackage version."""
    await _require_news_admin(request, route_operation="news_events_publish_package")
    payload = dict(body)
    payload["event_id"] = str(event_id)
    try:
        EventPackage.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=exc.errors(),
        ) from exc
    return await event_package_service.publish_event_package(payload)
