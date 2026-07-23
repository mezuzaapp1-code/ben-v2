"""EventPackage v1 contract, conflict rules, and consumer read path."""
from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:5432/test")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-openai")

import main  # noqa: E402
from services.news.event_package import (  # noqa: E402
    EVENT_PACKAGE_SCHEMA_VERSION,
    EventPackage,
    parse_event_package,
)

ORG_A = "11111111-1111-1111-1111-111111111111"
EVENT_ID = uuid.UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")
ARTICLE_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
ARTICLE_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
SOURCE_A = "11111111-1111-4111-8111-111111111111"
SOURCE_B = "22222222-2222-4222-8222-222222222222"
T0 = datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc)


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


def _base_package(**overrides) -> dict:
    data = {
        "schema_version": EVENT_PACKAGE_SCHEMA_VERSION,
        "event_id": str(EVENT_ID),
        "package_version": 1,
        "lifecycle": "developing",
        "headline": "NVIDIA reports quarterly results",
        "happened_at": T0.isoformat(),
        "updated_at": T0.isoformat(),
        "summary": "NVIDIA reported quarterly results. Revenue figures were released.",
        "current_facts": [
            {
                "claim_id": "c-rev",
                "text": "Revenue was $30B",
                "status": "corroborated",
                "confidence": "high",
                "evidence_refs": [
                    {"article_id": ARTICLE_A, "source_id": SOURCE_A},
                    {"article_id": ARTICLE_B, "source_id": SOURCE_B},
                ],
            }
        ],
        "impacts": [
            {
                "claim_id": "c-guide",
                "text": "Guidance was raised",
                "status": "attributed",
                "confidence": "medium",
                "evidence_refs": [{"article_id": ARTICLE_A, "source_id": SOURCE_A}],
            }
        ],
        "why_it_matters": [
            {
                "text": "Supports continued AI infrastructure demand narrative.",
                "kind": "interpretive",
                "basis_claim_ids": ["c-rev", "c-guide"],
                "article_ids": [ARTICLE_A],
            }
        ],
        "conflicts": [],
        "entities": [{"type": "company", "name": "NVIDIA"}],
        "sources": [
            {
                "source_id": SOURCE_A,
                "name": "Reuters",
                "tier": "B",
                "article_ids": [ARTICLE_A],
            }
        ],
        "articles": [
            {
                "article_id": ARTICLE_A,
                "source_id": SOURCE_A,
                "title": "NVIDIA earnings",
                "url": "https://example.com/a",
                "published_at": T0.isoformat(),
                "role": "supports",
            }
        ],
        "consumer_hints": {
            "alert_worthy": True,
            "brief_eligible": True,
            "conflict_open": False,
            "feed_rank_signals": {"corroboration_count": 2},
        },
        "provenance": {
            "generated_at": T0.isoformat(),
            "schema_version": EVENT_PACKAGE_SCHEMA_VERSION,
            "policy_notes": [],
        },
    }
    data.update(overrides)
    return data


# --- contract ---


def test_schema_version_constant():
    assert EVENT_PACKAGE_SCHEMA_VERSION == 1


def test_valid_package_parses():
    pkg = parse_event_package(_base_package())
    assert pkg.event_id == str(EVENT_ID)
    assert pkg.impacts[0].claim_id == "c-guide"
    assert pkg.why_it_matters[0].kind == "interpretive"


def test_why_it_matters_requires_basis():
    bad = _base_package(
        why_it_matters=[{"text": "Matters a lot", "kind": "interpretive"}]
    )
    with pytest.raises(ValidationError):
        parse_event_package(bad)


def test_fact_requires_evidence_refs():
    bad = _base_package(
        current_facts=[
            {
                "claim_id": "c1",
                "text": "X",
                "status": "corroborated",
                "confidence": "high",
                "evidence_refs": [],
            }
        ]
    )
    with pytest.raises(ValidationError):
        parse_event_package(bad)


