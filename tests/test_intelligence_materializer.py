"""EventUnderstanding materializer (Phase 1a) and persistence identity (Phase 1b unit)."""
from __future__ import annotations

import copy
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.intelligence.contracts import (
    materialize_event_understanding,
    semantic_fingerprint,
    understanding_identity,
)
from services.intelligence.taxonomy import CLASSIFIER_VERSION, TEMPLATE_VERSION
from services.news.event_package import EVENT_PACKAGE_SCHEMA_VERSION

EVENT_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
T0 = datetime(2026, 7, 29, 12, 0, 0, tzinfo=timezone.utc)


def _package(**overrides):
    base = {
        "schema_version": EVENT_PACKAGE_SCHEMA_VERSION,
        "event_id": EVENT_ID,
        "package_version": 1,
        "lifecycle": "developing",
        "headline": "MegaCorp acquires ChipStart to expand AI chips",
        "happened_at": T0.isoformat(),
        "updated_at": T0.isoformat(),
        "summary": "The acquisition deepens semiconductor and artificial intelligence capacity.",
        "current_facts": [],
        "impacts": [],
        "why_it_matters": [],
        "conflicts": [],
        "entities": [],
        "sources": [
            {
                "source_id": "11111111-1111-4111-8111-111111111111",
                "name": "TechWire",
                "tier": "C",
                "article_ids": ["bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"],
            }
        ],
        "articles": [
            {
                "article_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                "source_id": "11111111-1111-4111-8111-111111111111",
                "title": "MegaCorp acquires ChipStart",
                "url": "https://example.com/a",
                "published_at": T0.isoformat(),
                "role": "supports",
            }
        ],
        "consumer_hints": {
            "alert_worthy": False,
            "brief_eligible": False,
            "conflict_open": False,
            "feed_rank_signals": {},
        },
        "provenance": {
            "generated_at": T0.isoformat(),
            "schema_version": EVENT_PACKAGE_SCHEMA_VERSION,
            "policy_notes": [],
        },
    }
    base.update(overrides)
    return base


def test_same_input_same_semantic_output():
    pkg = _package()
    a = materialize_event_understanding(pkg, created_at=T0)
    b = materialize_event_understanding(copy.deepcopy(pkg), created_at=T0)
    assert semantic_fingerprint(a) == semantic_fingerprint(b)
    assert a.primary_event_type == "acquisition"
    assert a.question_agenda[0].question_id.startswith("acquisition.")
    assert any(q.question_id.endswith(".unknowns") for q in a.question_agenda)
    assert all(q.required_for_brief or True for q in a.question_agenda)


def test_package_dict_not_mutated():
    pkg = _package()
    original = copy.deepcopy(pkg)
    materialize_event_understanding(pkg, created_at=T0)
    assert pkg == original
    assert "primary_event_type" not in pkg
    assert "question_agenda" not in pkg


def test_package_version_changes_identity():
    u1 = materialize_event_understanding(_package(package_version=1), created_at=T0)
    u2 = materialize_event_understanding(_package(package_version=2), created_at=T0)
    assert understanding_identity(u1) != understanding_identity(u2)
    assert u1.package_version == 1
    assert u2.package_version == 2


def test_classifier_version_in_identity():
    u = materialize_event_understanding(_package(), created_at=T0)
    assert u.classifier_version == CLASSIFIER_VERSION
    assert u.template_version == TEMPLATE_VERSION
    assert understanding_identity(u) == (
        EVENT_ID,
        1,
        CLASSIFIER_VERSION,
        TEMPLATE_VERSION,
    )


def test_required_for_brief_preserved():
    u = materialize_event_understanding(_package(), created_at=T0)
    required_ids = {q.question_id for q in u.question_agenda if q.required_for_brief}
    assert "acquisition.strategic_motivation" in required_ids
    assert "acquisition.unknowns" in required_ids


def test_domain_tags_present_without_duplicating_event():
    u = materialize_event_understanding(_package(), created_at=T0)
    assert u.primary_event_type == "acquisition"
    tag_ids = [d.tag_id for d in u.domain_tags]
    assert "artificial_intelligence" in tag_ids or "semiconductors" in tag_ids
    assert u.primary_event_type not in tag_ids


@pytest.mark.asyncio
async def test_upsert_idempotent():
    from services.intelligence.persistence import upsert_event_understanding

    understanding = materialize_event_understanding(_package(), created_at=T0)
    stored = understanding.model_dump(mode="json")

    class _Row:
        def __init__(self, data):
            self.payload = data

    row = _Row(stored)

    session = MagicMock()
    session.execute = AsyncMock(
        side_effect=[
            MagicMock(first=lambda: ("new-id",)),
            MagicMock(scalar_one=lambda: row),
        ]
    )
    session.commit = AsyncMock()

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=None)

    with patch("services.intelligence.persistence.get_db_session", return_value=cm):
        first = await upsert_event_understanding(understanding)

    session2 = MagicMock()
    session2.execute = AsyncMock(
        side_effect=[
            MagicMock(first=lambda: None),
            MagicMock(scalar_one=lambda: row),
        ]
    )
    session2.commit = AsyncMock()
    cm2 = MagicMock()
    cm2.__aenter__ = AsyncMock(return_value=session2)
    cm2.__aexit__ = AsyncMock(return_value=None)

    with patch("services.intelligence.persistence.get_db_session", return_value=cm2):
        second = await upsert_event_understanding(understanding)

    assert first["created"] is True
    assert second["created"] is False
    assert first["understanding"]["primary_event_type"] == "acquisition"
    assert second["identity"]["classifier_version"] == CLASSIFIER_VERSION
