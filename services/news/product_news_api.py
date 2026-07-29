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
from services.news.event_package import EventPackage, PackageHeroImage, parse_event_package
from services.news.event_package_service import get_event_package
from services.news.hero_selection import ImageCandidate, select_hero_image
from services.news.presentation_translate import (
    fields_from_package,
    normalize_locale,
    translate_presentation_fields,
)
from services.ops.request_context import attach_request_id

PRODUCT_TOP_DEFAULT_LIMIT = 10
PRODUCT_TOP_MIN_LIMIT = 1
PRODUCT_TOP_MAX_LIMIT = 50


class NewsWhyItMattersItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    kind: str = "interpretive"


class NewsHeroImageOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    source_article_id: str | None = None
    origin: str | None = None
    width: int | None = None
    height: int | None = None
    selected_at: datetime | str | None = None
    selection_reason: str | None = None
    selection_score: float | None = None
    hero_confidence: float | None = None


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
    hero_image: NewsHeroImageOut | None = None
    locale: str = "en"
    original_locale_indicator: bool = False
    translation_status: str | None = None
    field_translation_status: dict[str, str] = Field(default_factory=dict)

class NewsTopResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generated_at: datetime | str
    editorial_version: str
    items: list[NewsTopItem] = Field(default_factory=list)
    locale: str = "en"
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
    hero_image: NewsHeroImageOut | None = None
    locale: str = "en"
    original_locale_indicator: bool = False
    fallback_fields: list[str] = Field(default_factory=list)
    translation_status: str | None = None
    field_translation_status: dict[str, str] = Field(default_factory=dict)
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


def _hero_out(hero: PackageHeroImage | dict[str, Any] | None) -> NewsHeroImageOut | None:
    if hero is None:
        return None
    if isinstance(hero, PackageHeroImage):
        data = hero.model_dump(mode="json")
    else:
        data = dict(hero)
    url = _safe_image_url(str(data.get("url") or ""))
    if not url:
        return None
    return NewsHeroImageOut(
        url=url,
        source_article_id=(str(data["source_article_id"]) if data.get("source_article_id") else None),
        origin=(str(data["origin"]) if data.get("origin") else None),
        width=data.get("width"),
        height=data.get("height"),
        selected_at=data.get("selected_at"),
        selection_reason=(str(data["selection_reason"]) if data.get("selection_reason") else None),
        selection_score=data.get("selection_score"),
        hero_confidence=data.get("hero_confidence", data.get("selection_score")),
    )


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


def resolve_hero_for_package(
    pkg: EventPackage,
    *,
    image_map: dict[str, str] | None = None,
) -> NewsHeroImageOut | None:
    """Prefer package.hero_image; legacy packages get deterministic server-side fallback."""
    packaged = _hero_out(pkg.hero_image)
    if packaged is not None:
        return packaged
    images = image_map or {}
    candidates = [
        ImageCandidate(
            url=images.get(str(a.article_id), ""),
            source_article_id=str(a.article_id),
            origin="rss",
            is_primary=(a.role in ("supports", "updates")),
            article_sort_key=str(a.article_id),
        )
        for a in pkg.articles
        if images.get(str(a.article_id))
    ]
    # Prefer first supports article as primary if present
    if pkg.articles:
        primary_id = str(pkg.articles[0].article_id)
        candidates = [
            ImageCandidate(
                url=c.url,
                source_article_id=c.source_article_id,
                origin=c.origin,
                width=c.width,
                height=c.height,
                is_primary=(c.source_article_id == primary_id) or c.is_primary,
                article_sort_key=c.article_sort_key,
            )
            for c in candidates
        ]
    selected = select_hero_image(candidates)
    return _hero_out(selected)


def project_top_item(
    ranked: dict[str, Any],
    *,
    hero: NewsHeroImageOut | None = None,
    headline: str | None = None,
    summary: str | None = None,
    locale: str = "en",
    original_locale_indicator: bool = False,
    translation_status: str | None = None,
    field_translation_status: dict[str, str] | None = None,
) -> NewsTopItem:
    package = ranked.get("package") or {}
    signals = ranked.get("signals") or {}
    pkg = parse_event_package(package) if isinstance(package, dict) else package
    resolved_hero = hero
    return NewsTopItem(
        rank=int(ranked["rank"]),
        event_id=str(ranked["event_id"]),
        headline=_clean_text(headline if headline is not None else pkg.headline),
        summary=_clean_text(summary if summary is not None else pkg.summary),
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
        image_url=resolved_hero.url if resolved_hero else None,
        hero_image=resolved_hero,
        locale=locale,
        original_locale_indicator=original_locale_indicator,
        translation_status=translation_status,
        field_translation_status=dict(field_translation_status or {}),
    )


