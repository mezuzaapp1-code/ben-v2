"""Pass C — Product-facing News API projections (package-first, read-only).

Does not mutate EventPackages or change editorial ranking logic.
"""
from __future__ import annotations

import html
import uuid
from datetime import datetime, timezone
from typing import Any, Sequence

from fastapi import HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from database.connection import get_db_session
from database.models import NewsArticle
from services.news.editorial_ranker import (
    EDITORIAL_DEFAULT_CANDIDATE_LIMIT,
    EDITORIAL_RANKER_VERSION,
    MAX_CANDIDATE_LIMIT,
    rank_top_event_packages,
)
from services.news.event_package import EventPackage, parse_event_package
from services.news.event_package_service import get_event_package
from services.ops.request_context import attach_request_id

PRODUCT_TOP_DEFAULT_LIMIT = 10
PRODUCT_TOP_MIN_LIMIT = 1
PRODUCT_TOP_MAX_LIMIT = 50


class NewsWhyItMattersItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    kind: str = "interpretive"


class NewsTopItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rank: int
    event_id: str
    headline: str
    summary: str
    why_it_matters: list[NewsWhyItMattersItem] = Field(default_factory=list)
    source_count: int
    article_count: int
    updated_at: datetime | str | None = None
    happened_at: datetime | str | None = None
    lifecycle: str
    conflict_open: bool
    reasons: list[str] = Field(default_factory=list)
    image_url: str | None = None


class NewsTopResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generated_at: datetime | str
    editorial_version: str
    items: list[NewsTopItem] = Field(default_factory=list)
    request_id: str | None = None


class NewsTopicSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    name: str
    tier: str
    article_ids: list[str] = Field(default_factory=list)


class NewsTopicArticle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    article_id: str
    title: str
    url: str
    published_at: datetime | str | None = None
    source_id: str
    role: str
    image_url: str | None = None


class NewsTopicDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str
    package_version: int
    schema_version: int
    headline: str
    summary: str
    why_it_matters: list[NewsWhyItMattersItem] = Field(default_factory=list)
    lifecycle: str
    conflict_open: bool
    happened_at: datetime | str | None = None
    updated_at: datetime | str | None = None
    image_url: str | None = None
    sources: list[NewsTopicSource] = Field(default_factory=list)
    articles: list[NewsTopicArticle] = Field(default_factory=list)
    current_facts: list[dict[str, Any]] = Field(default_factory=list)
    conflicts: list[dict[str, Any]] = Field(default_factory=list)
    claims: list[Any] = Field(default_factory=list)


class NewsTopicResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic: NewsTopicDetail
    request_id: str | None = None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _clean_text(value: str | None) -> str:
    return html.unescape((value or "").strip())


def _safe_image_url(raw: str | None) -> str | None:
    text = (raw or "").strip()
    if not text or len(text) > 2048:
        return None
    if not text.startswith("https://"):
        return None
    return text


def product_candidate_limit(limit: int) -> int:
    """Server-owned candidate window; never accepted from public clients."""
    raw = max(EDITORIAL_DEFAULT_CANDIDATE_LIMIT, int(limit) * 20)
    return min(raw, MAX_CANDIDATE_LIMIT)


def _validate_top_limit(limit: int) -> None:
    if limit < PRODUCT_TOP_MIN_LIMIT or limit > PRODUCT_TOP_MAX_LIMIT:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"limit must be between {PRODUCT_TOP_MIN_LIMIT} "
                f"and {PRODUCT_TOP_MAX_LIMIT}"
            ),
        )


def _project_why_it_matters(package: EventPackage | dict[str, Any]) -> list[NewsWhyItMattersItem]:
    if isinstance(package, EventPackage):
        items = package.why_it_matters
        return [NewsWhyItMattersItem(text=_clean_text(i.text), kind=i.kind) for i in items]
    raw = package.get("why_it_matters") or []
    out: list[NewsWhyItMattersItem] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        text = _clean_text(str(item.get("text") or ""))
        if not text:
            continue
        out.append(
            NewsWhyItMattersItem(
                text=text,
                kind=str(item.get("kind") or "interpretive"),
            )
        )
    return out


def _article_ids_from_package(pkg: EventPackage) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    # Prefer primary-ish roles first for hero image selection order.
    ordered = sorted(
        pkg.articles,
        key=lambda a: 0 if a.role in ("supports", "updates") else 1,
    )
    for article in ordered:
        aid = str(article.article_id).strip()
        if not aid or aid in seen:
            continue
        seen.add(aid)
        ids.append(aid)
    return ids


async def load_article_image_map(article_ids: Sequence[str]) -> dict[str, str]:
    """Batch-load https image URLs for article ids (missing/invalid omitted)."""
    uuids: list[uuid.UUID] = []
    for raw in article_ids:
        try:
            uuids.append(uuid.UUID(str(raw)))
        except ValueError:
            continue
    if not uuids:
        return {}
    async with get_db_session() as session:
        rows = (
            await session.execute(
                select(NewsArticle.id, NewsArticle.image_url).where(NewsArticle.id.in_(uuids))
            )
        ).all()
    out: dict[str, str] = {}
    for row_id, image_url in rows:
        safe = _safe_image_url(image_url)
        if safe:
            out[str(row_id)] = safe
    return out


