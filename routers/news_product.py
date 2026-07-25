"""Product News API — Top feed and topic detail (Pass C).

Package-first read path for signed-in product users. Not an admin surface.
Internal operator routes remain under /api/internal/news/*.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, Request

from auth.beta_gate import build_project_tenant_context_from_request
from services.news import product_news_api

router = APIRouter(prefix="/api/news", tags=["news-product"])


async def _require_signed_in_product_user(request: Request, *, route_operation: str):
    """Reuse the standard product auth gate — signed-in Clerk/beta, not news-admin."""
    return await build_project_tenant_context_from_request(
        request, route_operation=route_operation
    )


@router.get("/top")
async def get_news_top(
    request: Request,
    limit: int = Query(
        product_news_api.PRODUCT_TOP_DEFAULT_LIMIT,
        ge=product_news_api.PRODUCT_TOP_MIN_LIMIT,
        le=product_news_api.PRODUCT_TOP_MAX_LIMIT,
    ),
):
    """Product Top News list projected from the Editorial Engine."""
    await _require_signed_in_product_user(
        request, route_operation="GET /api/news/top"
    )
    return await product_news_api.get_top_news(limit=limit)


@router.get("/topics/{event_id}")
async def get_news_topic(
    request: Request,
    event_id: uuid.UUID,
):
    """Product topic detail projected from the current EventPackage."""
    await _require_signed_in_product_user(
        request, route_operation="GET /api/news/topics/{event_id}"
    )
    return await product_news_api.get_topic_detail(event_id)
