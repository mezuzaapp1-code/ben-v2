"""Pass A — Heuristic EventPackage builder: grouping, identity, idempotency, ops."""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:5432/test")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-openai")

import main  # noqa: E402
from services.news import heuristic_event_builder as heb  # noqa: E402
from services.news.event_package import parse_event_package  # noqa: E402
from services.news.heuristic_event_builder import (  # noqa: E402
    BUILDER_VERSION,
    NAMESPACE_BEN_NEWS_EVENTS,
    ArticleRecord,
    build_event_package_dict,
    build_heuristic_event_packages,
    canonicalize_article_url,
    choose_summary,
    content_fingerprint_from_material,
    event_id_for,
    fingerprint_from_package,
    group_articles,
    jaccard,
    normalize_title_tokens,
    select_medoid,
    topic_signature_for,
)

ORG_A = "11111111-1111-1111-1111-111111111111"
T0 = datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc)
SOURCE_A = uuid.UUID("11111111-1111-4111-8111-111111111111")
SOURCE_B = uuid.UUID("22222222-2222-4222-8222-222222222222")


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_clerk")
    monkeypatch.setenv("ENFORCE_AUTH", "false")
    monkeypatch.setenv("AUTH_SHADOW_MODE", "true")
    monkeypatch.setenv("BEN_ANONYMOUS_ORG_ID", "00000000-0000-0000-0000-000000000001")
    monkeypatch.delenv("BEN_LOCAL_BETA_MODE", raising=False)
    # Reset single-flight between tests
    heb._build_active = False


def _admin_claims():
    return {
        "user_id": "user_1",
        "email": "a@b.com",
        "org_id": ORG_A,
        "org_role": "org:admin",
    }


def _rec(
    *,
    aid: str | uuid.UUID | None = None,
    source_id: uuid.UUID = SOURCE_A,
    source_name: str = "Reuters",
    title: str,
    url: str | None = None,
    summary: str | None = None,
    published_at: datetime | None = T0,
    created_at: datetime | None = None,
    category: str = "tech",
) -> ArticleRecord:
    article_id = uuid.UUID(str(aid)) if aid else uuid.uuid4()
    created = created_at or T0
    published = published_at
    tokens = tuple(normalize_title_tokens(title))
    final_url = url or f"https://example.com/{article_id}"
    return ArticleRecord(
        id=article_id,
        source_id=source_id,
        source_name=source_name,
        title=title,
        url=final_url,
        summary=summary,
        published_at=published,
        created_at=created,
        category=category,
        tokens=tokens,
        canonical_url=canonicalize_article_url(final_url),
        event_time=heb.article_event_time(published_at=published, created_at=created),
    )


# --- tokenization / similarity ------------------------------------------------


def test_normalize_title_tokens_deterministic():
    a = normalize_title_tokens("NVIDIA Reports Quarterly Results!!!")
    b = normalize_title_tokens("  nvidia reports quarterly results  ")
    assert a == b
    assert "the" not in a
    assert all(len(t) >= 3 for t in a)


def test_jaccard_similar_and_unrelated():
    a = normalize_title_tokens("OpenAI launches GPT-5 model for developers")
    b = normalize_title_tokens("OpenAI launches GPT-5 model for enterprise")
    c = normalize_title_tokens("Federal Reserve raises interest rates again")
    assert jaccard(a, b) >= 0.55
    assert jaccard(a, c) < 0.55


def test_canonical_url_hard_merge_signal():
    u1 = canonicalize_article_url("https://Example.com/story/abc#frag")
    u2 = canonicalize_article_url("https://example.com/story/abc/")
    assert u1 == u2


# --- grouping -----------------------------------------------------------------


def test_similar_titles_group_together():
    a = _rec(aid="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", title="OpenAI launches GPT-5 model for developers")
    b = _rec(
        aid="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        source_id=SOURCE_B,
        source_name="AP",
        title="OpenAI launches GPT-5 model for enterprise users",
        published_at=T0 + timedelta(hours=1),
    )
    groups = group_articles([b, a], lookback_hours=72)
    assert len(groups) == 1
    assert {m.id for m in groups[0].members} == {a.id, b.id}


