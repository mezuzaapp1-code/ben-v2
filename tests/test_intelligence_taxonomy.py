"""Intelligence taxonomy invariants."""
from __future__ import annotations

import pytest

from services.intelligence.taxonomy import (
    DOMAIN_TAGS,
    PRIMARY_EVENT_TYPES,
    assert_not_domain_as_event_type,
    is_domain_tag,
    is_primary_event_type,
)


def test_unknown_always_available():
    assert "unknown" in PRIMARY_EVENT_TYPES
    assert is_primary_event_type("unknown")


def test_valid_primary_types_are_closed_set():
    assert "acquisition" in PRIMARY_EVENT_TYPES
    assert "funding_round" in PRIMARY_EVENT_TYPES
    assert "research_publication" in PRIMARY_EVENT_TYPES
    assert len(PRIMARY_EVENT_TYPES) == len(set(PRIMARY_EVENT_TYPES))


def test_domains_cannot_be_emitted_as_event_types():
    for tag in DOMAIN_TAGS:
        assert is_domain_tag(tag)
        assert not is_primary_event_type(tag)
        with pytest.raises(ValueError):
            assert_not_domain_as_event_type(tag)


def test_domain_and_event_sets_disjoint():
    assert set(PRIMARY_EVENT_TYPES).isdisjoint(set(DOMAIN_TAGS))
