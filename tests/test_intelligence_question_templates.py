"""Question template registry invariants."""
from __future__ import annotations

from services.intelligence.question_templates import get_question_template, list_template_event_types
from services.intelligence.taxonomy import PRIMARY_EVENT_TYPES, TEMPLATE_VERSION


def test_every_primary_type_has_template():
    assert list_template_event_types() == list(PRIMARY_EVENT_TYPES)
    for event_type in PRIMARY_EVENT_TYPES:
        agenda = get_question_template(event_type)
        assert agenda
        assert all(q["template_version"] == TEMPLATE_VERSION for q in agenda)


def test_stable_question_ids_and_ordering():
    agenda = get_question_template("acquisition")
    ids = [q["question_id"] for q in agenda]
    assert len(ids) == len(set(ids))
    priorities = [q["priority"] for q in agenda]
    assert priorities == sorted(priorities)
    assert ids == [q["question_id"] for q in sorted(agenda, key=lambda q: (q["priority"], q["question_id"]))]


def test_unknowns_always_included():
    for event_type in PRIMARY_EVENT_TYPES:
        agenda = get_question_template(event_type)
        assert any(q["question_id"].endswith(".unknowns") for q in agenda)


def test_required_for_brief_present():
    for event_type in PRIMARY_EVENT_TYPES:
        agenda = get_question_template(event_type)
        required = [q for q in agenda if q["required_for_brief"]]
        assert required, event_type
        # unknowns is required
        assert any(q["question_id"].endswith(".unknowns") and q["required_for_brief"] for q in agenda)


def test_template_copies_are_independent():
    a = get_question_template("regulation")
    b = get_question_template("regulation")
    a[0]["prompt"] = "mutated"
    assert b[0]["prompt"] != "mutated"