def test_unrelated_titles_remain_separate():
    a = _rec(aid="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", title="OpenAI launches GPT-5 model for developers")
    b = _rec(
        aid="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        title="Federal Reserve raises interest rates sharply",
        published_at=T0 + timedelta(hours=1),
    )
    groups = group_articles([a, b], lookback_hours=72)
    assert len(groups) == 2


def test_canonical_url_hard_merge():
    url = "https://news.example.com/ai/openai-gpt5"
    a = _rec(
        aid="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        title="Completely different wording one",
        url=url,
    )
    b = _rec(
        aid="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        source_id=SOURCE_B,
        title="Totally unrelated headline two",
        url=url + "/",
        published_at=T0 + timedelta(hours=2),
    )
    groups = group_articles([a, b], lookback_hours=72)
    assert len(groups) == 1


def test_near_duplicate_syndicated_titles_merge():
    a = _rec(aid="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", title="Apple unveils new AI chips for Mac lineup")
    b = _rec(
        aid="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        source_id=SOURCE_B,
        title="Apple unveils new AI chips for Mac lineup",
        published_at=T0 + timedelta(minutes=30),
    )
    assert jaccard(a.tokens, b.tokens) >= 0.85
    groups = group_articles([a, b], lookback_hours=72)
    assert len(groups) == 1


def test_single_article_produces_valid_package():
    a = _rec(
        aid="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        title="Anthropic releases Claude update",
        summary="Anthropic released an update to Claude.",
    )
    groups = group_articles([a], lookback_hours=72)
    assert len(groups) == 1
    pkg = build_event_package_dict(groups[0], lookback_hours=72, generated_at=T0)
    parsed = parse_event_package(pkg)
    assert parsed.lifecycle == "developing"
    assert parsed.headline == a.title
    assert parsed.summary.startswith("Anthropic")
    assert parsed.current_facts == []
    assert parsed.conflicts == []
    assert parsed.why_it_matters == []
    assert parsed.sources[0].tier == "C"
    assert parsed.articles[0].role == "supports"
    assert parsed.consumer_hints.brief_eligible is False
    assert parsed.consumer_hints.conflict_open is False


def test_input_shuffle_identical_groups_and_uuids():
    arts = [
        _rec(aid="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", title="OpenAI launches GPT-5 model for developers"),
        _rec(
            aid="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            source_id=SOURCE_B,
            title="OpenAI launches GPT-5 model for enterprise",
            published_at=T0 + timedelta(hours=1),
        ),
        _rec(
            aid="cccccccc-cccc-cccc-cccc-cccccccccccc",
            title="Federal Reserve raises interest rates again",
            published_at=T0 + timedelta(hours=2),
        ),
    ]
    g1 = group_articles(arts, lookback_hours=72)
    g2 = group_articles(list(reversed(arts)), lookback_hours=72)
    assert [(str(g.event_id), tuple(str(m.id) for m in g.members)) for g in g1] == [
        (str(g.event_id), tuple(str(m.id) for m in g.members)) for g in g2
    ]


def test_uuid5_identity_stable():
    a = _rec(aid="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", title="OpenAI launches GPT-5 model for developers")
    groups = group_articles([a], lookback_hours=72)
    sig = topic_signature_for(a)
    expected = uuid.uuid5(NAMESPACE_BEN_NEWS_EVENTS, f"{BUILDER_VERSION}|{sig}")
    assert groups[0].event_id == expected
    assert event_id_for(builder_version=BUILDER_VERSION, topic_signature=sig) == expected


def test_membership_growth_preserves_event_id():
    a = _rec(
        aid="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        title="OpenAI launches GPT-5 model for developers",
        published_at=T0,
    )
    b = _rec(
        aid="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        source_id=SOURCE_B,
        title="OpenAI launches GPT-5 model for enterprise users",
        published_at=T0 + timedelta(hours=2),
    )
    g1 = group_articles([a], lookback_hours=72)
    g2 = group_articles([a, b], lookback_hours=72)
    assert len(g2) == 1
    # Medoid remains earliest published (a) → same topic signature / event_id
    assert g2[0].event_id == g1[0].event_id
    assert g2[0].medoid.id == a.id


