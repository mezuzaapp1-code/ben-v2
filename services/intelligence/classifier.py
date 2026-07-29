"""Deterministic lexicon ClassificationRuleSet for Event Understanding."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from services.intelligence.normalize import normalize_text, package_classification_text
from services.intelligence.taxonomy import (
    CLASSIFIER_VERSION,
    DOMAIN_TAGS,
    PRIMARY_EVENT_TYPES,
    assert_not_domain_as_event_type,
    is_domain_tag,
    is_primary_event_type,
    precedence_index,
)

RuleKind = Literal["event", "domain"]

# Minimum score to emit a secondary event type or domain tag.
SECONDARY_EVENT_MIN_SCORE = 35
SECONDARY_EVENT_RATIO = 0.55
DOMAIN_MIN_SCORE = 20
MAX_SECONDARY_EVENT_TYPES = 2


@dataclass(frozen=True)
class ClassificationRule:
    rule_id: str
    kind: RuleKind
    target_id: str
    patterns: tuple[str, ...]
    weight: int


def _event(rule_id: str, target: str, weight: int, *patterns: str) -> ClassificationRule:
    assert_not_domain_as_event_type(target)
    return ClassificationRule(
        rule_id=rule_id,
        kind="event",
        target_id=target,
        patterns=tuple(normalize_text(p) for p in patterns),
        weight=weight,
    )


def _domain(rule_id: str, target: str, weight: int, *patterns: str) -> ClassificationRule:
    if not is_domain_tag(target):
        raise ValueError(f"invalid domain tag: {target}")
    return ClassificationRule(
        rule_id=rule_id,
        kind="domain",
        target_id=target,
        patterns=tuple(normalize_text(p) for p in patterns),
        weight=weight,
    )


RULESET: tuple[ClassificationRule, ...] = (
    # --- acquisition ---
    _event("event.acquisition.acquires", "acquisition", 80, "acquires", "acquired", "to acquire"),
    _event("event.acquisition.acquisition_of", "acquisition", 75, "acquisition of", "in an acquisition"),
    _event("event.acquisition.buyout", "acquisition", 70, "buyout", "takeover", "merger agreement"),
    _event("event.acquisition.buys", "acquisition", 55, " buys ", " agreed to buy "),
    # --- funding ---
    _event("event.funding.raises", "funding_round", 80, "raises funding", "raised funding", "raises  ", "raised  "),
    _event("event.funding.million", "funding_round", 70, "million series", "billion series", "m series", "b round"),
    _event("event.funding.series", "funding_round", 75, "series a", "series b", "series c", "seed round"),
    _event("event.funding.venture", "funding_round", 60, "venture round", "funding round", "led the round"),
    # --- earnings ---
    _event("event.earnings.quarterly", "earnings", 75, "quarterly earnings", "q1 earnings", "q2 earnings", "q3 earnings", "q4 earnings"),
    _event("event.earnings.results", "earnings", 65, "reports earnings", "earnings beat", "earnings miss", "fiscal results"),
    _event("event.earnings.guidance", "earnings", 55, "raises guidance", "cuts guidance", "revenue guidance"),
    # --- security ---
    _event("event.security.breach", "security_incident", 80, "data breach", "security breach", "ransomware"),
    _event("event.security.hack", "security_incident", 70, "hacked", "cyberattack", "cyber attack", "zero-day exploit"),
    _event("event.security.leak", "security_incident", 55, "credential leak", "exposed customer data"),
    # --- regulation ---
    _event("event.regulation.regulator", "regulation", 70, "regulator", "regulatory", "antitrust"),
    _event("event.regulation.fine", "regulation", 75, "fined", "imposes fine", "civil penalty"),
    _event("event.regulation.ban", "regulation", 65, "bans ", "banned ", "compliance deadline"),
    _event("event.regulation.sec", "regulation", 60, "sec charges", "ftc sues", "doj lawsuit"),
    # --- government policy ---
    _event("event.policy.executive", "government_policy", 70, "executive order", "white house", "administration announces"),
    _event("event.policy.legislation", "government_policy", 65, "congress passes", "new law", "national strategy"),
    _event("event.policy.export", "government_policy", 60, "export controls", "sanctions package"),
    # --- AI model release ---
    _event("event.model.releases_model", "ai_model_release", 80, "releases model", "model release", "unveils model"),
    _event("event.model.llm", "ai_model_release", 70, "large language model", "foundation model", "open weights"),
    _event("event.model.gpt_claude", "ai_model_release", 65, "gpt-", "claude ", "gemini ", "llama "),
    # --- open source ---
    _event("event.oss.release", "open_source_release", 70, "open sources", "open-sourced", "open source release"),
    _event("event.oss.apache", "open_source_release", 55, "apache license", "mit license", "github repository released"),
    # --- research ---
    _event("event.research.paper", "research_publication", 75, "research paper", "preprint", "arxiv"),
    _event("event.research.study", "research_publication", 60, "peer-reviewed", "published a study", "scientific study"),
    # --- partnership ---
    _event("event.partnership.partners", "partnership", 70, "partners with", "strategic partnership", "joint venture"),
    _event("event.partnership.alliance", "partnership", 60, "forms alliance", "collaboration agreement"),
    # --- product launch ---
    _event("event.product.launches", "product_launch", 70, "launches ", "unveils ", "announces launch"),
    _event("event.product.ga", "product_launch", 55, "generally available", "now available", "product debut"),
    # --- leadership ---
    _event("event.leadership.ceo", "leadership_change", 75, "new ceo", "appoints ceo", "steps down as ceo", "named ceo"),
    _event("event.leadership.exec", "leadership_change", 60, "chief executive", "executive shakeup", "resigns as"),
    # --- infrastructure ---
    _event("event.infra.datacenter", "infrastructure_expansion", 70, "data center", "datacenter", "hyperscale campus"),
    _event("event.infra.capacity", "infrastructure_expansion", 60, "expands capacity", "new fab", "chip plant", "gigawatt"),
    # --- domains ---
    _domain("domain.ai.general", "artificial_intelligence", 40, "artificial intelligence", " ai ", "machine learning", "generative ai"),
    _domain("domain.ai.openai", "artificial_intelligence", 50, "openai", "anthropic", "deepmind"),
    _domain("domain.robotics", "robotics", 45, "robotics", "humanoid robot", "autonomous robot"),
    _domain("domain.semiconductors", "semiconductors", 45, "semiconductor", "chipmaker", "nvidia", "tsmc", "gpu"),
    _domain("domain.cloud", "cloud", 40, "cloud computing", "aws", "azure", "google cloud"),
    _domain("domain.data_centers", "data_centers", 45, "data center", "datacenter", "colocation"),
    _domain("domain.security", "security", 40, "cybersecurity", "infosec", "vulnerability"),
    _domain("domain.open_source", "open_source", 40, "open source", "open-source", "github"),
    _domain("domain.startups", "startups", 35, "startup", "venture-backed", "seed-stage"),
    _domain("domain.science", "science", 35, "scientific", "researchers", "laboratory"),
    _domain("domain.business", "business", 25, "company", "corporation", "enterprise"),
)


def score_to_confidence(score: float) -> float:
    """Documented heuristic bands — not scientific probability."""
    if score >= 90:
        return 0.95
    if score >= 70:
        return 0.88
    if score >= 50:
        return 0.78
    if score >= 35:
        return 0.65
    if score >= 20:
        return 0.50
    if score > 0:
        return 0.35
    return 0.15


def _match_rules(text: str) -> list[tuple[ClassificationRule, str]]:
    hits: list[tuple[ClassificationRule, str]] = []
    for rule in RULESET:
        for pattern in rule.patterns:
            if pattern and pattern in text:
                hits.append((rule, pattern))
                break
    return hits


def classify_text(text: str) -> dict[str, Any]:
    """Classify normalized (or raw) text; always deterministic for fixed RULESET."""
    normalized = normalize_text(text)
    hits = _match_rules(normalized)

    event_scores: dict[str, int] = {t: 0 for t in PRIMARY_EVENT_TYPES if t != "unknown"}
    event_rules: dict[str, list[str]] = {t: [] for t in event_scores}
    domain_scores: dict[str, int] = {t: 0 for t in DOMAIN_TAGS}
    domain_rules: dict[str, list[str]] = {t: [] for t in DOMAIN_TAGS}

    for rule, _pattern in hits:
        if rule.kind == "event":
            event_scores[rule.target_id] = event_scores.get(rule.target_id, 0) + rule.weight
            event_rules.setdefault(rule.target_id, []).append(rule.rule_id)
        else:
            domain_scores[rule.target_id] = domain_scores.get(rule.target_id, 0) + rule.weight
            domain_rules.setdefault(rule.target_id, []).append(rule.rule_id)

    ranked_events = sorted(
        ((score, precedence_index(etype), etype) for etype, score in event_scores.items() if score > 0),
        key=lambda row: (-row[0], row[1], row[2]),
    )

    if not ranked_events:
        primary = "unknown"
        primary_score = 0
        primary_rule_ids: list[str] = []
        secondary: list[str] = []
    else:
        primary_score, _, primary = ranked_events[0]
        primary_rule_ids = sorted(set(event_rules.get(primary, [])))
        secondary = []
        for score, _prec, etype in ranked_events[1:]:
            if len(secondary) >= MAX_SECONDARY_EVENT_TYPES:
                break
            if score < SECONDARY_EVENT_MIN_SCORE:
                continue
            if score < primary_score * SECONDARY_EVENT_RATIO:
                continue
            secondary.append(etype)

    domain_tags = []
    for tag_id, score in sorted(
        domain_scores.items(),
        key=lambda item: (-item[1], item[0]),
    ):
        if score < DOMAIN_MIN_SCORE:
            continue
        domain_tags.append(
            {
                "tag_id": tag_id,
                "confidence": score_to_confidence(float(score)),
                "rule_ids": sorted(set(domain_rules.get(tag_id, []))),
                "score": score,
            }
        )

    # Domains must never be emitted as event types
    assert is_primary_event_type(primary)
    for sec in secondary:
        assert is_primary_event_type(sec)
        assert not is_domain_tag(sec)

    return {
        "primary_event_type": primary,
        "secondary_event_types": secondary,
        "domain_tags": [
            {
                "tag_id": d["tag_id"],
                "confidence": d["confidence"],
                "rule_ids": d["rule_ids"],
            }
            for d in domain_tags
        ],
        "classification": {
            "confidence": score_to_confidence(float(primary_score)),
            "rule_ids": primary_rule_ids,
            "classifier_version": CLASSIFIER_VERSION,
            "score": primary_score,
        },
        "classifier_version": CLASSIFIER_VERSION,
    }


def classify_event_package(package: dict[str, Any] | Any) -> dict[str, Any]:
    return classify_text(package_classification_text(package))
