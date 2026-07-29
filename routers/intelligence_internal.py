"""Internal Intelligence API — EventUnderstanding inspection (Phase 1b)."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, Request

from auth.beta_gate import build_project_tenant_context_from_request
from auth.news_registry_privileges import assert_can_manage_news_sources
from services.intelligence import persistence as understanding_persistence
from services.intelligence.taxonomy import CLASSIFIER_VERSION, TEMPLATE_VERSION

router = APIRouter(prefix="/api/internal/intelligence", tags=["intelligence"])


async def _require_intelligence_admin(request: Request, *, route_operation: str):
    ctx = await build_project_tenant_context_from_request(
        request, route_operation=route_operation
    )
    assert_can_manage_news_sources(ctx)
    return ctx


@router.get("/events/{event_id}/understanding")
async def get_event_understanding(
    request: Request,
    event_id: uuid.UUID,
    package_version: int | None = Query(None, ge=1),
    classifier_version: str = Query(CLASSIFIER_VERSION, max_length=64),
    template_version: str = Query(TEMPLATE_VERSION, max_length=64),
):
    """Operator read of a persisted EventUnderstanding (not a product News path)."""
    await _require_intelligence_admin(
        request, route_operation="GET /api/internal/intelligence/events/{event_id}/understanding"
    )
    return await understanding_persistence.get_event_understanding(
        event_id,
        package_version=package_version,
        classifier_version=classifier_version,
        template_version=template_version,
    )


@router.post("/events/{event_id}/understanding/materialize")
async def materialize_event_understanding_route(
    request: Request,
    event_id: uuid.UUID,
    package_version: int | None = Query(None, ge=1),
):
    """Materialize from current EventPackage and upsert Understanding (idempotent)."""
    await _require_intelligence_admin(
        request,
        route_operation="POST /api/internal/intelligence/events/{event_id}/understanding/materialize",
    )
    return await understanding_persistence.materialize_from_stored_package(
        event_id,
        package_version=package_version,
    )
