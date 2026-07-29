"""Versioned Question Agenda templates — questions only, no answers."""
from __future__ import annotations

from typing import Any, Final

from services.intelligence.taxonomy import PRIMARY_EVENT_TYPES, TEMPLATE_VERSION, assert_not_domain_as_event_type

QuestionSpec = dict[str, Any]


def _q(
    question_id: str,
    *,
    section: str,
    prompt: str,
    priority: int,
    required_for_brief: bool = True,
) -> QuestionSpec:
    return {
        "question_id": question_id,
        "section": section,
        "prompt": prompt,
        "required_for_brief": required_for_brief,
        "priority": priority,
        "template_version": TEMPLATE_VERSION,
    }


def _unknowns(prefix: str, *, priority: int = 900) -> QuestionSpec:
    return _q(
        f"{prefix}.unknowns",
        section="unknowns",
        prompt="What remains unknown or unverified?",
        priority=priority,
        required_for_brief=True,
    )


_TEMPLATES: Final[dict[str, tuple[QuestionSpec, ...]]] = {
    "acquisition": (
        _q("acquisition.why_now", section="timing", prompt="Why now?", priority=10),
        _q(
            "acquisition.strategic_motivation",
            section="strategy",
            prompt="What is the likely strategic motivation?",
            priority=20,
        ),
        _q(
            "acquisition.what_acquired",
            section="assets",
            prompt="What company, product, or capability was acquired?",
            priority=30,
        ),
        _q(
            "acquisition.terms_signal",
            section="terms",
            prompt="What price or terms signals are available?",
            priority=40,
            required_for_brief=False,
        ),
        _q(
            "acquisition.competitive_impact",
            section="competition",
            prompt="What is the competitive impact?",
            priority=50,
        ),
        _q("acquisition.winners", section="actors", prompt="Who benefits?", priority=60),
        _q("acquisition.losers", section="actors", prompt="Who loses or is pressured?", priority=70),
        _q(
            "acquisition.integration_risks",
            section="risks",
            prompt="What integration or execution risks matter?",
            priority=80,
        ),
        _unknowns("acquisition"),
    ),
    "funding_round": (
        _q("funding_round.size_stage", section="terms", prompt="What is the round size and stage?", priority=10),
        _q("funding_round.investors", section="actors", prompt="Who led or participated?", priority=20),
        _q("funding_round.runway_signal", section="strategy", prompt="What runway or valuation signal is implied?", priority=30),
        _q("funding_round.competitive_heat", section="competition", prompt="What competitive heat does this signal?", priority=40),
        _q("funding_round.use_of_proceeds", section="strategy", prompt="What use-of-proceeds signal is available?", priority=50, required_for_brief=False),
        _q("funding_round.dependency_risks", section="risks", prompt="What dependency or dilution risks matter?", priority=60),
        _unknowns("funding_round"),
    ),
    "regulation": (
        _q("regulation.who_affected", section="scope", prompt="Who is affected?", priority=10),
        _q("regulation.scope", section="scope", prompt="What is the regulatory scope?", priority=20),
        _q("regulation.timeline", section="timing", prompt="What is the timeline?", priority=30),
        _q("regulation.compliance", section="compliance", prompt="What compliance burden is created?", priority=40),
        _q("regulation.enforcement", section="enforcement", prompt="How will this be enforced?", priority=50),
        _q("regulation.market_impact", section="market", prompt="What is the market impact?", priority=60),
        _unknowns("regulation"),
    ),
    "government_policy": (
        _q("government_policy.actors", section="actors", prompt="Which government actors are involved?", priority=10),
        _q("government_policy.intent", section="strategy", prompt="What policy intent is stated?", priority=20),
        _q("government_policy.who_affected", section="scope", prompt="Who is affected?", priority=30),
        _q("government_policy.timeline", section="timing", prompt="What is the timeline?", priority=40),
        _q("government_policy.leverage", section="strategy", prompt="What leverage or constraints are created?", priority=50),
        _q("government_policy.market_impact", section="market", prompt="What is the market or sector impact?", priority=60),
        _unknowns("government_policy"),
    ),
    "ai_model_release": (
        _q("ai_model_release.capability_delta", section="capability", prompt="What capability delta is claimed?", priority=10),
        _q("ai_model_release.benchmarks", section="evidence", prompt="How does it compare on benchmarks or peers?", priority=20),
        _q("ai_model_release.access_model", section="access", prompt="What is the access or pricing model?", priority=30),
        _q("ai_model_release.safety_evals", section="safety", prompt="What safety or evaluation claims are made?", priority=40),
        _q("ai_model_release.competitive_displacement", section="competition", prompt="What competitive displacement is likely?", priority=50),
        _q("ai_model_release.adoption_friction", section="adoption", prompt="What adoption friction remains?", priority=60),
        _unknowns("ai_model_release"),
    ),
    "product_launch": (
        _q("product_launch.capability_delta", section="capability", prompt="What capability delta is shipping?", priority=10),
        _q("product_launch.target_buyer", section="market", prompt="Who is the target buyer?", priority=20),
        _q("product_launch.displacement", section="competition", prompt="What does it displace?", priority=30),
        _q("product_launch.pricing_access", section="access", prompt="What pricing or access model applies?", priority=40, required_for_brief=False),
        _q("product_launch.trust_claims", section="trust", prompt="What trust or reliability claims are made?", priority=50),
        _q("product_launch.adoption_friction", section="adoption", prompt="What adoption friction remains?", priority=60),
        _unknowns("product_launch"),
    ),
    "partnership": (
        _q("partnership.parties", section="actors", prompt="Who are the partners?", priority=10),
        _q("partnership.scope", section="scope", prompt="What is the partnership scope?", priority=20),
        _q("partnership.strategic_motivation", section="strategy", prompt="What is the strategic motivation?", priority=30),
        _q("partnership.exclusive_terms", section="terms", prompt="Are there exclusive or binding terms?", priority=40, required_for_brief=False),
        _q("partnership.competitive_impact", section="competition", prompt="What is the competitive impact?", priority=50),
        _q("partnership.execution_risks", section="risks", prompt="What execution risks matter?", priority=60),
        _unknowns("partnership"),
    ),
    "research_publication": (
        _q("research_publication.what_is_new", section="novelty", prompt="What is new?", priority=10),
        _q("research_publication.vs_prior_work", section="novelty", prompt="How does it compare to previous work?", priority=20),
        _q("research_publication.evidence_strength", section="evidence", prompt="How strong is the evidence?", priority=30),
        _q("research_publication.practical_impact", section="impact", prompt="What is the practical impact?", priority=40),
        _q("research_publication.production_readiness", section="readiness", prompt="How production-ready is it?", priority=50),
        _q("research_publication.limitations", section="limits", prompt="What limitations are acknowledged?", priority=60),
        _unknowns("research_publication"),
    ),
    "security_incident": (
        _q("security_incident.what_exposed", section="impact", prompt="What was exposed or compromised?", priority=10),
        _q("security_incident.blast_radius", section="impact", prompt="What is the blast radius?", priority=20),
        _q("security_incident.attribution", section="evidence", prompt="How confident is attribution?", priority=30),
        _q("security_incident.remediation", section="response", prompt="What remediation is underway?", priority=40),
        _q("security_incident.residual_risk", section="risks", prompt="What residual risk remains?", priority=50),
        _q("security_incident.who_affected", section="actors", prompt="Who is affected?", priority=60),
        _unknowns("security_incident"),
    ),
    "open_source_release": (
        _q("open_source_release.what_released", section="assets", prompt="What was released?", priority=10),
        _q("open_source_release.license", section="terms", prompt="What license or usage terms apply?", priority=20),
        _q("open_source_release.capability_delta", section="capability", prompt="What capability delta does it create?", priority=30),
        _q("open_source_release.ecosystem_impact", section="ecosystem", prompt="What ecosystem impact is expected?", priority=40),
        _q("open_source_release.competitive_impact", section="competition", prompt="What is the competitive impact?", priority=50),
        _unknowns("open_source_release"),
    ),
    "earnings": (
        _q("earnings.results_signal", section="results", prompt="What results or guidance were reported?", priority=10),
        _q("earnings.vs_expectations", section="results", prompt="How do results compare to expectations?", priority=20),
        _q("earnings.strategic_signal", section="strategy", prompt="What strategic signal is management sending?", priority=30),
        _q("earnings.segment_drivers", section="drivers", prompt="Which segments or products drove the outcome?", priority=40),
        _q("earnings.market_reaction", section="market", prompt="What market reaction is notable?", priority=50, required_for_brief=False),
        _unknowns("earnings"),
    ),
    "leadership_change": (
        _q("leadership_change.who", section="actors", prompt="Who is entering or leaving?", priority=10),
        _q("leadership_change.role", section="actors", prompt="What role changed?", priority=20),
        _q("leadership_change.why_now", section="timing", prompt="Why now?", priority=30),
        _q("leadership_change.strategic_implication", section="strategy", prompt="What strategic implication follows?", priority=40),
        _q("leadership_change.continuity_risk", section="risks", prompt="What continuity or execution risk is created?", priority=50),
        _unknowns("leadership_change"),
    ),
    "infrastructure_expansion": (
        _q("infrastructure_expansion.what_built", section="assets", prompt="What infrastructure is being expanded?", priority=10),
        _q("infrastructure_expansion.location_scale", section="scope", prompt="Where and at what scale?", priority=20),
        _q("infrastructure_expansion.capex_signal", section="terms", prompt="What capex or capacity signal is available?", priority=30, required_for_brief=False),
        _q("infrastructure_expansion.strategic_motivation", section="strategy", prompt="What is the strategic motivation?", priority=40),
        _q("infrastructure_expansion.competitive_impact", section="competition", prompt="What is the competitive impact?", priority=50),
        _q("infrastructure_expansion.constraints", section="risks", prompt="What power, supply, or regulatory constraints matter?", priority=60),
        _unknowns("infrastructure_expansion"),
    ),
    "unknown": (
        _q("unknown.what_happened", section="facts", prompt="What happened?", priority=10),
        _q("unknown.actors", section="actors", prompt="Who are the actors?", priority=20),
        _q("unknown.what_changed", section="impact", prompt="What changed?", priority=30),
        _q("unknown.why_it_matters", section="impact", prompt="Why does it matter?", priority=40),
        _q("unknown.contested_claims", section="evidence", prompt="Which claims are contested?", priority=50, required_for_brief=False),
        _unknowns("unknown"),
    ),
}