def test_unresolved_conflict_requires_contested_lifecycle():
    conflicted = _base_package(
        lifecycle="developing",
        consumer_hints={
            "alert_worthy": False,
            "brief_eligible": False,
            "conflict_open": True,
            "feed_rank_signals": {},
        },
        conflicts=[
            {
                "topic": "revenue figure",
                "resolution": "unresolved",
                "positions": [
                    {
                        "claim_id": "c-rev-a",
                        "text": "Revenue $30B",
                        "source_ids": [SOURCE_A],
                        "article_ids": [ARTICLE_A],
                    },
                    {
                        "claim_id": "c-rev-b",
                        "text": "Revenue $28B",
                        "source_ids": [SOURCE_B],
                        "article_ids": [ARTICLE_B],
                    },
                ],
            }
        ],
    )
    with pytest.raises(ValidationError):
        parse_event_package(conflicted)


def test_conflicted_claim_cannot_be_corroborated_fact():
    conflicted = _base_package(
        lifecycle="contested",
        brief_eligible=False,
        consumer_hints={
            "alert_worthy": False,
            "brief_eligible": False,
            "conflict_open": True,
            "feed_rank_signals": {},
        },
        current_facts=[
            {
                "claim_id": "c-rev-a",
                "text": "Revenue $30B",
                "status": "corroborated",
                "confidence": "high",
                "evidence_refs": [{"article_id": ARTICLE_A, "source_id": SOURCE_A}],
            }
        ],
        conflicts=[
            {
                "topic": "revenue figure",
                "resolution": "unresolved",
                "positions": [
                    {
                        "claim_id": "c-rev-a",
                        "text": "Revenue $30B",
                        "source_ids": [SOURCE_A],
                        "article_ids": [ARTICLE_A],
                    },
                    {
                        "claim_id": "c-rev-b",
                        "text": "Revenue $28B",
                        "source_ids": [SOURCE_B],
                        "article_ids": [ARTICLE_B],
                    },
                ],
            }
        ],
    )
    with pytest.raises(ValidationError):
        parse_event_package(conflicted)


def test_valid_contested_package():
    pkg = parse_event_package(
        _base_package(
            lifecycle="contested",
            current_facts=[],
            impacts=[],
            consumer_hints={
                "alert_worthy": True,
                "brief_eligible": False,
                "conflict_open": True,
                "feed_rank_signals": {},
            },
            conflicts=[
                {
                    "topic": "revenue figure",
                    "resolution": "unresolved",
                    "positions": [
                        {
                            "claim_id": "c-rev-a",
                            "text": "Revenue $30B",
                            "source_ids": [SOURCE_A],
                            "article_ids": [ARTICLE_A],
                        },
                        {
                            "claim_id": "c-rev-b",
                            "text": "Revenue $28B",
                            "source_ids": [SOURCE_B],
                            "article_ids": [ARTICLE_B],
                        },
                    ],
                }
            ],
            provenance={
                "generated_at": T0.isoformat(),
                "schema_version": 1,
                "policy_notes": ["summary withheld settled tone due to conflict"],
            },
        )
    )
    assert pkg.lifecycle == "contested"
    assert pkg.consumer_hints.conflict_open is True
    assert pkg.consumer_hints.brief_eligible is False


def test_brief_eligible_blocked_when_unresolved_conflict():
    bad = _base_package(
        lifecycle="contested",
        current_facts=[],
        consumer_hints={
            "alert_worthy": False,
            "brief_eligible": True,
            "conflict_open": True,
            "feed_rank_signals": {},
        },
        conflicts=[
            {
                "topic": "x",
                "resolution": "unresolved",
                "positions": [
                    {
                        "claim_id": "a",
                        "text": "A",
                        "source_ids": [SOURCE_A],
                        "article_ids": [ARTICLE_A],
                    },
                    {
                        "claim_id": "b",
                        "text": "B",
                        "source_ids": [SOURCE_B],
                        "article_ids": [ARTICLE_B],
                    },
                ],
            }
        ],
    )
    with pytest.raises(ValidationError):
        parse_event_package(bad)


