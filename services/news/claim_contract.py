"""E1 NewsClaim contract — atomic, provenance-bound claims (not Events).

Durable classification uses independent axes:
  - epistemic_type (exclusive)
  - semantic_domains (multi-label)
  - source_strength (provenance class, not confidence)

``claim_type`` / stored ``role`` are removed from the system of record.
``derived_role`` is a compatibility helper only (factual vs interpretive).
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

EXTRACTOR_VERSION = "e1.2"
HEURISTIC_PROVIDER = "heuristic"
HEURISTIC_MODEL = "rules-e1.2"

EpistemicType = Literal[
    "fact",
    "attributed_statement",
    "allegation",
    "prediction",
    "opinion",
    "correction",
]
SemanticDomain = Literal[
    "company",
    "financial",
    "market",
    "product",
    "technology",
    "regulation",
    "legal",
    "security",
    "supply_chain",
    "other",
]
SourceStrength = Literal[
    "official",
    "wire",
    "major_media",
    "industry_media",
    "blog",
    "social",
    "unknown",
]
SourceField = Literal["title", "summary"]
ClaimStatus = Literal["extracted", "failed"]
ExtractionStatus = Literal["pending", "succeeded", "failed", "skipped"]
DerivedRole = Literal["factual", "interpretive"]

EPISTEMIC_TYPES: frozenset[str] = frozenset(
    {
        "fact",
        "attributed_statement",
        "allegation",
        "prediction",
        "opinion",
        "correction",
    }
)
SEMANTIC_DOMAINS: frozenset[str] = frozenset(
    {
        "company",
        "financial",
        "market",
        "product",
        "technology",
        "regulation",
        "legal",
        "security",
        "supply_chain",
        "other",
    }
)
SOURCE_STRENGTHS: frozenset[str] = frozenset(
    {
        "official",
        "wire",
        "major_media",
        "industry_media",
        "blog",
        "social",
        "unknown",
    }
)

# Epistemic types that must never be treated as corroborated / current facts.
INTERPRETIVE_EPISTEMIC: frozenset[str] = frozenset({"prediction", "opinion"})
ATTRIBUTION_REQUIRED: frozenset[str] = frozenset({"attributed_statement", "allegation"})
# Event Builder fact-candidate gate (epistemic SoR — not derived_role).
FACT_CANDIDATE_EPISTEMIC: frozenset[str] = frozenset({"fact", "correction"})
# Never auto-promote to corroborated current fact without independent evidence path.
NEVER_AUTO_CORROBORATE: frozenset[str] = frozenset(
    {"allegation", "attributed_statement", "prediction", "opinion"}
)

_WS = re.compile(r"\s+")


def normalize_claim_text(text: str) -> str:
    """Whitespace-normalize only — never strip uncertainty/attribution language."""
    return _WS.sub(" ", text.strip())


def derived_role(epistemic_type: str) -> DerivedRole:
    """API convenience only — coarse factual vs interpretive shape.

    MUST NOT be used alone for Event Builder corroboration. Allegations and
    attributed statements may project as ``factual`` here while remaining
    non-corroborated at the epistemic layer.
    """
    if epistemic_type in INTERPRETIVE_EPISTEMIC:
        return "interpretive"
    return "factual"


def is_fact_candidate(epistemic_type: str) -> bool:
    """True for epistemic types allowed as Event fact candidates."""
    return epistemic_type in FACT_CANDIDATE_EPISTEMIC


def can_auto_corroborate(epistemic_type: str) -> bool:
    """True only when epistemic_type alone may become a corroborated current fact.

    Allegations and attributed statements require independent evidence; prediction
    and opinion never become current facts.
    """
    return epistemic_type == "fact"


def is_blocked_as_current_fact(epistemic_type: str) -> bool:
    """Prediction/opinion are never current facts; allegation is not without evidence."""
    return epistemic_type in INTERPRETIVE_EPISTEMIC or epistemic_type == "allegation"


def content_fingerprint(*, title: str, summary: str | None) -> str:
    raw = f"{title or ''}\n{summary or ''}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def claim_fingerprint(
    *,
    text: str,
    epistemic_type: str,
    semantic_domains: list[str],
    source_field: str,
    source_start: int | None,
    source_end: int | None,
) -> str:
    domains = ",".join(sorted(semantic_domains))
    key = (
        f"{normalize_claim_text(text).lower()}|{epistemic_type}|{domains}|"
        f"{source_field}|{source_start}|{source_end}"
    )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


class ExtractedClaim(BaseModel):
    """Validated claim candidate before persistence."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(..., min_length=1, max_length=2000)
    epistemic_type: EpistemicType
    semantic_domains: list[SemanticDomain] = Field(..., min_length=1)
    source_strength: SourceStrength = "unknown"
    source_field: SourceField
    source_excerpt: str = Field(..., min_length=1, max_length=4000)
    source_start: int | None = Field(None, ge=0)
    source_end: int | None = Field(None, ge=0)
    attribution: str | None = Field(None, max_length=1000)
    uncertainty: str | None = Field(None, max_length=1000)
    corrects_ref: str | None = Field(
        None,
        max_length=2000,
        description="What a correction revises (claim id, excerpt, or description).",
    )

    @field_validator("text", "source_excerpt", "attribution", "uncertainty", "corrects_ref", mode="before")
    @classmethod
    def _normalize_strings(cls, v: Any) -> Any:
        if isinstance(v, str):
            return normalize_claim_text(v) if v.strip() else v
        return v

    @field_validator("semantic_domains", mode="before")
    @classmethod
    def _dedupe_domains(cls, v: Any) -> Any:
        if not isinstance(v, list):
            return v
        seen: list[str] = []
        for item in v:
            if item not in seen:
                seen.append(item)
        return seen

    @model_validator(mode="after")
    def _enforce_epistemic_safety(self) -> ExtractedClaim:
        if self.source_start is not None and self.source_end is not None:
            if self.source_end < self.source_start:
                raise ValueError("source_end must be >= source_start")
        if not self.source_excerpt.strip():
            raise ValueError("source_excerpt is required")
        if not self.semantic_domains:
            raise ValueError("semantic_domains must be non-empty")
        for d in self.semantic_domains:
            if d not in SEMANTIC_DOMAINS:
                raise ValueError(f"invalid semantic domain: {d}")

        if self.epistemic_type in ATTRIBUTION_REQUIRED and not (self.attribution and self.attribution.strip()):
            raise ValueError(f"{self.epistemic_type} requires attribution")

        if self.epistemic_type == "correction" and not (self.corrects_ref and self.corrects_ref.strip()):
            raise ValueError("correction requires corrects_ref")

        # Opinion/prediction are interpretive — reject attempts to smuggle them as facts
        # via missing hedging is allowed, but role projection is always interpretive.
        if self.epistemic_type in INTERPRETIVE_EPISTEMIC:
            if derived_role(self.epistemic_type) != "interpretive":
                raise ValueError("opinion/prediction must be interpretive")

        return self

    @property
    def role(self) -> DerivedRole:
        """Derived compatibility projection — not stored."""
        return derived_role(self.epistemic_type)


class ExtractionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claims: list[ExtractedClaim]
    provider: str
    model: str
    extractor_version: str = EXTRACTOR_VERSION


def parse_extracted_claims(payload: Any) -> list[ExtractedClaim]:
    """Parse model/heuristic JSON into validated claims. Raises ValidationError."""
    if isinstance(payload, dict) and "claims" in payload:
        items = payload["claims"]
    else:
        items = payload
    if not isinstance(items, list):
        raise ValueError("claims payload must be a list or {claims: [...]}")
    return [ExtractedClaim.model_validate(item) for item in items]