def _validate_registry() -> None:
    missing = [t for t in PRIMARY_EVENT_TYPES if t not in _TEMPLATES]
    if missing:
        raise RuntimeError(f"missing question templates for: {missing}")
    for event_type, questions in _TEMPLATES.items():
        assert_not_domain_as_event_type(event_type)
        ids = [q["question_id"] for q in questions]
        if len(ids) != len(set(ids)):
            raise RuntimeError(f"duplicate question_id in {event_type}")
        if not any(q["question_id"].endswith(".unknowns") for q in questions):
            raise RuntimeError(f"template {event_type} missing unknowns question")
        if not any(q["required_for_brief"] for q in questions):
            raise RuntimeError(f"template {event_type} has no required_for_brief questions")
        ordered = sorted(questions, key=lambda q: (int(q["priority"]), q["question_id"]))
        if [q["question_id"] for q in ordered] != ids:
            # Allow declaration order to equal priority order; enforce sorted equality for safety
            pass
        priorities = [int(q["priority"]) for q in questions]
        if priorities != sorted(priorities):
            raise RuntimeError(f"template {event_type} priorities must be ascending")


_validate_registry()


def get_question_template(event_type: str) -> list[QuestionSpec]:
    assert_not_domain_as_event_type(event_type)
    specs = _TEMPLATES.get(event_type) or _TEMPLATES["unknown"]
    return [dict(q) for q in specs]


def list_template_event_types() -> list[str]:
    return list(PRIMARY_EVENT_TYPES)


def template_version() -> str:
    return TEMPLATE_VERSION
