"""Pass A — Heuristic EventPackage builder (no LLM / embeddings / entities).

NewsArticle → conservative deterministic groups → NewsEvent + EventPackage v1.
Membership lives only in package snapshots; no membership table.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import unicodedata
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Sequence
from urllib.parse import urlparse, urlunparse

from fastapi import HTTPException, status
from pydantic import ValidationError
from sqlalchemy import func, or_, select

from database.connection import get_db_session
from database.models import NewsArticle, NewsEvent, NewsEventPackage, NewsSource
from services.news.event_package import (
    EVENT_PACKAGE_SCHEMA_VERSION,
    ConsumerHints,
    EventPackage,
    PackageArticleCard,
    PackageProvenance,
    PackageSource,
    event_package_to_dict,
    parse_event_package,
)
from services.news.event_package_service import publish_event_package
from services.ops.request_context import attach_request_id

BUILDER_VERSION = "heuristic_event_builder.v1"

# Fixed BEN-owned namespace for UUID5 event identity (do not change).
NAMESPACE_BEN_NEWS_EVENTS = uuid.UUID("b3e00001-4e77-5a11-9e77-00000000a001")

DEFAULT_LOOKBACK_HOURS = 72
DEFAULT_MAX_ARTICLES = 500
MIN_LOOKBACK_HOURS = 1
MAX_LOOKBACK_HOURS = 24 * 30
MIN_MAX_ARTICLES = 1
MAX_MAX_ARTICLES = 5000

JACCARD_THRESHOLD = 0.55
NEAR_DUP_THRESHOLD = 0.85

DEFAULT_ELIGIBLE_CATEGORIES = frozenset({"ai", "technology", "tech"})

_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "from",
        "that",
        "this",
        "into",
        "over",
        "under",
        "after",
        "before",
        "about",
        "amid",
        "amidst",
        "against",
        "between",
        "among",
        "via",
        "are",
        "was",
        "were",
        "been",
        "being",
        "have",
        "has",
        "had",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "can",
        "not",
        "but",
        "its",
        "their",
        "his",
        "her",
        "our",
        "your",
        "new",
        "says",
        "said",
        "how",
        "why",
        "what",
        "when",
        "where",
        "who",
        "whom",
        "which",
        "while",
        "than",
        "then",
        "also",
        "just",
        "more",
        "most",
        "some",
        "any",
        "all",
        "out",
        "off",
        "per",
    }
)

_PUNCT_RE = re.compile(r"[^\w\s]+", re.UNICODE)
_WS_RE = re.compile(r"\s+")

_build_guard = asyncio.Lock()
_build_active = False


@dataclass(frozen=True)
class ArticleRecord:
    """Normalized in-memory article used by the grouping pipeline."""

    id: uuid.UUID
    source_id: uuid.UUID
    source_name: str
    title: str
    url: str
    summary: str | None
    published_at: datetime | None
    created_at: datetime
    category: str
    tokens: tuple[str, ...]
    canonical_url: str
    event_time: datetime  # published_at or created_at (UTC)


@dataclass
class ArticleGroup:
    members: list[ArticleRecord]
    medoid: ArticleRecord
    topic_signature: str
    event_id: uuid.UUID


@dataclass
class BuildResult:
    articles_considered: int = 0
    eligible_articles: int = 0
    groups_formed: int = 0
    single_article_groups: int = 0
    events_created: int = 0
    events_updated: int = 0
    unchanged_events_skipped: int = 0
    packages_published: int = 0
    skipped_articles: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    dry_run: bool = False
    concurrency_conflict: bool = False
    groups: list[dict[str, Any]] = field(default_factory=list)
    packages: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "builder_version": BUILDER_VERSION,
            "articles_considered": self.articles_considered,
            "eligible_articles": self.eligible_articles,
            "groups_formed": self.groups_formed,
            "single_article_groups": self.single_article_groups,
            "events_created": self.events_created,
            "events_updated": self.events_updated,
            "unchanged_events_skipped": self.unchanged_events_skipped,
            "packages_published": self.packages_published,
            "skipped_articles": self.skipped_articles,
            "errors": self.errors,
            "dry_run": self.dry_run,
            "concurrency_conflict": self.concurrency_conflict,
            "groups": self.groups,
        }
        if self.dry_run or self.packages:
            payload["packages"] = self.packages
        return attach_request_id(payload)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def article_event_time(*, published_at: datetime | None, created_at: datetime) -> datetime:
    pub = ensure_utc(published_at)
    if pub is not None:
        return pub
    created = ensure_utc(created_at)
    if created is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    return created


def normalize_title_tokens(title: str) -> list[str]:
    """Deterministic title tokenization. Original title is never mutated by callers."""
    if not title:
        return []
    text = unicodedata.normalize("NFKC", title).lower()
    text = _PUNCT_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    tokens: list[str] = []
    for tok in text.split(" "):
        if len(tok) < 3:
            continue
        if tok in _STOPWORDS:
            continue
        tokens.append(tok)
    return tokens


def jaccard(a: Sequence[str], b: Sequence[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def canonicalize_article_url(url: str) -> str:
    """Deterministic URL key for hard-merge (no network)."""
    raw = (url or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    scheme = (parsed.scheme or "https").lower()
    host = (parsed.hostname or "").strip().lower()
    if not host:
        return raw.lower()
    netloc = host
    if parsed.port is not None:
        default = 80 if scheme == "http" else 443
        if parsed.port != default:
            netloc = f"{host}:{parsed.port}"
    path = parsed.path or ""
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    # Drop fragment; keep query (syndication often differs by tracking — still useful for exact matches).
    return urlunparse((scheme, netloc, path, "", parsed.query, ""))


def topic_signature_for(medoid: ArticleRecord) -> str:
    if medoid.tokens:
        return " ".join(sorted(medoid.tokens))
    if medoid.canonical_url:
        return f"url:{medoid.canonical_url}"
    return f"article:{medoid.id}"


def event_id_for(*, builder_version: str, topic_signature: str) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE_BEN_NEWS_EVENTS, f"{builder_version}|{topic_signature}")


def select_medoid(members: Sequence[ArticleRecord]) -> ArticleRecord:
    """Earliest published_at (nulls last), then source_id, then article_id."""

    def key(a: ArticleRecord) -> tuple:
        pub = ensure_utc(a.published_at)
        null_pub = pub is None
        pub_ord = pub or datetime.max.replace(tzinfo=timezone.utc)
        return (null_pub, pub_ord, str(a.source_id), str(a.id))

    return sorted(members, key=key)[0]


def _day_key(dt: datetime) -> str:
    return ensure_utc(dt).date().isoformat()  # type: ignore[union-attr]


def _adjacent_day_keys(dt: datetime) -> list[str]:
    d = ensure_utc(dt)
    assert d is not None
    return [
        (d.date() - timedelta(days=1)).isoformat(),
        d.date().isoformat(),
        (d.date() + timedelta(days=1)).isoformat(),
    ]


def _block_keys(article: ArticleRecord) -> set[str]:
    keys: set[str] = set()
    for day in _adjacent_day_keys(article.event_time):
        keys.add(f"day:{day}")
        if article.tokens:
            keys.add(f"daytok:{day}:{article.tokens[0]}")
            if len(article.tokens) >= 2:
                bigram = f"{article.tokens[0]}_{article.tokens[1]}"
                keys.add(f"daybi:{day}:{bigram}")
        if article.canonical_url:
            keys.add(f"url:{article.canonical_url}")
    return keys


def _within_window(a: ArticleRecord, b: ArticleRecord, *, lookback: timedelta) -> bool:
    """Pair must be within lookback of each other (not only of 'now')."""
    delta = abs(a.event_time - b.event_time)
    return delta <= lookback


def _should_merge(
    a: ArticleRecord,
    b: ArticleRecord,
    *,
    lookback: timedelta,
    jaccard_threshold: float = JACCARD_THRESHOLD,
    near_dup_threshold: float = NEAR_DUP_THRESHOLD,
) -> bool:
    if a.id == b.id:
        return True
    if not _within_window(a, b, lookback=lookback):
        return False
    if a.canonical_url and a.canonical_url == b.canonical_url:
        return True
    score = jaccard(a.tokens, b.tokens)
    return score >= jaccard_threshold or score >= near_dup_threshold


class _UnionFind:
    def __init__(self, ids: Iterable[uuid.UUID]):
        self.parent = {i: i for i in ids}

    def find(self, x: uuid.UUID) -> uuid.UUID:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: uuid.UUID, b: uuid.UUID) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        # Deterministic parent: smaller UUID string
        if str(ra) < str(rb):
            self.parent[rb] = ra
        else:
            self.parent[ra] = rb


def _pair_candidates(articles: Sequence[ArticleRecord]) -> list[tuple[ArticleRecord, ArticleRecord]]:
    """Blocking: only compare articles that share a block key. Order-independent."""
    by_id = {a.id: a for a in articles}
    buckets: dict[str, list[uuid.UUID]] = defaultdict(list)
    for a in articles:
        for key in _block_keys(a):
            buckets[key].append(a.id)

    seen_pairs: set[tuple[uuid.UUID, uuid.UUID]] = set()
    pairs: list[tuple[ArticleRecord, ArticleRecord]] = []
    for ids in buckets.values():
        uniq = sorted(set(ids), key=str)
        for i in range(len(uniq)):
            for j in range(i + 1, len(uniq)):
                a_id, b_id = uniq[i], uniq[j]
                edge = (a_id, b_id) if str(a_id) < str(b_id) else (b_id, a_id)
                if edge in seen_pairs:
                    continue
                seen_pairs.add(edge)
                pairs.append((by_id[edge[0]], by_id[edge[1]]))
    # Stable pair order
    pairs.sort(key=lambda p: (str(p[0].id), str(p[1].id)))
    return pairs


def _medoid_filter(
    members: Sequence[ArticleRecord],
    *,
    lookback: timedelta,
    jaccard_threshold: float,
) -> list[list[ArticleRecord]]:
    """Split a connected component using medoid similarity (anti chain-merge)."""
    remaining = sorted(members, key=lambda a: str(a.id))
    clusters: list[list[ArticleRecord]] = []
    while remaining:
        medoid = select_medoid(remaining)
        kept: list[ArticleRecord] = []
        rejected: list[ArticleRecord] = []
        for a in remaining:
            if a.id == medoid.id or _should_merge(
                a,
                medoid,
                lookback=lookback,
                jaccard_threshold=jaccard_threshold,
            ):
                kept.append(a)
            else:
                rejected.append(a)
        clusters.append(sorted(kept, key=lambda a: str(a.id)))
        remaining = rejected
    return clusters


def group_articles(
    articles: Sequence[ArticleRecord],
    *,
    lookback_hours: int = DEFAULT_LOOKBACK_HOURS,
    builder_version: str = BUILDER_VERSION,
    jaccard_threshold: float = JACCARD_THRESHOLD,
) -> list[ArticleGroup]:
    """Deterministic grouping. Input order must not affect output."""
    if not articles:
        return []
    # Canonical order
    ordered = sorted(articles, key=lambda a: str(a.id))
    lookback = timedelta(hours=lookback_hours)
    uf = _UnionFind(a.id for a in ordered)
    by_id = {a.id: a for a in ordered}

    for a, b in _pair_candidates(ordered):
        if _should_merge(a, b, lookback=lookback, jaccard_threshold=jaccard_threshold):
            uf.union(a.id, b.id)

    components: dict[uuid.UUID, list[ArticleRecord]] = defaultdict(list)
    for a in ordered:
        components[uf.find(a.id)].append(a)

    groups: list[ArticleGroup] = []
    for root in sorted(components.keys(), key=str):
        for cluster in _medoid_filter(
            components[root],
            lookback=lookback,
            jaccard_threshold=jaccard_threshold,
        ):
            medoid = select_medoid(cluster)
            sig = topic_signature_for(medoid)
            eid = event_id_for(builder_version=builder_version, topic_signature=sig)
            groups.append(
                ArticleGroup(
                    members=sorted(cluster, key=lambda a: str(a.id)),
                    medoid=medoid,
                    topic_signature=sig,
                    event_id=eid,
                )
            )

    # Stable group order by event_id then medoid id
    groups.sort(key=lambda g: (str(g.event_id), str(g.medoid.id)))
    return groups


def choose_summary(members: Sequence[ArticleRecord], *, headline: str) -> str:
    """Longest non-empty summary; ties broken by medoid order (pub, source, id)."""
    candidates = [m for m in members if (m.summary or "").strip()]
    if not candidates:
        return headline[:4000]
    ordered = sorted(
        candidates,
        key=lambda m: (
            -len(m.summary.strip()),  # type: ignore[union-attr]
            ensure_utc(m.published_at) is None,
            ensure_utc(m.published_at) or datetime.max.replace(tzinfo=timezone.utc),
            str(m.source_id),
            str(m.id),
        ),
    )
    return ordered[0].summary.strip()[:4000]  # type: ignore[union-attr]


def content_fingerprint_from_material(
    *,
    event_id: str,
    lifecycle: str,
    headline: str,
    summary: str,
    happened_at: str | None,
    updated_at: str,
    article_ids: Sequence[str],
    source_ids: Sequence[str],
    article_count: int,
    source_count: int,
    builder_version: str,
) -> str:
    material = {
        "event_id": event_id,
        "lifecycle": lifecycle,
        "headline": headline,
        "summary": summary,
        "happened_at": happened_at,
        "updated_at": updated_at,
        "article_ids": sorted(article_ids),
        "source_ids": sorted(source_ids),
        "article_count": article_count,
        "source_count": source_count,
        "builder_version": builder_version,
        "conflict_open": False,
        "brief_eligible": False,
    }
    blob = json.dumps(material, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def fingerprint_from_package(package: EventPackage | dict[str, Any]) -> str:
    pkg = parse_event_package(package)
    article_ids = [a.article_id for a in pkg.articles]
    source_ids = [s.source_id for s in pkg.sources]
    happened = pkg.happened_at.isoformat() if isinstance(pkg.happened_at, datetime) else pkg.happened_at
    updated = pkg.updated_at.isoformat() if isinstance(pkg.updated_at, datetime) else str(pkg.updated_at)
    signals = pkg.consumer_hints.feed_rank_signals or {}
    return content_fingerprint_from_material(
        event_id=pkg.event_id,
        lifecycle=pkg.lifecycle,
        headline=pkg.headline,
        summary=pkg.summary,
        happened_at=happened,
        updated_at=updated,
        article_ids=article_ids,
        source_ids=source_ids,
        article_count=int(signals.get("article_count", len(article_ids))),
        source_count=int(signals.get("source_count", len(source_ids))),
        builder_version=str(signals.get("builder_version", BUILDER_VERSION)),
    )


def build_event_package_dict(
    group: ArticleGroup,
    *,
    lookback_hours: int,
    generated_at: datetime | None = None,
    package_version: int = 1,
) -> dict[str, Any]:
    medoid = group.medoid
    members = group.members
    headline = (medoid.title or "").strip()[:1024]
    if not headline:
        raise ValueError("medoid title empty")
    summary = choose_summary(members, headline=headline)
    times = [m.event_time for m in members]
    happened_at = min(times)
    updated_at = max(times)
    gen_at = ensure_utc(generated_at) or _utc_now()

    articles = [
        PackageArticleCard(
            article_id=str(m.id),
            source_id=str(m.source_id),
            title=m.title.strip()[:1024],
            url=m.url.strip()[:2048],
            published_at=ensure_utc(m.published_at),
            role="supports",
        )
        for m in sorted(members, key=lambda a: str(a.id))
    ]

    by_source: dict[str, PackageSource] = {}
    for m in sorted(members, key=lambda a: (str(a.source_id), str(a.id))):
        sid = str(m.source_id)
        if sid not in by_source:
            by_source[sid] = PackageSource(
                source_id=sid,
                name=(m.source_name or sid)[:256] or sid,
                tier="C",
                article_ids=[],
            )
        by_source[sid].article_ids.append(str(m.id))
    sources = list(by_source.values())

    article_ids = [a.article_id for a in articles]
    source_ids = [s.source_id for s in sources]
    fp = content_fingerprint_from_material(
        event_id=str(group.event_id),
        lifecycle="developing",
        headline=headline,
        summary=summary,
        happened_at=happened_at.isoformat(),
        updated_at=updated_at.isoformat(),
        article_ids=article_ids,
        source_ids=source_ids,
        article_count=len(article_ids),
        source_count=len(source_ids),
        builder_version=BUILDER_VERSION,
    )

    policy_notes = [
        f"builder_version={BUILDER_VERSION}",
        f"topic_signature={group.topic_signature}",
        f"jaccard_threshold={JACCARD_THRESHOLD}",
        f"near_dup_threshold={NEAR_DUP_THRESHOLD}",
        f"lookback_hours={lookback_hours}",
        f"medoid_article_id={medoid.id}",
        f"membership_article_ids={','.join(sorted(article_ids))}",
        f"content_fingerprint={fp}",
        "grouping=title_jaccard_url_time_medoid_filter",
    ]

    package = EventPackage(
        schema_version=EVENT_PACKAGE_SCHEMA_VERSION,
        event_id=str(group.event_id),
        package_version=package_version,
        lifecycle="developing",
        headline=headline,
        happened_at=happened_at,
        updated_at=updated_at,
        summary=summary,
        current_facts=[],
        impacts=[],
        why_it_matters=[],
        conflicts=[],
        entities=[],
        sources=sources,
        articles=articles,
        consumer_hints=ConsumerHints(
            alert_worthy=False,
            brief_eligible=False,
            conflict_open=False,
            feed_rank_signals={
                "article_count": len(article_ids),
                "source_count": len(source_ids),
                "builder_version": BUILDER_VERSION,
            },
        ),
        provenance=PackageProvenance(
            generated_at=gen_at,
            schema_version=EVENT_PACKAGE_SCHEMA_VERSION,
            policy_notes=policy_notes,
        ),
    )
    return event_package_to_dict(package)


def article_to_record(article: NewsArticle, source: NewsSource) -> ArticleRecord | None:
    title = (article.title or "").strip()
    if not title:
        return None
    url = (article.url or "").strip()
    if not url:
        return None
    created = ensure_utc(article.created_at) or _utc_now()
    published = ensure_utc(article.published_at)
    tokens = tuple(normalize_title_tokens(title))
    return ArticleRecord(
        id=article.id,
        source_id=article.source_id,
        source_name=(source.name or "").strip() or str(source.id),
        title=title,
        url=url,
        summary=article.summary,
        published_at=published,
        created_at=created,
        category=(article.category or "").strip(),
        tokens=tokens,
        canonical_url=canonicalize_article_url(url),
        event_time=article_event_time(published_at=published, created_at=created),
    )


def _eligible_category(category: str, eligible: frozenset[str]) -> bool:
    return (category or "").strip().lower() in eligible


async def _load_candidate_articles(
    *,
    lookback_hours: int,
    max_articles: int,
    eligible_categories: frozenset[str],
    now: datetime,
) -> tuple[list[ArticleRecord], list[dict[str, Any]], int]:
    """Load recent articles from enabled sources within the UTC lookback window."""
    cutoff = now - timedelta(hours=lookback_hours)
    skipped: list[dict[str, Any]] = []
    records: list[ArticleRecord] = []
    cats = sorted(eligible_categories)

    # event_time ≈ coalesce(published_at, created_at); compare in UTC-safe timestamptz columns.
    event_time_expr = func.coalesce(NewsArticle.published_at, NewsArticle.created_at)

    async with get_db_session() as session:
        q = (
            select(NewsArticle, NewsSource)
            .join(NewsSource, NewsArticle.source_id == NewsSource.id)
            .where(NewsSource.enabled.is_(True))
            .where(event_time_expr >= cutoff)
            .where(
                or_(
                    func.lower(NewsArticle.category).in_(cats),
                    func.lower(NewsSource.category).in_(cats),
                )
            )
            .where(func.length(func.trim(NewsArticle.title)) > 0)
            .order_by(event_time_expr.desc(), NewsArticle.id.desc())
            .limit(max_articles)
        )
        rows = (await session.execute(q)).all()

    considered = len(rows)
    for article, source in rows:
        try:
            cat = (article.category or source.category or "").strip().lower()
            if not _eligible_category(cat, eligible_categories):
                skipped.append(
                    {
                        "article_id": str(article.id),
                        "reason": "category_ineligible",
                        "category": cat,
                    }
                )
                continue
            rec = article_to_record(article, source)
            if rec is None:
                skipped.append(
                    {
                        "article_id": str(getattr(article, "id", None)),
                        "reason": "malformed_metadata",
                    }
                )
                continue
            if rec.event_time < cutoff:
                skipped.append(
                    {
                        "article_id": str(article.id),
                        "reason": "outside_lookback",
                    }
                )
                continue
            records.append(rec)
        except Exception as exc:  # noqa: BLE001
            skipped.append(
                {
                    "article_id": str(getattr(article, "id", None)),
                    "reason": "malformed_metadata",
                    "error": exc.__class__.__name__,
                }
            )

    records.sort(key=lambda a: str(a.id))
    return records, skipped, considered


async def _current_package_payload(event_id: uuid.UUID) -> dict[str, Any] | None:
    async with get_db_session() as session:
        event = await session.get(NewsEvent, event_id)
        if event is None or int(event.current_package_version) < 1:
            return None
        q = select(NewsEventPackage).where(
            NewsEventPackage.event_id == event_id,
            NewsEventPackage.package_version == event.current_package_version,
        )
        row = (await session.execute(q)).scalar_one_or_none()
        if row is None:
            return None
        return dict(row.payload)


async def _event_exists(event_id: uuid.UUID) -> bool:
    async with get_db_session() as session:
        event = await session.get(NewsEvent, event_id)
        return event is not None and int(event.current_package_version) >= 1


async def _try_begin_build() -> bool:
    global _build_active
    async with _build_guard:
        if _build_active:
            return False
        _build_active = True
        return True


async def _end_build() -> None:
    global _build_active
    async with _build_guard:
        _build_active = False


def _validate_params(*, lookback_hours: int, max_articles: int) -> None:
    if lookback_hours < MIN_LOOKBACK_HOURS or lookback_hours > MAX_LOOKBACK_HOURS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"lookback_hours must be between {MIN_LOOKBACK_HOURS} and {MAX_LOOKBACK_HOURS}",
        )
    if max_articles < MIN_MAX_ARTICLES or max_articles > MAX_MAX_ARTICLES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"max_articles must be between {MIN_MAX_ARTICLES} and {MAX_MAX_ARTICLES}",
        )


async def build_heuristic_event_packages(
    *,
    lookback_hours: int = DEFAULT_LOOKBACK_HOURS,
    max_articles: int = DEFAULT_MAX_ARTICLES,
    dry_run: bool = False,
    eligible_categories: frozenset[str] | None = None,
    now: datetime | None = None,
    skip_concurrency_guard: bool = False,
) -> dict[str, Any]:
    """Build EventPackages from recent eligible articles. Idempotent across unchanged runs."""
    _validate_params(lookback_hours=lookback_hours, max_articles=max_articles)
    categories = eligible_categories or DEFAULT_ELIGIBLE_CATEGORIES
    clock = ensure_utc(now) or _utc_now()

    result = BuildResult(dry_run=dry_run)

    claimed = True
    if not skip_concurrency_guard:
        claimed = await _try_begin_build()
    if not claimed:
        result.concurrency_conflict = True
        result.errors.append(
            {
                "error_class": "concurrency_conflict",
                "message": "heuristic event build already in progress",
            }
        )
        return result.to_dict()

    try:
        records, skipped, considered = await _load_candidate_articles(
            lookback_hours=lookback_hours,
            max_articles=max_articles,
            eligible_categories=frozenset(c.lower() for c in categories),
            now=clock,
        )
        result.articles_considered = considered
        result.eligible_articles = len(records)
        result.skipped_articles = skipped

        groups = group_articles(records, lookback_hours=lookback_hours)
        result.groups_formed = len(groups)
        result.single_article_groups = sum(1 for g in groups if len(g.members) == 1)

        for group in groups:
            group_info = {
                "event_id": str(group.event_id),
                "topic_signature": group.topic_signature,
                "medoid_article_id": str(group.medoid.id),
                "article_ids": [str(m.id) for m in group.members],
                "article_count": len(group.members),
            }
            result.groups.append(group_info)
            try:
                package = build_event_package_dict(
                    group,
                    lookback_hours=lookback_hours,
                    generated_at=clock,
                    package_version=1,
                )
                parse_event_package(package)  # contract gate
                new_fp = fingerprint_from_package(package)

                if dry_run:
                    result.packages.append(package)
                    continue

                existing = await _current_package_payload(group.event_id)
                if existing is not None:
                    try:
                        old_fp = fingerprint_from_package(existing)
                    except (ValidationError, ValueError, TypeError):
                        old_fp = None
                    if old_fp == new_fp:
                        result.unchanged_events_skipped += 1
                        continue
                    existed = True
                else:
                    existed = await _event_exists(group.event_id)

                published = await publish_event_package(package)
                result.packages_published += 1
                if existed:
                    result.events_updated += 1
                else:
                    result.events_created += 1
                if "package" in published:
                    result.packages.append(published["package"])
            except HTTPException as exc:
                result.errors.append(
                    {
                        "event_id": str(group.event_id),
                        "error_class": "publish_failed",
                        "status_code": exc.status_code,
                        "detail": exc.detail,
                    }
                )
            except (ValidationError, ValueError) as exc:
                result.errors.append(
                    {
                        "event_id": str(group.event_id),
                        "error_class": "package_validation_failed",
                        "message": str(exc),
                    }
                )
            except Exception as exc:  # noqa: BLE001
                result.errors.append(
                    {
                        "event_id": str(group.event_id),
                        "error_class": "builder_error",
                        "message": exc.__class__.__name__,
                    }
                )

        return result.to_dict()
    finally:
        if not skip_concurrency_guard:
            await _end_build()