def test_anti_chain_merge_behavior():
    """A~B and B~C but A unrelated to C after medoid filter when thresholds fail diameter."""
    # Construct with shared hub tokens so CC forms, then medoid filter may still keep all
    # if all similar to medoid. Use a chain where ends are dissimilar to medoid of the
    # wrong cluster: force three articles where A-B merge, B-C merge via near tokens,
    # but A not similar to C — medoid is earliest (A); C must fail vs A.
    a = _rec(
        aid="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        title="Alpha beta gamma delta epsilon zeta",
        published_at=T0,
    )
    b = _rec(
        aid="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        title="Alpha beta gamma delta epsilon eta",
        published_at=T0 + timedelta(hours=1),
    )
    c = _rec(
        aid="cccccccc-cccc-cccc-cccc-cccccccccccc",
        title="Epsilon eta theta iota kappa lambda",
        published_at=T0 + timedelta(hours=2),
    )
    # Ensure A-B merge, B-C merge-ish, A-C weak
    assert jaccard(a.tokens, b.tokens) >= 0.55
    assert jaccard(a.tokens, c.tokens) < 0.55
    groups = group_articles([a, b, c], lookback_hours=72)
    # Prefer false separation: C should not ride A via B if dissimilar to medoid A
    member_sets = [{str(m.id) for m in g.members} for g in groups]
    assert {"aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"} in member_sets or any(
        "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa" in s and "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb" in s for s in member_sets
    )
    ac_together = any(
        "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa" in s and "cccccccc-cccc-cccc-cccc-cccccccccccc" in s for s in member_sets
    )
    assert not ac_together


def test_utc_and_null_published_at_fallback():
    naive = datetime(2026, 7, 21, 12, 0, 0)  # naive → treated as UTC
    a = _rec(
        aid="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        title="Null published fallback article title",
        published_at=None,
        created_at=naive.replace(tzinfo=timezone.utc),
    )
    assert a.event_time.tzinfo is not None
    b = _rec(
        aid="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        title="Aware published article title here",
        published_at=T0,
    )
    med = select_medoid([a, b])
    # null published last → b wins when b has earlier/equal pub
    assert med.id == b.id


def test_empty_summary_falls_back_to_headline():
    a = _rec(title="Only headline available here today", summary=None)
    b = _rec(
        aid="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        title="Only headline available here today",
        summary="   ",
        published_at=T0 + timedelta(minutes=1),
    )
    assert choose_summary([a, b], headline=a.title) == a.title


def test_source_tier_defaults_to_c():
    a = _rec(aid="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", title="Google deepmind model release news")
    pkg = build_event_package_dict(group_articles([a])[0], lookback_hours=72, generated_at=T0)
    assert all(s["tier"] == "C" for s in pkg["sources"])


def test_package_validation_and_fingerprint_stable():
    a = _rec(
        aid="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        title="Microsoft expands Azure AI capacity",
        summary="Microsoft expanded Azure AI capacity in two regions.",
    )
    group = group_articles([a])[0]
    pkg1 = build_event_package_dict(group, lookback_hours=72, generated_at=T0)
    pkg2 = build_event_package_dict(group, lookback_hours=72, generated_at=T0 + timedelta(hours=5))
    parse_event_package(pkg1)
    assert fingerprint_from_package(pkg1) == fingerprint_from_package(pkg2)


# --- service / publish behavior ----------------------------------------------


class _CM:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *args):
        return False


def _article_row(**over):
    base = SimpleNamespace(
        id=uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        source_id=SOURCE_A,
        guid="g1",
        title="OpenAI launches GPT-5 model for developers",
        url="https://example.com/a",
        summary="OpenAI launched GPT-5 for developers.",
        image_url=None,
        published_at=T0,
        category="tech",
        created_at=T0,
    )
    for k, v in over.items():
        setattr(base, k, v)
    return base


def _source_row(**over):
    base = SimpleNamespace(
        id=SOURCE_A,
        name="Reuters",
        feed_url="https://example.com/rss",
        category="tech",
        enabled=True,
        language="en",
    )
    for k, v in over.items():
        setattr(base, k, v)
    return base