def pick_hero_image(article_ids: Sequence[str], image_map: dict[str, str]) -> str | None:
    for aid in article_ids:
        url = image_map.get(str(aid))
        if url:
            return url
    return None


def project_top_item(
    ranked: dict[str, Any],
    *,
    image_url: str | None = None,
) -> NewsTopItem:
    package = ranked.get("package") or {}
    signals = ranked.get("signals") or {}
    pkg = parse_event_package(package) if isinstance(package, dict) else package
    return NewsTopItem(
        rank=int(ranked["rank"]),
        event_id=str(ranked["event_id"]),
        headline=_clean_text(pkg.headline),
        summary=_clean_text(pkg.summary),
        why_it_matters=_project_why_it_matters(pkg),
        source_count=int(signals.get("source_count", len(pkg.sources))),
        article_count=int(signals.get("article_count", len(pkg.articles))),
        updated_at=pkg.updated_at,
        happened_at=pkg.happened_at,
        lifecycle=pkg.lifecycle,
        conflict_open=bool(
            signals.get("conflict_open", pkg.consumer_hints.conflict_open)
        ),
        reasons=list(ranked.get("reasons") or []),
        image_url=_safe_image_url(image_url),
    )


def project_topic_detail(
    package: EventPackage | dict[str, Any],
    *,
    image_map: dict[str, str] | None = None,
) -> NewsTopicDetail:
    pkg = package if isinstance(package, EventPackage) else parse_event_package(package)
    images = image_map or {}
    article_ids = _article_ids_from_package(pkg)
    return NewsTopicDetail(
        event_id=str(pkg.event_id),
        package_version=int(pkg.package_version),
        schema_version=int(pkg.schema_version),
        headline=_clean_text(pkg.headline),
        summary=_clean_text(pkg.summary),
        why_it_matters=_project_why_it_matters(pkg),
        lifecycle=pkg.lifecycle,
        conflict_open=bool(pkg.consumer_hints.conflict_open),
        happened_at=pkg.happened_at,
        updated_at=pkg.updated_at,
        image_url=pick_hero_image(article_ids, images),
        sources=[
            NewsTopicSource(
                source_id=s.source_id,
                name=s.name,
                tier=s.tier,
                article_ids=list(s.article_ids),
            )
            for s in pkg.sources
        ],
        articles=[
            NewsTopicArticle(
                article_id=a.article_id,
                title=_clean_text(a.title),
                url=a.url,
                published_at=a.published_at,
                source_id=a.source_id,
                role=a.role,
                image_url=images.get(str(a.article_id)),
            )
            for a in pkg.articles
        ],
        # Product-safe JSON dumps of package facts/conflicts (already contract fields).
        current_facts=[f.model_dump(mode="json") for f in pkg.current_facts],
        conflicts=[c.model_dump(mode="json") for c in pkg.conflicts],
        # Claims are not part of EventPackage; leave empty until a future pass.
        claims=[],
    )


async def get_top_news(*, limit: int = PRODUCT_TOP_DEFAULT_LIMIT) -> dict[str, Any]:
    """Product Top News: ranked EventPackage projections only.

    Product contract: never 500 the Top feed for transient ranking/DB failures.
    Degrade to an empty, valid feed so the UI can show a proper empty state.
    """
    _validate_top_limit(limit)
    candidate_limit = product_candidate_limit(limit)
    try:
        ranked = await rank_top_event_packages(top_n=limit, candidate_limit=candidate_limit)
    except Exception:  # noqa: BLE001
        import logging

        logging.getLogger(__name__).exception(
            "get_top_news ranking failed; returning empty product feed"
        )
        response = NewsTopResponse(
            generated_at=_utc_now().isoformat(),
            editorial_version=EDITORIAL_RANKER_VERSION,
            items=[],
        )
        return attach_request_id(response.model_dump(mode="json"))
    editorial = ranked.get("editorial") or {}
    ranked_items = list(ranked.get("items") or [])

    all_ids: list[str] = []
    per_item_ids: list[list[str]] = []
    for item in ranked_items:
        package = item.get("package") or {}
        try:
            pkg = parse_event_package(package) if isinstance(package, dict) else package
            ids = _article_ids_from_package(pkg)
        except Exception:  # noqa: BLE001
            ids = []
        per_item_ids.append(ids)
        all_ids.extend(ids)

    image_map = await load_article_image_map(all_ids)
    items = [
        project_top_item(item, image_url=pick_hero_image(ids, image_map))
        for item, ids in zip(ranked_items, per_item_ids, strict=True)
    ]
    generated_at = editorial.get("generated_at") or _utc_now().isoformat()
    response = NewsTopResponse(
        generated_at=generated_at,
        editorial_version=str(editorial.get("version") or EDITORIAL_RANKER_VERSION),
        items=items,
    )
    return attach_request_id(response.model_dump(mode="json"))


async def get_topic_detail(event_id) -> dict[str, Any]:
    """Product topic detail from current EventPackage only."""
    envelope = await get_event_package(event_id)
    package = envelope.get("package")
    if not package:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Event package not found")
    pkg = parse_event_package(package)
    image_map = await load_article_image_map(_article_ids_from_package(pkg))
    topic = project_topic_detail(pkg, image_map=image_map)
    response = NewsTopicResponse(topic=topic)
    return attach_request_id(response.model_dump(mode="json"))
