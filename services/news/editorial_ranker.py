"""Pass B — Deterministic Editorial Engine for EventPackage ranking.

Read-time only: never mutates or persists EventPackages / rank scores.
"""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, status
from pydantic import ValidationError

from services.news.event_package import EventPackage, event_package_to_dict, parse_event_package
from services.news.event_package_service import (
    MAX_LIMIT as LIST_MAX_LIMIT,
    list_event_packages,
)
from services.ops.request_context import attach_request_id

EDITORIAL_RANKER_VERSION = "editorial_ranker.v1"
EDITORIAL_DEFAULT_TOP_N = 10
EDITORIAL_DEFAULT_CANDIDATE_LIMIT = 200
EDITORIAL_HALF_LIFE_HOURS = 36

MIN_TOP_N = 1
MAX_TOP_N = 100
MIN_CANDIDATE_LIMIT = 1
MAX_CANDIDATE_LIMIT = LIST_MAX_LIMIT

LIFECYCLE_BANDS: dict[str, int] = {
    "developing": 4,
    "open": 4,
    "stable": 3,
    "corrected": 2,
    "contested": 1,
    "closed": 0,
}
# Unknown lifecycle: below closed, still deterministic.
UNKNOWN_LIFECYCLE_BAND = -1

_LN2 = math.log(2)


@dataclass(frozen=True)
class EditorialSignals:
    lifecycle: str
    lifecycle_band: int
    conflict_open: bool
    conflict_band: int
    recency_score: float
    age_hours: float
    source_count: int
    article_count: int
    material_time: str | None


@dataclass
class RankedItem:
    rank: int
    event_id: str
    sort_key: list[Any]
    signals: dict[str, Any]
    reasons: list[str]
    package: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "rank": self.rank,
            "event_id": self.event_id,
            "sort_key": list(self.sort_key),
            "signals": dict(self.signals),
            "reasons": list(self.reasons),
            "package": self.package,
        }


@dataclass
class RankedFeedResult:
    editorial: dict[str, Any]
    items: list[RankedItem] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return attach_request_id(
            {
                "editorial": dict(self.editorial),
                "items": [item.to_dict() for item in self.items],
                "skipped": list(self.skipped),
                "skipped_count": len(self.skipped),
                "errors": list(self.errors),
            }
        )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def lifecycle_band(lifecycle: str | None) -> int:
    key = (lifecycle or "").strip().lower()
    if not key:
        return UNKNOWN_LIFECYCLE_BAND
    return LIFECYCLE_BANDS.get(key, UNKNOWN_LIFECYCLE_BAND)


def has_open_conflict(package: EventPackage) -> bool:
    if bool(package.consumer_hints.conflict_open):
        return True
    for conflict in package.conflicts:
        if conflict.resolution == "unresolved":
            return True
    return False


def material_time_for(package: EventPackage) -> datetime | None:
    updated = ensure_utc(package.updated_at)
    if updated is not None:
        return updated
    return ensure_utc(package.happened_at)


def recency_score_for(
    material_time: datetime | None,
    *,
    now: datetime,
    half_life_hours: float = EDITORIAL_HALF_LIFE_HOURS,
) -> tuple[float, float]:
    """Return (recency_score, age_hours). Future timestamps clamp to age 0."""
    if material_time is None:
        return 0.0, 0.0
    if half_life_hours <= 0:
        return 0.0, 0.0
    now_utc = ensure_utc(now) or _utc_now()
    age_hours = max(0.0, (now_utc - material_time).total_seconds() / 3600.0)
    score = math.exp(-_LN2 * age_hours / half_life_hours)
    # Clamp numerical noise; never exceed 1.0
    if score > 1.0:
        score = 1.0
    return score, age_hours


def extract_editorial_signals(
    package: EventPackage,
    *,
    now: datetime,
    half_life_hours: float = EDITORIAL_HALF_LIFE_HOURS,
) -> EditorialSignals:
    lifecycle = (package.lifecycle or "").strip()
    band = lifecycle_band(lifecycle)
    open_conflict = has_open_conflict(package)
    conflict_band = 0 if open_conflict else 1
    material = material_time_for(package)
    recency, age_hours = recency_score_for(
        material, now=now, half_life_hours=half_life_hours
    )
    return EditorialSignals(
        lifecycle=lifecycle or "unknown",
        lifecycle_band=band,
        conflict_open=open_conflict,
        conflict_band=conflict_band,
        recency_score=recency,
        age_hours=age_hours,
        source_count=len(package.sources),
        article_count=len(package.articles),
        material_time=material.isoformat() if material is not None else None,
    )


def build_editorial_sort_key(signals: EditorialSignals, event_id: str) -> tuple:
    """Lexicographic key for ascending sort: negate bands/scores so higher ranks first."""
    return (
        -signals.lifecycle_band,
        -signals.conflict_band,
        -signals.recency_score,
        -signals.source_count,
        -signals.article_count,
        str(event_id),
    )


def build_editorial_reasons(signals: EditorialSignals) -> list[str]:
    reasons = [
        f"lifecycle={signals.lifecycle}",
    ]
    if signals.conflict_open:
        reasons.append("open conflict")
    else:
        reasons.append("no open conflict")
    if signals.material_time is None:
        reasons.append("no material timestamp")
    else:
        reasons.append(f"recent (half-life {EDITORIAL_HALF_LIFE_HOURS}h)")
    reasons.append(f"{signals.source_count} sources")
    reasons.append(f"{signals.article_count} articles")
    return reasons