@pytest.mark.asyncio
async def test_dry_run_performs_no_writes():
    article = _article_row()
    source = _source_row()
    session = AsyncMock()
    result_mock = MagicMock()
    result_mock.all = MagicMock(return_value=[(article, source)])
    session.execute = AsyncMock(return_value=result_mock)

    publish = AsyncMock()
    with patch.object(heb, "get_db_session", return_value=_CM(session)), patch.object(
        heb, "publish_event_package", publish
    ):
        out = await build_heuristic_event_packages(
            lookback_hours=72,
            max_articles=50,
            dry_run=True,
            now=T0 + timedelta(hours=1),
            skip_concurrency_guard=True,
        )
    assert out["dry_run"] is True
    assert out["packages_published"] == 0
    assert out["groups_formed"] >= 1
    assert out["packages"]
    publish.assert_not_called()


@pytest.mark.asyncio
async def test_unchanged_rerun_skips_publish():
    article = _article_row()
    source = _source_row()
    session = AsyncMock()
    result_mock = MagicMock()
    result_mock.all = MagicMock(return_value=[(article, source)])
    session.execute = AsyncMock(return_value=result_mock)

    rec = heb.article_to_record(article, source)
    assert rec is not None
    group = group_articles([rec])[0]
    existing = build_event_package_dict(group, lookback_hours=72, generated_at=T0)
    existing["package_version"] = 3

    publish = AsyncMock()
    with patch.object(heb, "get_db_session", return_value=_CM(session)), patch.object(
        heb, "_current_package_payload", AsyncMock(return_value=existing)
    ), patch.object(heb, "publish_event_package", publish):
        out = await build_heuristic_event_packages(
            lookback_hours=72,
            max_articles=50,
            dry_run=False,
            now=T0 + timedelta(hours=1),
            skip_concurrency_guard=True,
        )
    assert out["unchanged_events_skipped"] == 1
    assert out["packages_published"] == 0
    publish.assert_not_called()


@pytest.mark.asyncio
async def test_membership_growth_increments_package_version():
    a = _article_row()
    b = _article_row(
        id=uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        source_id=SOURCE_B,
        title="OpenAI launches GPT-5 model for enterprise users",
        url="https://example.com/b",
        published_at=T0 + timedelta(hours=1),
        guid="g2",
    )
    source_a = _source_row()
    source_b = _source_row(id=SOURCE_B, name="AP")
    session = AsyncMock()
    result_mock = MagicMock()
    result_mock.all = MagicMock(return_value=[(a, source_a), (b, source_b)])
    session.execute = AsyncMock(return_value=result_mock)

    rec_a = heb.article_to_record(a, source_a)
    assert rec_a is not None
    old_group = group_articles([rec_a])[0]
    existing = build_event_package_dict(old_group, lookback_hours=72, generated_at=T0)
    existing["package_version"] = 1

    published_versions = []

    async def _publish(pkg):
        published_versions.append(pkg)
        body = dict(pkg)
        body["package_version"] = 2
        return {"package": body}

    with patch.object(heb, "get_db_session", return_value=_CM(session)), patch.object(
        heb, "_current_package_payload", AsyncMock(return_value=existing)
    ), patch.object(heb, "publish_event_package", side_effect=_publish):
        out = await build_heuristic_event_packages(
            lookback_hours=72,
            max_articles=50,
            dry_run=False,
            now=T0 + timedelta(hours=2),
            skip_concurrency_guard=True,
        )
    assert out["packages_published"] == 1
    assert out["events_updated"] == 1
    assert published_versions
    assert published_versions[0]["event_id"] == existing["event_id"]
    assert len(published_versions[0]["articles"]) == 2


@pytest.mark.asyncio
async def test_category_ineligible_skipped():
    article = _article_row(category="sports")
    source = _source_row(category="sports")
    # SQL path may still return if mocked without filter — service still checks
    session = AsyncMock()
    result_mock = MagicMock()
    result_mock.all = MagicMock(return_value=[(article, source)])
    session.execute = AsyncMock(return_value=result_mock)

    with patch.object(heb, "get_db_session", return_value=_CM(session)), patch.object(
        heb, "publish_event_package", AsyncMock()
    ):
        out = await build_heuristic_event_packages(
            lookback_hours=72,
            max_articles=50,
            dry_run=True,
            now=T0 + timedelta(hours=1),
            skip_concurrency_guard=True,
        )
    assert out["eligible_articles"] == 0
    assert any(s["reason"] == "category_ineligible" for s in out["skipped_articles"])


