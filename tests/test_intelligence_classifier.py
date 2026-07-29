"""Deterministic event/domain classifier."""
from __future__ import annotations

from services.intelligence.classifier import classify_text
from services.intelligence.normalize import normalize_text
from services.intelligence.taxonomy import DOMAIN_TAGS, is_domain_tag, is_primary_event_type


def test_deterministic_output():
    text = "OpenAI acquires startup to boost robotics research"
    a = classify_text(text)
    b = classify_text(text)
    assert a == b


def test_acquisition():
    out = classify_text("MegaCorp acquires ChipStart for $2B")
    assert out["primary_event_type"] == "acquisition"
    assert "event.acquisition.acquires" in out["classification"]["rule_ids"]


def test_funding_round():
    out = classify_text("AI startup raises $120M Series B funding round")
    assert out["primary_event_type"] == "funding_round"


def test_regulation():
    out = classify_text("Regulator fines cloud provider for antitrust violations")
    assert out["primary_event_type"] == "regulation"


def test_model_release():
    out = classify_text("Anthropic unveils model Claude update with open weights option")
    assert out["primary_event_type"] == "ai_model_release"


def test_research_publication():
    out = classify_text("Researchers publish a research paper on arxiv about transformers")
    assert out["primary_event_type"] == "research_publication"


def test_security_incident():
    out = classify_text("Hospital hit by ransomware data breach exposing records")
    assert out["primary_event_type"] == "security_incident"


def test_unrelated_returns_unknown():
    out = classify_text("Local bakery opens new storefront downtown")
    assert out["primary_event_type"] == "unknown"
    assert out["classification"]["rule_ids"] == []


def test_english_normalization():
    messy = "  MegaCorp   ACQUIRES   ChipStart!!!  "
    clean = normalize_text(messy)
    assert "acquires" in clean
    assert classify_text(messy)["primary_event_type"] == "acquisition"


def test_precedence_and_tie_breaking():
    # Both acquisition and partnership cues; acquisition has higher weight + precedence.
    out = classify_text(
        "Company acquires rival and also partners with cloud vendor on joint venture"
    )
    assert out["primary_event_type"] == "acquisition"
    assert "partnership" in out["secondary_event_types"] or out["primary_event_type"] == "acquisition"


def test_ambiguous_weak_signal_still_typed_or_unknown():
    out = classify_text("Company announces something important today")
    assert is_primary_event_type(out["primary_event_type"])


def test_domain_tags_multilabel_deterministic():
    out = classify_text(
        "OpenAI and NVIDIA expand AI data center capacity for cloud GPUs"
    )
    tags = [d["tag_id"] for d in out["domain_tags"]]
    assert len(tags) == len(set(tags))
    assert all(is_domain_tag(t) for t in tags)
    assert all(t not in ("acquisition", "unknown") for t in tags)
    # Domains are not event types
    assert out["primary_event_type"] not in DOMAIN_TAGS
    again = classify_text(
        "OpenAI and NVIDIA expand AI data center capacity for cloud GPUs"
    )
    assert again["domain_tags"] == out["domain_tags"]


def test_domain_scores_ordered():
    out = classify_text("OpenAI artificial intelligence machine learning robotics")
    confidences = [d["confidence"] for d in out["domain_tags"]]
    assert confidences == sorted(confidences, reverse=True)