def project_topic_detail(
    package: EventPackage | dict[str, Any],
    *,
    image_map: dict[str, str] | None = None,
    hero: NewsHeroImageOut | None = None,
    headline: str | None = None,
    summary: str | None = None,
    locale: str = "en",
    original_locale_indicator: bool = False,
    fallback_fields: list[str] | None = None,
    translation_status: str | None = None,
    field_translation_status: dict[str, str] | None = None,
) -> NewsTopicDetail:
    pkg = package if isinstance(package, EventPackage) else parse_event_package(package)
    images = image_map or {}
    resolved_hero = hero or resolve_hero_for_package(pkg, image_map=images)
    return NewsTopicDetail(
        event_id=str(pkg.event_id),
        package_version=int(pkg.package_version),
        schema_version=int(pkg.schema_version),
        headline=_clean_text(headline if headline is not None else pkg.headline),
        summary=_clean_text(summary if summary is not None else pkg.summary),
        why_it_matters=_project_why_it_matters(pkg),
        lifecycle=pkg.lifecycle,
        conflict_open=bool(pkg.consumer_hints.conflict_open),
        happened_at=pkg.happened_at,
        updated_at=pkg.updated_at,
        image_url=resolved_hero.url if resolved_hero else None,
        hero_image=resolved_hero,
        locale=locale,
        original_locale_indicator=original_locale_indicator,
        fallback_fields=list(fallback_fields or []),
        translation_status=translation_status,
        field_translation_status=dict(field_translation_status or {}),
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
        current_facts=[f.model_dump(mode="json") for f in pkg.current_facts],
        conflicts=[c.model_dump(mode="json") for c in pkg.conflicts],
        claims=[],
    )


async def get_top_news(
    *,
    limit: int = PRODUCT_TOP_DEFAULT_LIMIT,
    locale: str = "en",
) -> dict[str, Any]:
    """Product Top News: ranked EventPackage projections only."""
    _validate_top_limit(limit)
    loc = normalize_locale(locale)
    candidate_limit = product_candidate_limit(limit)
    ranked = await rank_top_event_packages(top_n=limit, candidate_limit=candidate_limit)
    editorial = ranked.get("editorial") or {}
    ranked_items = list(ranked.get("items") or [])

    all_ids: list[str] = []
    packages: list[EventPackage | None] = []
    for item in ranked_items:
        package = item.get("package") or {}
        try:
            pkg = parse_event_package(package) if isinstance(package, dict) else package
            packages.append(pkg)
            all_ids.extend(_article_ids_from_package(pkg))
        except Exception:  # noqa: BLE001
            packages.append(None)

    image_map = await load_article_image_map(all_ids)
    items: list[NewsTopItem] = []
    for item, pkg in zip(ranked_items, packages, strict=True):
        if pkg is None:
            continue
        hero = resolve_hero_for_package(pkg, image_map=image_map)
        headline = pkg.headline
        summary = pkg.summary
        indicator = False
        translation_status = None
        field_translation_status: dict[str, str] = {}
        if loc != "en":
            translated = await translate_presentation_fields(
                event_id=str(pkg.event_id),
                package_version=int(pkg.package_version),
                locale=loc,
                fields=fields_from_package(pkg),
            )
            texts = translated.get("texts") or {}
            headline = texts.get("headline", headline)
            summary = texts.get("summary", summary)
            indicator = bool(translated.get("original_locale_indicator"))
            translation_status = translated.get("translation_status")
            field_translation_status = dict(translated.get("field_translation_status") or {})
        items.append(
            project_top_item(
                item,
                hero=hero,
                headline=headline,
                summary=summary,
                locale=loc,
                original_locale_indicator=indicator,
                translation_status=translation_status,
                field_translation_status=field_translation_status,
            )
        )
    generated_at = editorial.get("generated_at") or _utc_now().isoformat()
    response = NewsTopResponse(
        generated_at=generated_at,
        editorial_version=str(editorial.get("version") or EDITORIAL_RANKER_VERSION),
        items=items,
        locale=loc,
    )
    return attach_request_id(response.model_dump(mode="json"))


async def get_topic_detail(event_id, *, locale: str = "en") -> dict[str, Any]:
    """Product topic detail from current EventPackage only."""
    loc = normalize_locale(locale)
    envelope = await get_event_package(event_id)
    package = envelope.get("package")
    if not package:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Event package not found")
    pkg = parse_event_package(package)
    image_map = await load_article_image_map(_article_ids_from_package(pkg))
    hero = resolve_hero_for_package(pkg, image_map=image_map)
    headline = pkg.headline
    summary = pkg.summary
    indicator = False
    fallback_fields: list[str] = []
    translation_status = None
    field_translation_status: dict[str, str] = {}
    if loc != "en":
        translated = await translate_presentation_fields(
            event_id=str(pkg.event_id),
            package_version=int(pkg.package_version),
            locale=loc,
            fields=fields_from_package(pkg),
        )
        texts = translated.get("texts") or {}
        headline = texts.get("headline", headline)
        summary = texts.get("summary", summary)
        indicator = bool(translated.get("original_locale_indicator"))
        fallback_fields = list(translated.get("fallback_fields") or [])
        translation_status = translated.get("translation_status")
        field_translation_status = dict(translated.get("field_translation_status") or {})
    topic = project_topic_detail(
        pkg,
        image_map=image_map,
        hero=hero,
        headline=headline,
        summary=summary,
        locale=loc,
        original_locale_indicator=indicator,
        fallback_fields=fallback_fields,
        translation_status=translation_status,
        field_translation_status=field_translation_status,
    )
    response = NewsTopicResponse(topic=topic)
    return attach_request_id(response.model_dump(mode="json"))