@pytest.mark.asyncio
async def test_malformed_article_metadata_handled():
    article = _article_row(title="   ", url="")
    source = _source_row()
    session = AsyncMock()
    result_mock = MagicMock()
    result_mock.all = MagicMock(return_value=[(article, source)])
    session.execute = AsyncMock(return_value=result_mock)

    with patch.object(heb, "get_db_session", return_value=_CM(session)):
        out = await build_heuristic_event_packages(
            lookback_hours=72,
            dry_run=True,
            now=T0 + timedelta(hours=1),
            skip_concurrency_guard=True,
        )
    assert out["eligible_articles"] == 0
    assert any(s["reason"] == "malformed_metadata" for s in out["skipped_articles"])


@pytest.mark.asyncio
async def test_concurrent_invocation_single_flight():
    async def _slow_build(**kwargs):
        await asyncio.sleep(0.05)
        # call real after claiming? simulate by holding guard
        return {"ok": True}

    heb._build_active = False
    # Start a build that holds the guard
    async def holder():
        claimed = await heb._try_begin_build()
        assert claimed
        await asyncio.sleep(0.1)
        await heb._end_build()

    t = asyncio.create_task(holder())
    await asyncio.sleep(0.01)
    out = await build_heuristic_event_packages(dry_run=True, skip_concurrency_guard=False)
    assert out["concurrency_conflict"] is True
    await t


# --- HTTP --------------------------------------------------------------------


def test_build_endpoint_requires_auth():
    client = TestClient(main.app)
    assert client.post("/api/internal/news/events/build").status_code == 401


def test_build_endpoint_calls_service():
    with patch(
        "auth.beta_gate.authenticate_request",
        return_value=("auth_valid", _admin_claims(), True),
    ), patch(
        "routers.news_sources.heuristic_event_builder.build_heuristic_event_packages",
        new_callable=AsyncMock,
        return_value={
            "articles_considered": 1,
            "eligible_articles": 1,
            "groups_formed": 1,
            "single_article_groups": 1,
            "events_created": 0,
            "events_updated": 0,
            "unchanged_events_skipped": 1,
            "packages_published": 0,
            "skipped_articles": [],
            "errors": [],
            "dry_run": True,
            "concurrency_conflict": False,
            "groups": [],
        },
    ) as build_mock:
        client = TestClient(main.app)
        res = client.post(
            "/api/internal/news/events/build",
            headers={"Authorization": "Bearer t"},
            json={"lookback_hours": 48, "max_articles": 100, "dry_run": True},
        )
    assert res.status_code == 200
    build_mock.assert_awaited_once()
    kwargs = build_mock.await_args.kwargs
    assert kwargs["lookback_hours"] == 48
    assert kwargs["dry_run"] is True


def test_openapi_registers_build_route():
    client = TestClient(main.app)
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/internal/news/events/build" in paths
    assert "post" in paths["/api/internal/news/events/build"]


def test_example_serialized_event_package_fixture_shape():
    """Document one example package produced by the builder for Pass B consumers."""
    a = _rec(
        aid="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        title="NVIDIA reports record data-center revenue",
        summary="NVIDIA reported record data-center revenue driven by AI chips.",
    )
    b = _rec(
        aid="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        source_id=SOURCE_B,
        source_name="Bloomberg",
        title="NVIDIA reports record data-center revenue growth",
        summary="NVIDIA posted record data-center revenue as AI demand continued.",
        published_at=T0 + timedelta(hours=3),
    )
    group = group_articles([a, b])[0]
    pkg = build_event_package_dict(group, lookback_hours=72, generated_at=T0)
    parsed = parse_event_package(pkg)
    assert parsed.schema_version == 1
    assert parsed.lifecycle == "developing"
    assert len(parsed.articles) == 2
    assert len(parsed.sources) == 2
    assert parsed.consumer_hints.feed_rank_signals["article_count"] == 2
    assert parsed.consumer_hints.feed_rank_signals["builder_version"] == BUILDER_VERSION
    assert any(n.startswith("content_fingerprint=") for n in parsed.provenance.policy_notes)
    # Stable identity from medoid (earliest pub = a)
    assert parsed.event_id == str(
        event_id_for(builder_version=BUILDER_VERSION, topic_signature=topic_signature_for(a))
    )
