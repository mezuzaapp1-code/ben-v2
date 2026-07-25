"""Pass B — Editorial Engine: lexicographic ranking, recency, determinism."""
from __future__ import annotations

import copy
import math
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:5432/test")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-openai")

import main  # noqa: E402
from services.news.editorial_ranker import (  # noqa: E402
    EDITORIAL_HALF_LIFE_HOURS,
    EDITORIAL_RANKER_VERSION,
    build_editorial_reasons,
    extract_editorial_signals,
    has_open_conflict,
    lifecycle_band,
    rank_event_packages,
    rank_top_event_packages,
    recency_score_for,
)
from services.news.event_package import EVENT_PACKAGE_SCHEMA_VERSION, parse_event_package  # noqa: E402

ORG_A = "11111111-1111-1111-1111-111111111111"
T0 = datetime(2026, 7, 25, 12, 0, 0, tzinfo=timezone.utc)
SOURCE_A = "11111111-1111-4111-8111-111111111111"
SOURCE_B = "22222222-2222-4222-8222-222222222222"
SOURCE_C = "33333333-3333-4333-8333-333333333333"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_clerk")
    monkeypatch.setenv("ENFORCE_AUTH", "false")
    monkeypatch.setenv("AUTH_SHADOW_MODE", "true")
    monkeypatch.setenv("BEN_ANONYMOUS_ORG_ID", "00000000-0000-0000-0000-000000000001")
    monkeypatch.delenv("BEN_LOCAL_BETA_MODE", raising=False)


def _admin_claims():
    return {
        "user_id": "user_1",
        "email": "a@b.com",
        "org_id": ORG_A,
        "org_role": "org:admin",
    }


def _pkg(
    *,
    event_id: str,
    lifecycle: str = "developing",
    updated_at: datetime | str | None = T0,
    happened_at: datetime | str | None = None,
    source_ids: list[str] | None = None,
    article_count: int | None = None,
    conflict_open: bool = False,
    unresolved_conflict: bool = False,
    headline: str | None = None,
) -> dict:
    sids = source_ids or [SOURCE_A]
    n_articles = article_count if article_count is not None else len(sids)
    articles = []
    sources = []
    for i in range(n_articles):
        sid = sids[i % len(sids)]
        aid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{event_id}:{i}"))
        articles.append(
            {
                "article_id": aid,
                "source_id": sid,
                "title": f"Title {i} for {event_id[:8]}",
                "url": f"https://example.com/{aid}",
                "published_at": T0.isoformat(),
                "role": "supports",
            }
        )
    for sid in sids:
        aids = [a["article_id"] for a in articles if a["source_id"] == sid]
        sources.append(
            {
                "source_id": sid,
                "name": f"Source {sid[:4]}",
                "tier": "C",
                "article_ids": aids,
            }
        )

    conflicts = []
    if unresolved_conflict:
        conflicts.append(
            {
                "topic": "disputed outcome",
                "positions": [
                    {
                        "claim_id": "c1",
                        "text": "Position one claims X happened",
                        "source_ids": [SOURCE_A],
                        "article_ids": [articles[0]["article_id"]],
                    },
                    {
                        "claim_id": "c2",
                        "text": "Position two claims Y happened",
                        "source_ids": [SOURCE_B],
                        "article_ids": [articles[0]["article_id"]],
                    },
                ],
                "resolution": "unresolved",
            }
        )
        lifecycle = "contested"
        conflict_open = True

    data = {
        "schema_version": EVENT_PACKAGE_SCHEMA_VERSION,
        "event_id": event_id,
        "package_version": 1,
        "lifecycle": lifecycle,
        "headline": headline or f"Headline {event_id[:8]}",
        "happened_at": happened_at.isoformat() if isinstance(happened_at, datetime) else happened_at,
        "updated_at": updated_at.isoformat() if isinstance(updated_at, datetime) else updated_at,
        "summary": f"Summary for {event_id[:8]} covering the material update.",
        "current_facts": [],
        "impacts": [],
        "why_it_matters": [],
        "conflicts": conflicts,
        "entities": [],
        "sources": sources,
        "articles": articles,
        "consumer_hints": {
            "alert_worthy": False,
            "brief_eligible": False,
            "conflict_open": conflict_open,
            "feed_rank_signals": {"article_count": 999, "source_count": 999},
        },
        "provenance": {
            "generated_at": T0.isoformat(),
            "schema_version": EVENT_PACKAGE_SCHEMA_VERSION,
            "policy_notes": [],
        },
    }
    # Validate contract for test fixtures
    parse_event_package(data)
    return data


