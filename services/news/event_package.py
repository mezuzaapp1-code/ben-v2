"""EventPackage v1 — stable consumer contract for BEN News.

Product consumers (Feed, Ask BEN, Alerts, Daily Brief) MUST read EventPackages only.
They MUST NOT query NewsArticle / raw acquisition rows as their primary data source.

Article cards may appear *inside* a package (``articles[]``) as provenance.
Operator article-registry APIs remain separate and are not product consumers.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

EVENT_PACKAGE_SCHEMA_VERSION = 1

EventLifecycle = Literal[
    "open",
    "developing",
    "contested",
    "stable",
    "corrected",
    "closed",
]

ClaimStatus = Literal["corroborated", "attributed", "superseded"]
ClaimConfidence = Literal["low", "medium", "high", "contested"]
EvidenceRole = Literal["supports", "updates", "contradicts", "corrects"]
ConflictResolution = Literal[
    "unresolved",
    "resolved_by_correction",
    "resolved_by_authority",
]
SourceTier = Literal["A", "B", "C", "D"]


class EvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    article_id: str
    source_id: str | None = None


class PackageFact(BaseModel):
    """Occurrence / metric fact. Traceable. Never unsettled conflict as settled."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str
    text: str = Field(..., min_length=1, max_length=2000)
    status: ClaimStatus
    confidence: ClaimConfidence
    evidence_refs: list[EvidenceRef] = Field(..., min_length=1)


class PackageImpact(BaseModel):
    """Observable change (claim-backed)."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str
    text: str = Field(..., min_length=1, max_length=2000)
    status: ClaimStatus
    confidence: ClaimConfidence
    evidence_refs: list[EvidenceRef] = Field(..., min_length=1)


class WhyItMattersItem(BaseModel):
    """Explicitly interpretive. Never treated as corroborated fact."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(..., min_length=1, max_length=2000)
    kind: Literal["interpretive"] = "interpretive"
    basis_claim_ids: list[str] = Field(default_factory=list)
    article_ids: list[str] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)

    @model_validator(mode="after")
    def _require_basis(self) -> WhyItMattersItem:
        if not self.basis_claim_ids and not self.article_ids and not self.evidence_refs:
            raise ValueError("why_it_matters item requires basis_claim_ids, article_ids, or evidence_refs")
        return self


class ConflictPosition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str
    text: str = Field(..., min_length=1, max_length=2000)
    source_ids: list[str] = Field(..., min_length=1)
    article_ids: list[str] = Field(..., min_length=1)


class PackageConflict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic: str = Field(..., min_length=1, max_length=512)
    positions: list[ConflictPosition] = Field(..., min_length=2)
    resolution: ConflictResolution


class PackageEntity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=256)
    id: str | None = None


class PackageSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    name: str = Field(..., min_length=1, max_length=256)
    tier: SourceTier
    article_ids: list[str] = Field(default_factory=list)


class PackageArticleCard(BaseModel):
    """Provenance card embedded in the package — not a substitute for raw article APIs."""

    model_config = ConfigDict(extra="forbid")

    article_id: str
    source_id: str
    title: str = Field(..., min_length=1, max_length=1024)
    url: str = Field(..., min_length=1, max_length=2048)
    published_at: datetime | str | None = None
    role: EvidenceRole


class PackageHeroImage(BaseModel):
    """Selected Event hero — metadata only; V1 hotlinks https URL (no proxy)."""

    model_config = ConfigDict(extra="forbid")

    url: str = Field(..., min_length=1, max_length=2048)
    source_article_id: str | None = None
    origin: str | None = None
    width: int | None = Field(None, ge=0)
    height: int | None = Field(None, ge=0)
    selected_at: datetime | str | None = None
    selection_reason: str | None = Field(None, max_length=512)
    selection_score: float | None = Field(None, ge=0.0, le=1.0)
    hero_confidence: float | None = Field(
        None,
        ge=0.0,
        le=1.0,
        description="Alias of selection_score for future intelligence consumers.",
    )


class ConsumerHints(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alert_worthy: bool = False
    brief_eligible: bool = False
    conflict_open: bool = False
    feed_rank_signals: dict[str, Any] = Field(default_factory=dict)


class PackageProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generated_at: datetime | str
    schema_version: int = EVENT_PACKAGE_SCHEMA_VERSION
    policy_notes: list[str] = Field(default_factory=list)


class EventPackage(BaseModel):
    """Stable v1 consumer contract."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = EVENT_PACKAGE_SCHEMA_VERSION
    event_id: str
    package_version: int = Field(..., ge=1)
    lifecycle: EventLifecycle
    headline: str = Field(..., min_length=1, max_length=1024)
    happened_at: datetime | str | None = None
    updated_at: datetime | str
    summary: str = Field(..., min_length=1, max_length=4000)
    current_facts: list[PackageFact] = Field(default_factory=list)
    impacts: list[PackageImpact] = Field(default_factory=list)
    why_it_matters: list[WhyItMattersItem] = Field(default_factory=list)
    conflicts: list[PackageConflict] = Field(default_factory=list)
    entities: list[PackageEntity] = Field(default_factory=list)
    sources: list[PackageSource] = Field(default_factory=list)
    articles: list[PackageArticleCard] = Field(default_factory=list)
    hero_image: PackageHeroImage | None = None
    consumer_hints: ConsumerHints = Field(default_factory=ConsumerHints)
    provenance: PackageProvenance

    @field_validator("schema_version")
    @classmethod
    def _schema_v1(cls, v: int) -> int:
        if v != EVENT_PACKAGE_SCHEMA_VERSION:
            raise ValueError(f"unsupported EventPackage schema_version {v}")
        return v

    @model_validator(mode="after")
    def _enforce_conflict_and_tone_rules(self) -> EventPackage:
        unresolved = [c for c in self.conflicts if c.resolution == "unresolved"]
        conflicted_claim_ids = {
            p.claim_id for c in unresolved for p in c.positions
        }
        if unresolved:
            if self.lifecycle != "contested":
                raise ValueError("unresolved conflicts require lifecycle=contested")
            if not self.consumer_hints.conflict_open:
                raise ValueError("unresolved conflicts require consumer_hints.conflict_open=true")
            # Disputed claims must not appear as settled facts/impacts
            for fact in self.current_facts:
                if fact.claim_id in conflicted_claim_ids and fact.status == "corroborated":
                    raise ValueError(
                        "conflicted claim_id must not appear in current_facts as corroborated"
                    )
            for impact in self.impacts:
                if impact.claim_id in conflicted_claim_ids and impact.status == "corroborated":
                    raise ValueError(
                        "conflicted claim_id must not appear in impacts as corroborated"
                    )
        if self.lifecycle == "contested" and not unresolved:
            raise ValueError("lifecycle=contested requires at least one unresolved conflict")
        if self.consumer_hints.brief_eligible and unresolved:
            raise ValueError("brief_eligible cannot be true while conflicts are unresolved")
        return self


def parse_event_package(data: dict[str, Any] | EventPackage) -> EventPackage:
    """Validate and return an EventPackage (raises ValidationError on contract breach)."""
    if isinstance(data, EventPackage):
        return data
    return EventPackage.model_validate(data)


def event_package_to_dict(package: EventPackage) -> dict[str, Any]:
    return package.model_dump(mode="json")
