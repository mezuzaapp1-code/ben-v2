"""Deterministic EventPackage hero image selection (RSS candidates only)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal, Sequence
from urllib.parse import urlparse

HeroOrigin = Literal["rss", "enclosure", "media", "unknown"]

# Obvious tracking / beacon URL fragments (lowercase).
_TRACKING_HINTS = (
    "pixel",
    "1x1",
    "spacer",
    "tracking",
    "/beacon",
    "analytics",
    "doubleclick",
    "facebook.com/tr",
    "google-analytics",
    "scorecardresearch",
)

_MIN_KNOWN_EDGE_PX = 64


@dataclass(frozen=True)
class ImageCandidate:
    url: str
    source_article_id: str
    origin: HeroOrigin = "rss"
    width: int | None = None
    height: int | None = None
    is_primary: bool = False
    article_sort_key: str = ""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_image_url(raw: str | None) -> str | None:
    text = (raw or "").strip()
    if not text or len(text) > 2048:
        return None
    parsed = urlparse(text)
    if (parsed.scheme or "").lower() != "https":
        return None
    host = (parsed.hostname or "").strip().lower()
    if not host or host in {"localhost", "127.0.0.1"}:
        return None
    # Drop fragment for dedupe identity; keep query (CDNs often need it).
    normalized = parsed._replace(fragment="").geturl()
    return normalized


def is_tracking_or_unusable(url: str, *, width: int | None, height: int | None) -> bool:
    lower = url.lower()
    if any(hint in lower for hint in _TRACKING_HINTS):
        return True
    if width is not None and height is not None:
        if width > 0 and height > 0 and (width < _MIN_KNOWN_EDGE_PX or height < _MIN_KNOWN_EDGE_PX):
            return True
    return False


def candidate_is_valid(candidate: ImageCandidate) -> bool:
    url = normalize_image_url(candidate.url)
    if not url:
        return False
    if is_tracking_or_unusable(url, width=candidate.width, height=candidate.height):
        return False
    return True


def hero_selection_score(candidate: ImageCandidate) -> float:
    """Deterministic 0..1 confidence for analytics; not used for ranking ties."""
    score = 0.45
    if candidate.is_primary:
        score += 0.30
    if (
        candidate.width is not None
        and candidate.height is not None
        and candidate.width >= _MIN_KNOWN_EDGE_PX
        and candidate.height >= _MIN_KNOWN_EDGE_PX
    ):
        score += 0.15
    origin_bonus = {"media": 0.10, "enclosure": 0.08, "rss": 0.05, "unknown": 0.0}
    score += origin_bonus.get(candidate.origin, 0.0)
    return round(min(1.0, max(0.0, score)), 4)


def select_hero_image(
    candidates: Sequence[ImageCandidate],
    *,
    selected_at: datetime | None = None,
) -> dict[str, Any] | None:
    """
    Pick one hero for an EventPackage version.

    Deterministic order:
    1. Prefer is_primary (medoid / primary article)
    2. Prefer known larger area when both dimensions present
    3. Prefer earlier origin rank: media < enclosure < rss < unknown
    4. Prefer lexicographically smaller article_id
    5. Prefer lexicographically smaller normalized URL
    """
    when = selected_at or _utc_now()
    origin_rank = {"media": 0, "enclosure": 1, "rss": 2, "unknown": 3}

    best_by_url: dict[str, tuple[tuple[Any, ...], ImageCandidate, str]] = {}
    for raw in candidates:
        url = normalize_image_url(raw.url)
        if not url:
            continue
        if not candidate_is_valid(
            ImageCandidate(
                url=url,
                source_article_id=raw.source_article_id,
                origin=raw.origin,
                width=raw.width,
                height=raw.height,
                is_primary=raw.is_primary,
                article_sort_key=raw.article_sort_key,
            )
        ):
            continue
        area = -1
        if raw.width and raw.height and raw.width > 0 and raw.height > 0:
            area = int(raw.width) * int(raw.height)
        sort_key = (
            0 if raw.is_primary else 1,
            0 if area > 0 else 1,
            -(area if area > 0 else 0),
            origin_rank.get(raw.origin, 9),
            (raw.article_sort_key or raw.source_article_id or ""),
            url,
        )
        prev = best_by_url.get(url)
        if prev is None or sort_key < prev[0]:
            best_by_url[url] = (sort_key, raw, url)

    if not best_by_url:
        return None

    valid = list(best_by_url.values())
    valid.sort(key=lambda row: row[0])
    _, chosen, url = valid[0]
    reason_parts = ["deterministic_v1"]
    if chosen.is_primary:
        reason_parts.append("primary_article")
    reason_parts.append(f"origin={chosen.origin}")
    score = hero_selection_score(chosen)
    return {
        "url": url,
        "source_article_id": str(chosen.source_article_id),
        "origin": chosen.origin,
        "width": chosen.width,
        "height": chosen.height,
        "selected_at": when.isoformat(),
        "selection_reason": ",".join(reason_parts),
        "selection_score": score,
        "hero_confidence": score,
    }