def test_migration_007_exists_and_chains():
    versions = Path(__file__).resolve().parents[1] / "database" / "migrations" / "versions"
    path = versions / "007_news_event_packages_v1.py"
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert 'down_revision = "006_news_v0_1"' in text
    assert "news_event_packages" in text
    assert "news_events" in text


def test_models_define_event_package_tables():
    from database.models import NewsEvent, NewsEventPackage

    assert NewsEvent.__tablename__ == "news_events"
    assert NewsEventPackage.__tablename__ == "news_event_packages"


# --- HTTP consumer path ---


def test_list_events_requires_auth():
    client = TestClient(main.app)
    assert client.get("/api/internal/news/events").status_code == 401


def test_get_event_requires_auth():
    client = TestClient(main.app)
    assert client.get(f"/api/internal/news/events/{EVENT_ID}").status_code == 401


def test_list_events_returns_packages_only():
    package = parse_event_package(_base_package()).model_dump(mode="json")
    with patch(
        "auth.beta_gate.authenticate_request",
        return_value=("auth_valid", _admin_claims(), True),
    ), patch(
        "routers.news_sources.event_package_service.list_event_packages",
        new_callable=AsyncMock,
        return_value={"items": [package], "next_cursor": None},
    ):
        client = TestClient(main.app)
        res = client.get(
            "/api/internal/news/events",
            headers={"Authorization": "Bearer t"},
        )
    assert res.status_code == 200
    body = res.json()
    assert "items" in body
    assert body["items"][0]["schema_version"] == 1
    assert "current_facts" in body["items"][0]
    assert "impacts" in body["items"][0]
    assert "why_it_matters" in body["items"][0]
    assert "conflicts" in body["items"][0]


def test_get_event_returns_package_envelope():
    package = parse_event_package(_base_package()).model_dump(mode="json")
    with patch(
        "auth.beta_gate.authenticate_request",
        return_value=("auth_valid", _admin_claims(), True),
    ), patch(
        "routers.news_sources.event_package_service.get_event_package",
        new_callable=AsyncMock,
        return_value={"package": package},
    ):
        client = TestClient(main.app)
        res = client.get(
            f"/api/internal/news/events/{EVENT_ID}",
            headers={"Authorization": "Bearer t"},
        )
    assert res.status_code == 200
    assert res.json()["package"]["event_id"] == str(EVENT_ID)


def test_openapi_registers_event_package_routes():
    client = TestClient(main.app)
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/internal/news/events" in paths
    assert "get" in paths["/api/internal/news/events"]
    assert "/api/internal/news/events/{event_id}" in paths
    assert "get" in paths["/api/internal/news/events/{event_id}"]
    assert "/api/internal/news/events/{event_id}/package" in paths
    assert "put" in paths["/api/internal/news/events/{event_id}/package"]


def test_consumer_contract_module_forbids_raw_article_product_path():
    """Event package service must not import article_read_service."""
    root = Path(__file__).resolve().parents[1]
    src = (root / "services/news/event_package_service.py").read_text(encoding="utf-8")
    assert "article_read_service" not in src
    assert "NewsArticle" not in src
    contract = (root / "services/news/event_package.py").read_text(encoding="utf-8")
    assert "MUST read EventPackages only" in contract


@pytest.mark.asyncio
async def test_get_event_package_not_found():
    from services.news import event_package_service as eps

    session = AsyncMock()
    session.get = AsyncMock(return_value=None)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    with patch("services.news.event_package_service.get_db_session", return_value=session):
        with pytest.raises(Exception) as ei:
            await eps.get_event_package(EVENT_ID)
    assert ei.value.status_code == 404
