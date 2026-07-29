"""Pass C — Product News API: auth, projections, empty/error states."""
from __future__ import annotations

import copy
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:5432/test")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-openai")

import main  # noqa: E402
from services.news.editorial_ranker import EDITORIAL_RANKER_VERSION, rank_event_packages  # noqa: E402
from services.news.event_package import EVENT_PACKAGE_SCHEMA_VERSION, parse_event_package  # noqa: E402
from services.news.product_news_api import (  # noqa: E402
    PRODUCT_TOP_DEFAULT_LIMIT,
    get_top_news,
    product_candidate_limit,
    project_top_item,
    project_topic_detail,
)

ORG_A = "11111111-1111-1111-1111-111111111111"
EVENT_A = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
EVENT_B = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
EVENT_C = uuid.UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
SOURCE_A = "11111111-1111-4111-8111-111111111111"
SOURCE_B = "22222222-2222-4222-8222-222222222222"
ARTICLE_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
ARTICLE_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
T0 = datetime(2026, 7, 25, 18, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_clerk")
    monkeypatch.setenv("ENFORCE_AUTH", "false")
    monkeypatch.setenv("AUTH_SHADOW_MODE", "true")
    monkeypatch.setenv("BEN_ANONYMOUS_ORG_ID", "00000000-0000-0000-0000-000000000001")
    monkeypatch.delenv("BEN_LOCAL_BETA_MODE", raising=False)

    async def _no_images(*_args, **_kwargs):
        return {}

    async def _passthrough_translate(*, fields, **_kwargs):
        return {
            "texts": dict(fields),
            "locale": "en",
            "fallback_fields": [],
            "field_translation_status": {},
            "translation_status": None,
            "original_locale_indicator": False,
            "translation_engine_version": "news_mt_v1",
        }

    monkeypatch.setattr(
        "services.news.product_news_api.load_article_image_map",
        _no_images,
    )
    monkeypatch.setattr(
        "services.news.product_news_api.translate_presentation_fields",
        _passthrough_translate,
    )


def _member_claims():
    return {
        "user_id": "user_member",
        "email": "member@example.com",
        "org_id": ORG_A,
        "org_role": "org:member",
    }


def _admin_claims():
    return {
        "user_id": "user_admin",
        "email": "admin@example.com",
        "org_id": ORG_A,
        "org_role": "org:admin",
    }


def _package(
    *,
    event_id: uuid.UUID,
    headline: str,
    lifecycle: str = "developing",
    sources: int = 1,
    conflict_open: bool = False,
) -> dict:
    source_ids = [SOURCE_A] if sources == 1 else [SOURCE_A, SOURCE_B]
    articles = []
    for i, sid in enumerate(source_ids):
        aid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{event_id}:{i}"))
        articles.append(
            {
                "article_id": aid,
                "source_id": sid,
                "title": f"{headline} coverage {i}",
                "url": f"https://example.com/{aid}",
                "published_at": T0.isoformat(),
                "role": "supports",
            }
        )
    src_payload = []
    for sid in source_ids:
        src_payload.append(
            {
                "source_id": sid,
                "name": f"Outlet {sid[:4]}",
                "tier": "C",
                "article_ids": [a["article_id"] for a in articles if a["source_id"] == sid],
            }
        )
    data = {
        "schema_version": EVENT_PACKAGE_SCHEMA_VERSION,
        "event_id": str(event_id),
        "package_version": 1,
        "lifecycle": lifecycle,
        "headline": headline,
        "happened_at": T0.isoformat(),
        "updated_at": T0.isoformat(),
        "summary": f"{headline} summary for product consumers.",
        "current_facts": [],
        "impacts": [],
        "why_it_matters": [],
        "conflicts": [],
        "entities": [],
        "sources": src_payload,
        "articles": articles,
        "consumer_hints": {
            "alert_worthy": False,
            "brief_eligible": False,
            "conflict_open": conflict_open,
            "feed_rank_signals": {
                "article_count": len(articles),
                "source_count": len(src_payload),
                "builder_version": "heuristic_event_builder.v1",
            },
        },
        "provenance": {
            "generated_at": T0.isoformat(),
            "schema_version": EVENT_PACKAGE_SCHEMA_VERSION,
            "policy_notes": [
                "builder_version=heuristic_event_builder.v1",
                "topic_signature=secret-internal",
                "content_fingerprint=deadbeef",
            ],
        },
    }
    parse_event_package(data)
    return data


def _ranked_feed(packages: list[dict], *, top_n: int = 10) -> dict:
    result = rank_event_packages(packages, now=T0, top_n=top_n)
    payload = result.to_dict()
    payload["editorial"]["candidate_limit"] = 200
    payload["editorial"]["loaded_count"] = len(packages)
    return payload


# --- pure projection ----------------------------------------------------------


def test_product_candidate_limit_policy():
    assert product_candidate_limit(10) == 200
    assert product_candidate_limit(20) == 400
    assert product_candidate_limit(50) == 500  # clamped to MAX_CANDIDATE_LIMIT


def test_list_projection_excludes_internal_fields():
    pkg = _package(event_id=EVENT_A, headline="OpenAI launches model", sources=2)
    ranked = _ranked_feed([pkg])["items"][0]
    item = project_top_item(ranked).model_dump(mode="json")
    assert "package" not in item
    assert "sort_key" not in item
    assert "provenance" not in item
    assert "policy_notes" not in item
    assert "feed_rank_signals" not in item
    assert "topic_signature" not in str(item)
    assert "content_fingerprint" not in str(item)
    assert item["headline"] == "OpenAI launches model"
    assert item["reasons"]
    assert item["source_count"] == 2


def test_topic_projection_includes_sources_articles_empty_claims():
    pkg = _package(event_id=EVENT_A, headline="Topic detail package", sources=2)
    topic = project_topic_detail(pkg).model_dump(mode="json")
    assert topic["event_id"] == str(EVENT_A)
    assert len(topic["sources"]) == 2
    assert len(topic["articles"]) == 2
    assert topic["claims"] == []
    assert "provenance" not in topic
    assert "policy_notes" not in topic
    assert "consumer_hints" not in topic


def test_project_does_not_mutate_package():
    pkg = _package(event_id=EVENT_A, headline="Immutable package")
    original = copy.deepcopy(pkg)
    project_topic_detail(pkg)
    ranked = _ranked_feed([pkg])["items"][0]
    project_top_item(ranked)
    assert pkg == original


# --- service ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_feed_returns_empty_items():
    with patch(
        "services.news.product_news_api.rank_top_event_packages",
        new_callable=AsyncMock,
        return_value={
            "editorial": {
                "version": EDITORIAL_RANKER_VERSION,
                "generated_at": T0.isoformat(),
                "candidate_count": 0,
                "ranked_count": 0,
            },
            "items": [],
        },
    ) as rank_mock:
        out = await get_top_news(limit=10)
    assert out["items"] == []
    assert out["editorial_version"] == EDITORIAL_RANKER_VERSION
    assert out["generated_at"] == T0.isoformat()
    kwargs = rank_mock.await_args.kwargs
    assert kwargs["top_n"] == 10
    assert kwargs["candidate_limit"] == product_candidate_limit(10)
    assert "now" not in kwargs


@pytest.mark.asyncio
async def test_ranked_order_matches_editorial_engine():
    pkgs = [
        _package(event_id=EVENT_C, headline="Stable older", lifecycle="stable"),
        _package(event_id=EVENT_B, headline="Developing single", sources=1),
        _package(event_id=EVENT_A, headline="Developing broad", sources=2),
    ]
    feed = _ranked_feed(pkgs, top_n=10)
    with patch(
        "services.news.product_news_api.rank_top_event_packages",
        new_callable=AsyncMock,
        return_value=feed,
    ):
        out = await get_top_news(limit=10)
    assert [i["event_id"] for i in out["items"]] == [i["event_id"] for i in feed["items"]]
    assert out["items"][0]["reasons"] == feed["items"][0]["reasons"]


# --- HTTP ---------------------------------------------------------------------


def test_top_allows_unsigned_when_auth_not_enforced():
    feed = _ranked_feed(
        [_package(event_id=EVENT_A, headline="Public readable", sources=1)],
        top_n=10,
    )
    with patch(
        "services.news.product_news_api.rank_top_event_packages",
        new_callable=AsyncMock,
        return_value=feed,
    ):
        client = TestClient(main.app)
        res = client.get("/api/news/top")
    assert res.status_code == 200
    assert len(res.json()["items"]) == 1


def test_topic_allows_unsigned_when_auth_not_enforced():
    detail = {
        "event_id": str(EVENT_A),
        "package_version": 1,
        "schema_version": EVENT_PACKAGE_SCHEMA_VERSION,
        "headline": "Topic",
        "summary": "Summary",
        "why_it_matters": [],
        "lifecycle": "developing",
        "conflict_open": False,
        "happened_at": None,
        "updated_at": T0.isoformat(),
        "sources": [],
        "articles": [],
        "current_facts": [],
        "conflicts": [],
        "claims": [],
    }
    with patch(
        "services.news.product_news_api.get_topic_detail",
        new_callable=AsyncMock,
        return_value={"topic": detail},
    ):
        client = TestClient(main.app)
        res = client.get(f"/api/news/topics/{EVENT_A}")
    assert res.status_code == 200


def test_top_requires_auth_when_enforced(monkeypatch):
    monkeypatch.setenv("ENFORCE_AUTH", "true")
    client = TestClient(main.app)
    assert client.get("/api/news/top").status_code == 401


def test_topic_requires_auth_when_enforced(monkeypatch):
    monkeypatch.setenv("ENFORCE_AUTH", "true")
    client = TestClient(main.app)
    assert client.get(f"/api/news/topics/{EVENT_A}").status_code == 401


def test_signed_in_member_can_access_top():
    feed = _ranked_feed(
        [_package(event_id=EVENT_A, headline="Member readable", sources=2)],
        top_n=10,
    )
    with patch(
        "auth.beta_gate.authenticate_request",
        return_value=("auth_valid", _member_claims(), True),
    ), patch(
        "services.news.product_news_api.rank_top_event_packages",
        new_callable=AsyncMock,
        return_value=feed,
    ):
        client = TestClient(main.app)
        res = client.get("/api/news/top", headers={"Authorization": "Bearer t"})
    assert res.status_code == 200
    body = res.json()
    assert body["editorial_version"] == EDITORIAL_RANKER_VERSION
    assert len(body["items"]) == 1
    assert body["items"][0]["rank"] == 1
    assert "package" not in body["items"][0]
    assert "sort_key" not in body["items"][0]


def test_news_admin_not_required_for_product():
    """org:member must succeed; privilege assert must not be invoked."""
    feed = _ranked_feed([_package(event_id=EVENT_A, headline="No admin needed")])
    with patch(
        "auth.beta_gate.authenticate_request",
        return_value=("auth_valid", _member_claims(), True),
    ), patch(
        "auth.news_registry_privileges.assert_can_manage_news_sources",
        side_effect=AssertionError("news-admin must not be required"),
    ), patch(
        "services.news.product_news_api.rank_top_event_packages",
        new_callable=AsyncMock,
        return_value=feed,
    ):
        client = TestClient(main.app)
        res = client.get("/api/news/top", headers={"Authorization": "Bearer t"})
    assert res.status_code == 200


def test_default_limit_at_most_10():
    pkgs = [
        _package(
            event_id=uuid.UUID(int=i),
            headline=f"Story {i}",
        )
        for i in range(1, 16)
    ]
    feed = _ranked_feed(pkgs, top_n=PRODUCT_TOP_DEFAULT_LIMIT)
    with patch(
        "auth.beta_gate.authenticate_request",
        return_value=("auth_valid", _member_claims(), True),
    ), patch(
        "services.news.product_news_api.rank_top_event_packages",
        new_callable=AsyncMock,
        return_value=feed,
    ) as rank_mock:
        client = TestClient(main.app)
        res = client.get("/api/news/top", headers={"Authorization": "Bearer t"})
    assert res.status_code == 200
    assert len(res.json()["items"]) <= 10
    assert rank_mock.await_args.kwargs["top_n"] == 10


def test_custom_valid_limit():
    feed = _ranked_feed(
        [_package(event_id=EVENT_A, headline="A"), _package(event_id=EVENT_B, headline="B")],
        top_n=2,
    )
    with patch(
        "auth.beta_gate.authenticate_request",
        return_value=("auth_valid", _member_claims(), True),
    ), patch(
        "services.news.product_news_api.rank_top_event_packages",
        new_callable=AsyncMock,
        return_value=feed,
    ) as rank_mock:
        client = TestClient(main.app)
        res = client.get(
            "/api/news/top",
            headers={"Authorization": "Bearer t"},
            params={"limit": 2},
        )
    assert res.status_code == 200
    assert rank_mock.await_args.kwargs["top_n"] == 2
    assert rank_mock.await_args.kwargs["candidate_limit"] == product_candidate_limit(2)


def test_invalid_limit_below_minimum():
    with patch(
        "auth.beta_gate.authenticate_request",
        return_value=("auth_valid", _member_claims(), True),
    ):
        client = TestClient(main.app)
        res = client.get(
            "/api/news/top",
            headers={"Authorization": "Bearer t"},
            params={"limit": 0},
        )
    assert res.status_code == 422


def test_invalid_limit_above_maximum():
    with patch(
        "auth.beta_gate.authenticate_request",
        return_value=("auth_valid", _member_claims(), True),
    ):
        client = TestClient(main.app)
        res = client.get(
            "/api/news/top",
            headers={"Authorization": "Bearer t"},
            params={"limit": 51},
        )
    assert res.status_code == 422


def test_product_endpoint_does_not_expose_now_or_candidate_limit():
    client = TestClient(main.app)
    schema = client.get("/openapi.json").json()["paths"]["/api/news/top"]["get"]
    params = {p["name"] for p in schema.get("parameters", [])}
    assert "limit" in params
    assert "now" not in params
    assert "candidate_limit" not in params


def test_empty_feed_http_200():
    with patch(
        "auth.beta_gate.authenticate_request",
        return_value=("auth_valid", _member_claims(), True),
    ), patch(
        "services.news.product_news_api.rank_top_event_packages",
        new_callable=AsyncMock,
        return_value={
            "editorial": {
                "version": EDITORIAL_RANKER_VERSION,
                "generated_at": T0.isoformat(),
                "candidate_count": 0,
                "ranked_count": 0,
            },
            "items": [],
        },
    ):
        client = TestClient(main.app)
        res = client.get("/api/news/top", headers={"Authorization": "Bearer t"})
    assert res.status_code == 200
    assert res.json()["items"] == []


def test_topic_detail_returns_projection():
    pkg = _package(event_id=EVENT_A, headline="Detail headline", sources=2)
    with patch(
        "auth.beta_gate.authenticate_request",
        return_value=("auth_valid", _member_claims(), True),
    ), patch(
        "services.news.product_news_api.get_event_package",
        new_callable=AsyncMock,
        return_value={"package": pkg},
    ):
        client = TestClient(main.app)
        res = client.get(
            f"/api/news/topics/{EVENT_A}",
            headers={"Authorization": "Bearer t"},
        )
    assert res.status_code == 200
    topic = res.json()["topic"]
    assert topic["headline"] == "Detail headline"
    assert len(topic["sources"]) == 2
    assert len(topic["articles"]) == 2
    assert topic["claims"] == []
    assert "provenance" not in topic
    assert topic["locale"] == "en"
    assert topic["hero_image"] is None


def test_top_accepts_locale_query():
    feed = _ranked_feed(
        [_package(event_id=EVENT_A, headline="Public readable", sources=1)],
        top_n=10,
    )

    async def _he_translate(*, fields, locale, **_kwargs):
        assert locale == "he"
        return {
            "texts": {
                "headline": "כותרת בעברית",
                "summary": fields["summary"],
            },
            "locale": "he",
            "fallback_fields": ["summary"],
            "field_translation_status": {
                "headline": "generated",
                "summary": "fallback_en",
            },
            "translation_status": "fallback_en",
            "original_locale_indicator": True,
            "translation_engine_version": "news_mt_v1",
        }

    with patch(
        "services.news.product_news_api.rank_top_event_packages",
        new_callable=AsyncMock,
        return_value=feed,
    ), patch(
        "services.news.product_news_api.translate_presentation_fields",
        new=_he_translate,
    ):
        client = TestClient(main.app)
        res = client.get("/api/news/top", params={"locale": "he"})
    assert res.status_code == 200
    body = res.json()
    assert body["locale"] == "he"
    assert body["items"][0]["headline"] == "כותרת בעברית"
    assert body["items"][0]["original_locale_indicator"] is True
    assert body["items"][0]["translation_status"] == "fallback_en"
    assert body["items"][0]["field_translation_status"]["headline"] == "generated"


def test_packaged_hero_is_projected():
    pkg = _package(event_id=EVENT_A, headline="With hero", sources=1)
    pkg["hero_image"] = {
        "url": "https://cdn.example.com/hero.jpg",
        "source_article_id": ARTICLE_A,
        "origin": "rss",
        "width": None,
        "height": None,
        "selected_at": T0.isoformat(),
        "selection_reason": "deterministic_v1,primary_article,origin=rss",
        "selection_score": 0.8,
        "hero_confidence": 0.8,
    }
    detail = project_topic_detail(pkg)
    assert detail.hero_image is not None
    assert detail.hero_image.url == "https://cdn.example.com/hero.jpg"
    assert detail.image_url == "https://cdn.example.com/hero.jpg"
    assert detail.hero_image.selection_score == 0.8
    assert detail.hero_image.hero_confidence == 0.8


def test_unknown_event_404():
    from fastapi import HTTPException

    with patch(
        "auth.beta_gate.authenticate_request",
        return_value=("auth_valid", _member_claims(), True),
    ), patch(
        "services.news.product_news_api.get_event_package",
        new_callable=AsyncMock,
        side_effect=HTTPException(status_code=404, detail="Event package not found"),
    ):
        client = TestClient(main.app)
        res = client.get(
            f"/api/news/topics/{EVENT_A}",
            headers={"Authorization": "Bearer t"},
        )
    assert res.status_code == 404


def test_invalid_uuid_validation():
    with patch(
        "auth.beta_gate.authenticate_request",
        return_value=("auth_valid", _member_claims(), True),
    ):
        client = TestClient(main.app)
        res = client.get(
            "/api/news/topics/not-a-uuid",
            headers={"Authorization": "Bearer t"},
        )
    assert res.status_code == 422


def test_internal_admin_endpoints_remain_protected():
    """Member cannot hit admin rank/build; admin still required."""
    with patch(
        "auth.beta_gate.authenticate_request",
        return_value=("auth_valid", _member_claims(), True),
    ):
        client = TestClient(main.app)
        rank = client.get(
            "/api/internal/news/events/rank",
            headers={"Authorization": "Bearer t"},
        )
        build = client.post(
            "/api/internal/news/events/build",
            headers={"Authorization": "Bearer t"},
            json={"dry_run": True},
        )
    assert rank.status_code == 403
    assert build.status_code == 403


def test_internal_rank_still_works_for_admin():
    with patch(
        "auth.beta_gate.authenticate_request",
        return_value=("auth_valid", _admin_claims(), True),
    ), patch(
        "routers.news_sources.editorial_ranker.rank_top_event_packages",
        new_callable=AsyncMock,
        return_value={
            "editorial": {"version": EDITORIAL_RANKER_VERSION},
            "items": [],
            "skipped": [],
            "skipped_count": 0,
            "errors": [],
        },
    ):
        client = TestClient(main.app)
        res = client.get(
            "/api/internal/news/events/rank",
            headers={"Authorization": "Bearer t"},
        )
    assert res.status_code == 200


def test_openapi_registers_product_routes():
    client = TestClient(main.app)
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/news/top" in paths
    assert "/api/news/topics/{event_id}" in paths
    assert "get" in paths["/api/news/top"]
    assert "get" in paths["/api/news/topics/{event_id}"]


def test_example_top_response_shape():
    pkgs = [
        _package(event_id=EVENT_A, headline="NVIDIA revenue", sources=2),
        _package(event_id=EVENT_B, headline="Fed rates", sources=1),
    ]
    feed = _ranked_feed(pkgs)
    with patch(
        "auth.beta_gate.authenticate_request",
        return_value=("auth_valid", _member_claims(), True),
    ), patch(
        "services.news.product_news_api.rank_top_event_packages",
        new_callable=AsyncMock,
        return_value=feed,
    ):
        client = TestClient(main.app)
        body = client.get("/api/news/top", headers={"Authorization": "Bearer t"}).json()
    assert set(body.keys()) >= {"generated_at", "editorial_version", "items"}
    item = body["items"][0]
    for key in (
        "rank",
        "event_id",
        "headline",
        "summary",
        "why_it_matters",
        "source_count",
        "article_count",
        "updated_at",
        "happened_at",
        "lifecycle",
        "conflict_open",
        "reasons",
    ):
        assert key in item


def test_no_publish_called_from_product_paths():
    feed = _ranked_feed([_package(event_id=EVENT_A, headline="No write")])
    publish = AsyncMock()
    with patch(
        "auth.beta_gate.authenticate_request",
        return_value=("auth_valid", _member_claims(), True),
    ), patch(
        "services.news.product_news_api.rank_top_event_packages",
        new_callable=AsyncMock,
        return_value=feed,
    ), patch(
        "services.news.product_news_api.get_event_package",
        new_callable=AsyncMock,
        return_value={"package": _package(event_id=EVENT_A, headline="No write")},
    ), patch(
        "services.news.event_package_service.publish_event_package",
        publish,
    ):
        client = TestClient(main.app)
        assert client.get("/api/news/top", headers={"Authorization": "Bearer t"}).status_code == 200
        assert client.get(
            f"/api/news/topics/{EVENT_A}",
            headers={"Authorization": "Bearer t"},
        ).status_code == 200
    publish.assert_not_called()