def _ids(result) -> list[str]:
    return [item.event_id for item in result.items]


# --- lifecycle bands ----------------------------------------------------------


def test_lifecycle_band_ordering_values():
    assert lifecycle_band("developing") == lifecycle_band("open") == 4
    assert lifecycle_band("stable") == 3
    assert lifecycle_band("corrected") == 2
    assert lifecycle_band("contested") == 1
    assert lifecycle_band("closed") == 0
    assert lifecycle_band("totally-unknown") == -1


def test_developing_outranks_stable():
    a = _pkg(event_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", lifecycle="developing")
    b = _pkg(event_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", lifecycle="stable")
    result = rank_event_packages([b, a], now=T0)
    assert _ids(result) == [
        "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
    ]


def test_stable_outranks_corrected():
    a = _pkg(event_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", lifecycle="stable")
    b = _pkg(event_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", lifecycle="corrected")
    assert _ids(rank_event_packages([b, a], now=T0))[0] == a["event_id"]


def test_corrected_outranks_contested():
    a = _pkg(event_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", lifecycle="corrected")
    b = _pkg(
        event_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        unresolved_conflict=True,
        source_ids=[SOURCE_A, SOURCE_B],
        article_count=2,
    )
    assert _ids(rank_event_packages([b, a], now=T0))[0] == a["event_id"]


def test_contested_outranks_closed():
    a = _pkg(
        event_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        unresolved_conflict=True,
        source_ids=[SOURCE_A, SOURCE_B],
        article_count=2,
    )
    b = _pkg(event_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", lifecycle="closed")
    assert _ids(rank_event_packages([b, a], now=T0))[0] == a["event_id"]


def test_unknown_lifecycle_band_is_below_closed():
    assert lifecycle_band("mystery") == -1
    assert lifecycle_band("mystery") < lifecycle_band("closed")
    assert lifecycle_band("") == -1
    assert lifecycle_band(None) == -1


# --- conflict -----------------------------------------------------------------


def test_no_conflict_outranks_open_conflict_same_lifecycle():
    # Same lifecycle band via contested+unresolved vs developing without conflict is
    # covered elsewhere; here both use developing with conflict_open hint only.
    a = _pkg(
        event_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        lifecycle="developing",
        conflict_open=False,
    )
    b = _pkg(
        event_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        lifecycle="developing",
        conflict_open=True,
    )
    assert _ids(rank_event_packages([b, a], now=T0))[0] == a["event_id"]


def test_unresolved_conflict_detection():
    pkg = _pkg(
        event_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        unresolved_conflict=True,
        source_ids=[SOURCE_A, SOURCE_B],
        article_count=2,
    )
    parsed = parse_event_package(pkg)
    assert has_open_conflict(parsed) is True
    signals = extract_editorial_signals(parsed, now=T0)
    assert signals.conflict_open is True
    assert signals.conflict_band == 0


# --- recency ------------------------------------------------------------------


def test_newer_package_outranks_older():
    newer = _pkg(
        event_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        updated_at=T0,
    )
    older = _pkg(
        event_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        updated_at=T0 - timedelta(hours=24),
    )
    assert _ids(rank_event_packages([older, newer], now=T0))[0] == newer["event_id"]


def test_updated_at_preferred_over_happened_at():
    pkg = _pkg(
        event_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        updated_at=T0,
        happened_at=T0 - timedelta(days=10),
    )
    signals = extract_editorial_signals(parse_event_package(pkg), now=T0)
    assert signals.material_time == T0.isoformat()
    assert signals.age_hours == 0.0


def test_missing_timestamps_produce_recency_zero():
    # happened_at None and updated_at required by contract — use empty happened and
    # force material path by testing recency_score_for directly + package with only updated
    score, age = recency_score_for(None, now=T0)
    assert score == 0.0
    assert age == 0.0


def test_future_timestamps_clamp_to_age_zero():
    future = T0 + timedelta(hours=5)
    score, age = recency_score_for(future, now=T0)
    assert age == 0.0
    assert score == 1.0


def test_fixed_now_reproducible_recency():
    pkg = _pkg(
        event_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        updated_at=T0 - timedelta(hours=36),
    )
    r1 = rank_event_packages([pkg], now=T0)
    r2 = rank_event_packages([pkg], now=T0)
    assert r1.items[0].signals["recency_score"] == r2.items[0].signals["recency_score"]
    expected = math.exp(-math.log(2) * 36 / EDITORIAL_HALF_LIFE_HOURS)
    assert abs(r1.items[0].signals["recency_score"] - expected) < 1e-12


def test_naive_timestamps_handled_safely():
    naive = datetime(2026, 7, 25, 12, 0, 0)  # naive
    pkg = _pkg(
        event_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        updated_at=naive.isoformat(),  # no tz in string
    )
    # fromisoformat without tz → ensure_utc treats as UTC
    signals = extract_editorial_signals(parse_event_package(pkg), now=T0)
    assert signals.recency_score == 1.0


def test_aware_timestamps_normalized_to_utc():
    eastern = T0.astimezone(timezone(timedelta(hours=-4)))
    pkg = _pkg(
        event_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        updated_at=eastern,
    )
    signals = extract_editorial_signals(parse_event_package(pkg), now=T0)
    assert signals.age_hours == 0.0


# --- counts / ties ------------------------------------------------------------


def test_counts_from_arrays_not_feed_rank_signals():
    pkg = _pkg(
        event_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        source_ids=[SOURCE_A, SOURCE_B],
        article_count=2,
    )
    # fixture plants feed_rank_signals 999
    assert pkg["consumer_hints"]["feed_rank_signals"]["source_count"] == 999
    signals = extract_editorial_signals(parse_event_package(pkg), now=T0)
    assert signals.source_count == 2
    assert signals.article_count == 2


def test_more_sources_break_tie():
    a = _pkg(
        event_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        source_ids=[SOURCE_A, SOURCE_B, SOURCE_C],
        article_count=3,
    )
    b = _pkg(
        event_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        source_ids=[SOURCE_A],
        article_count=3,
    )
    # equal article count, more sources on a
    assert _ids(rank_event_packages([b, a], now=T0))[0] == a["event_id"]


def test_more_articles_break_tie():
    a = _pkg(
        event_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        source_ids=[SOURCE_A],
        article_count=5,
    )
    b = _pkg(
        event_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        source_ids=[SOURCE_A],
        article_count=2,
    )
    assert _ids(rank_event_packages([b, a], now=T0))[0] == a["event_id"]


def test_identical_signals_use_ascending_event_id():
    a = _pkg(event_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    b = _pkg(event_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    assert _ids(rank_event_packages([b, a], now=T0)) == [
        "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
    ]


def test_shuffled_input_identical_output():
    pkgs = [
        _pkg(event_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", lifecycle="developing"),
        _pkg(
            event_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            lifecycle="stable",
            updated_at=T0 - timedelta(hours=2),
        ),
        _pkg(
            event_id="cccccccc-cccc-cccc-cccc-cccccccccccc",
            lifecycle="developing",
            source_ids=[SOURCE_A, SOURCE_B],
            article_count=2,
        ),
    ]
    r1 = rank_event_packages(pkgs, now=T0)
    r2 = rank_event_packages(list(reversed(pkgs)), now=T0)
    assert _ids(r1) == _ids(r2)
    assert [i.sort_key for i in r1.items] == [i.sort_key for i in r2.items]
    assert [i.reasons for i in r1.items] == [i.reasons for i in r2.items]


def test_empty_sources_and_articles_count_zero():
    # Contract allows empty sources/articles
    data = _pkg(event_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    data["sources"] = []
    data["articles"] = []
    data["consumer_hints"]["feed_rank_signals"] = {}
    parse_event_package(data)
    signals = extract_editorial_signals(parse_event_package(data), now=T0)
    assert signals.source_count == 0
    assert signals.article_count == 0


def test_input_packages_not_mutated():
    pkg = _pkg(event_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    original = copy.deepcopy(pkg)
    rank_event_packages([pkg], now=T0)
    assert pkg == original


def test_top_n_truncation():
    pkgs = [
        _pkg(event_id=f"{i:032x}"[:8] + "-aaaa-aaaa-aaaa-" + f"{i:012x}")
        for i in range(5)
    ]
    # fix UUIDs properly
    pkgs = [
        _pkg(event_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        _pkg(event_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        _pkg(event_id="cccccccc-cccc-cccc-cccc-cccccccccccc"),
        _pkg(event_id="dddddddd-dddd-dddd-dddd-dddddddddddd"),
        _pkg(event_id="eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"),
    ]
    result = rank_event_packages(pkgs, now=T0, top_n=3)
    assert len(result.items) == 3
    assert result.editorial["ranked_count"] == 3
    assert result.editorial["candidate_count"] == 5
    assert result.editorial["requested_top_n"] == 3


def test_reasons_stable_and_non_empty():
    pkg = _pkg(event_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    signals = extract_editorial_signals(parse_event_package(pkg), now=T0)
    reasons = build_editorial_reasons(signals)
    assert reasons
    assert reasons == build_editorial_reasons(signals)
    assert any(r.startswith("lifecycle=") for r in reasons)


def test_metadata_version_and_counts():
    pkgs = [
        _pkg(event_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        _pkg(event_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
    ]
    result = rank_event_packages(pkgs, now=T0, top_n=10)
    assert result.editorial["version"] == EDITORIAL_RANKER_VERSION
    assert result.editorial["half_life_hours"] == EDITORIAL_HALF_LIFE_HOURS
    assert result.editorial["candidate_count"] == 2
    assert result.editorial["ranked_count"] == 2
    assert result.editorial["generated_at"] == T0.isoformat()


def test_invalid_package_skipped():
    good = _pkg(event_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    bad = {"event_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", "headline": "nope"}
    result = rank_event_packages([bad, good], now=T0)
    assert len(result.items) == 1
    assert result.items[0].event_id == good["event_id"]
    assert result.skipped
    assert result.skipped[0]["reason"] == "invalid_package"


def test_example_ranking_three_packages():
    developing_broad = _pkg(
        event_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        lifecycle="developing",
        updated_at=T0 - timedelta(hours=1),
        source_ids=[SOURCE_A, SOURCE_B],
        article_count=2,
    )
    developing_single = _pkg(
        event_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        lifecycle="developing",
        updated_at=T0 - timedelta(hours=1),
        source_ids=[SOURCE_A],
        article_count=1,
    )
    stable_old = _pkg(
        event_id="cccccccc-cccc-cccc-cccc-cccccccccccc",
        lifecycle="stable",
        updated_at=T0 - timedelta(hours=1),
    )
    result = rank_event_packages(
        [stable_old, developing_single, developing_broad],
        now=T0,
    )
    assert _ids(result) == [
        "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        "cccccccc-cccc-cccc-cccc-cccccccccccc",
    ]


# --- service / HTTP -----------------------------------------------------------


@pytest.mark.asyncio
async def test_candidate_limit_passed_to_list():
    pkgs = [_pkg(event_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")]
    with patch(
        "services.news.editorial_ranker.list_event_packages",
        new_callable=AsyncMock,
        return_value={"items": pkgs},
    ) as listed:
        out = await rank_top_event_packages(top_n=10, candidate_limit=50, now=T0)
    listed.assert_awaited_once_with(limit=50)
    assert out["editorial"]["candidate_limit"] == 50
    assert out["editorial"]["version"] == EDITORIAL_RANKER_VERSION
    assert len(out["items"]) == 1


def test_rank_endpoint_requires_auth():
    client = TestClient(main.app)
    assert client.get("/api/internal/news/events/rank").status_code == 401


def test_rank_endpoint_calls_service():
    with patch(
        "auth.beta_gate.authenticate_request",
        return_value=("auth_valid", _admin_claims(), True),
    ), patch(
        "routers.news_sources.editorial_ranker.rank_top_event_packages",
        new_callable=AsyncMock,
        return_value={
            "editorial": {"version": EDITORIAL_RANKER_VERSION, "ranked_count": 0},
            "items": [],
            "skipped": [],
            "skipped_count": 0,
            "errors": [],
        },
    ) as rank_mock:
        client = TestClient(main.app)
        res = client.get(
            "/api/internal/news/events/rank",
            headers={"Authorization": "Bearer t"},
            params={"top_n": 5, "candidate_limit": 40},
        )
    assert res.status_code == 200
    rank_mock.assert_awaited_once()
    kwargs = rank_mock.await_args.kwargs
    assert kwargs["top_n"] == 5
    assert kwargs["candidate_limit"] == 40


def test_openapi_registers_rank_route():
    client = TestClient(main.app)
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/internal/news/events/rank" in paths
    assert "get" in paths["/api/internal/news/events/rank"]