def signals_to_dict(signals: EditorialSignals) -> dict[str, Any]:
    return {
        "lifecycle": signals.lifecycle,
        "lifecycle_band": signals.lifecycle_band,
        "conflict_open": signals.conflict_open,
        "conflict_band": signals.conflict_band,
        "recency_score": signals.recency_score,
        "age_hours": signals.age_hours,
        "source_count": signals.source_count,
        "article_count": signals.article_count,
        "material_time": signals.material_time,
    }


def sort_key_to_list(key: tuple) -> list[Any]:
    """Public sort_key uses descending-semantic values (bands/scores as ranked)."""
    lifecycle_band_v = -int(key[0])
    conflict_band_v = -int(key[1])
    recency = -float(key[2])
    source_count = -int(key[3])
    article_count = -int(key[4])
    event_id = str(key[5])
    return [
        lifecycle_band_v,
        conflict_band_v,
        recency,
        source_count,
        article_count,
        event_id,
    ]


def _parse_package_safe(
    package: EventPackage | dict[str, Any],
) -> tuple[EventPackage | None, dict[str, Any] | None]:
    try:
        if isinstance(package, EventPackage):
            parsed = package
        else:
            parsed = parse_event_package(package)
        # Snapshot dict for output — never return caller's mutable dict as authoritative.
        return parsed, event_package_to_dict(parsed)
    except (ValidationError, ValueError, TypeError):
        return None, None


def rank_event_packages(
    packages: list[EventPackage | dict[str, Any]],
    *,
    now: datetime | None = None,
    top_n: int = EDITORIAL_DEFAULT_TOP_N,
    half_life_hours: float = EDITORIAL_HALF_LIFE_HOURS,
) -> RankedFeedResult:
    """Pure ranker. Input order must not affect output. Does not mutate inputs."""
    if top_n < MIN_TOP_N or top_n > MAX_TOP_N:
        raise ValueError(f"top_n must be between {MIN_TOP_N} and {MAX_TOP_N}")

    clock = ensure_utc(now) or _utc_now()
    # Deep-copy dict inputs so accidental mutation elsewhere cannot affect callers' objects
    # used only for identity checks in tests; we never write back.
    working: list[EventPackage | dict[str, Any]] = []
    for pkg in packages:
        if isinstance(pkg, dict):
            working.append(copy.deepcopy(pkg))
        else:
            working.append(pkg)

    skipped: list[dict[str, Any]] = []
    scored: list[tuple[tuple, RankedItem]] = []

    for pkg in working:
        parsed, snapshot = _parse_package_safe(pkg)
        if parsed is None or snapshot is None:
            event_id = None
            if isinstance(pkg, dict):
                event_id = pkg.get("event_id")
            skipped.append(
                {
                    "event_id": event_id,
                    "reason": "invalid_package",
                }
            )
            continue

        # Immutability: score from parsed model; output package is a fresh dict snapshot.
        signals = extract_editorial_signals(
            parsed, now=clock, half_life_hours=half_life_hours
        )
        event_id = str(parsed.event_id)
        key = build_editorial_sort_key(signals, event_id)
        item = RankedItem(
            rank=0,  # assigned after sort
            event_id=event_id,
            sort_key=sort_key_to_list(key),
            signals=signals_to_dict(signals),
            reasons=build_editorial_reasons(signals),
            package=snapshot,
        )
        scored.append((key, item))

    scored.sort(key=lambda row: row[0])
    ranked_items = [item for _key, item in scored[:top_n]]
    for idx, item in enumerate(ranked_items, start=1):
        item.rank = idx

    return RankedFeedResult(
        editorial={
            "version": EDITORIAL_RANKER_VERSION,
            "generated_at": clock.isoformat(),
            "candidate_count": len(scored),
            "ranked_count": len(ranked_items),
            "requested_top_n": top_n,
            "candidate_limit": None,
            "half_life_hours": half_life_hours,
            "skipped_invalid": len(skipped),
        },
        items=ranked_items,
        skipped=skipped,
    )


def _validate_rank_params(*, top_n: int, candidate_limit: int) -> None:
    if top_n < MIN_TOP_N or top_n > MAX_TOP_N:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"top_n must be between {MIN_TOP_N} and {MAX_TOP_N}",
        )
    if candidate_limit < MIN_CANDIDATE_LIMIT or candidate_limit > MAX_CANDIDATE_LIMIT:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"candidate_limit must be between {MIN_CANDIDATE_LIMIT} "
                f"and {MAX_CANDIDATE_LIMIT}"
            ),
        )
    if top_n > candidate_limit:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="top_n cannot exceed candidate_limit",
        )


async def rank_top_event_packages(
    *,
    top_n: int = EDITORIAL_DEFAULT_TOP_N,
    candidate_limit: int = EDITORIAL_DEFAULT_CANDIDATE_LIMIT,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Load recent EventPackages and return a ranked feed sidecar (read-only)."""
    _validate_rank_params(top_n=top_n, candidate_limit=candidate_limit)
    # list_event_packages already orders by material_updated_at DESC, id DESC.
    listed = await list_event_packages(limit=candidate_limit)
    raw_items = list(listed.get("items") or [])

    # Soft-validate again so a malformed row cannot poison the whole ranking response
    # if list ever returns loosely shaped data; normally list already validated.
    result = rank_event_packages(raw_items, now=now, top_n=top_n)
    result.editorial["candidate_limit"] = candidate_limit
    result.editorial["loaded_count"] = len(raw_items)
    return result.to_dict()
