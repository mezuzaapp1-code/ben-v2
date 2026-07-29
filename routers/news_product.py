"""Product News API — Top feed and topic detail (Pass C).

Package-first read path for product users. Auth matches chat shadow policy:
when ENFORCE_AUTH is false, unsigned/anonymous readers may load News.
Internal operator routes remain under /api/internal/news/*.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, Request

from auth.beta_gate import maybe_beta_auditor_context
from auth.shadow_auth import apply_auth_policy
from auth.tenant_binding import build_tenant_context, log_tenant_bound
from services.news import product_news_api

router = APIRouter(prefix="/api/news", tags=["news-product"])


async def _require_product_news_reader(request: Request, *, route_operation: str):
    """Same gate as chat: enforce only when ENFORCE_AUTH=true; beta auditor wins when present."""
    outcome, claims, auth_present = await apply_auth_policy(
        request, route_operation=route_operation
    )
    beta_ctx = maybe_beta_auditor_context(request)
    if beta_ctx:
        log_tenant_bound(route_operation=route_operation, ctx=beta_ctx)
        return beta_ctx
    ctx = build_tenant_context(outcome, claims, auth_present)
    log_tenant_bound(route_operation=route_operation, ctx=ctx)
    return ctx


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
    await _require_product_news_reader(
        request, route_operation="GET /api/news/top"
    )
    return await product_news_api.get_top_news(limit=limit)


@router.get("/topics/{event_id}")
async def get_news_topic(
    request: Request,
    event_id: uuid.UUID,
):
    """Product topic detail projected from the current EventPackage."""
    await _require_product_news_reader(
        request, route_operation="GET /api/news/topics/{event_id}"
    )
    return await product_news_api.get_topic_detail(event_id)
