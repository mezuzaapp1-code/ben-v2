"""EventUnderstanding contract and pure materializer (Phase 1a)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from services.intelligence.classifier import classify_event_package
from services.intelligence.question_templates import get_question_template
from services.intelligence.taxonomy import (
    CLASSIFIER_VERSION,
    TEMPLATE_VERSION,
    assert_not_domain_as_event_type,
    is_domain_tag,
    is_primary_event_type,
)
from services.news.event_package import EventPackage, parse_event_package


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DomainTagHit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tag_id: str
    confidence: float = Field(..., ge=0.0, le=1.0)
    rule_ids: list[str] = Field(default_factory=list)

    @field_validator("tag_id")
    @classmethod
    def _domain_only(cls, v: str) -> str:
        if not is_domain_tag(v):
            raise ValueError(f"invalid domain tag: {v}")
        return v


class ClassificationMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confidence: float = Field(..., ge=0.0, le=1.0)
    rule_ids: list[str] = Field(default_factory=list)
    classifier_version: str = CLASSIFIER_VERSION


class AgendaQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str
    section: str
    prompt: str
    required_for_brief: bool
    priority: int
    template_version: str = TEMPLATE_VERSION


class EventUnderstanding(BaseModel):
    """Intelligence-layer artifact — never written into EventPackage."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    event_id: str
    package_version: int = Field(..., ge=1)
    primary_event_type: str
    secondary_event_types: list[str] = Field(default_factory=list)
    domain_tags: list[DomainTagHit] = Field(default_factory=list)
    classification: ClassificationMeta
    question_agenda: list[AgendaQuestion] = Field(default_factory=list)
    template_version: str = TEMPLATE_VERSION
    classifier_version: str = CLASSIFIER_VERSION
    created_at: datetime | str

    @field_validator("primary_event_type")
    @classmethod
    def _primary(cls, v: str) -> str:
        assert_not_domain_as_event_type(v)
        return v

    @field_validator("secondary_event_types")
    @classmethod
    def _secondary(cls, values: list[str]) -> list[str]:
        out: list[str] = []
        for v in values:
            assert_not_domain_as_event_type(v)
            if v == "unknown":
                continue
            if v not in out:
                out.append(v)
        return out


def build_question_agenda(primary_event_type: str) -> list[AgendaQuestion]:
    assert_not_domain_as_event_type(primary_event_type)
    specs = get_question_template(primary_event_type)
    agenda = [AgendaQuestion.model_validate(spec) for spec in specs]
    if not any(q.question_id.endswith(".unknowns") for q in agenda):
        raise RuntimeError("agenda missing unknowns question")
    if not any(q.required_for_brief for q in agenda):
        raise RuntimeError("agenda missing required_for_brief questions")
    return agenda


def materialize_event_understanding(
    package: EventPackage | dict[str, Any],
    *,
    created_at: datetime | None = None,
) -> EventUnderstanding:
    """
    Deterministic transform: EventPackage → EventUnderstanding.

    Does not mutate the input package. Same package + classifier/template versions
    → identical semantic fields (created_at may be injected for tests).
    """
    pkg = parse_event_package(package)
    # Defensive copy proof: callers keep original dict untouched when dict input
    classified = classify_event_package(pkg)
    primary = classified["primary_event_type"]
    if not is_primary_event_type(primary):
        primary = "unknown"
    agenda = build_question_agenda(primary)
    when = created_at or _utc_now()
    return EventUnderstanding(
        event_id=str(pkg.event_id),
        package_version=int(pkg.package_version),
        primary_event_type=primary,
        secondary_event_types=list(classified.get("secondary_event_types") or []),
        domain_tags=[DomainTagHit.model_validate(d) for d in classified.get("domain_tags") or []],
        classification=ClassificationMeta.model_validate(
            {
                "confidence": classified["classification"]["confidence"],
                "rule_ids": classified["classification"]["rule_ids"],
                "classifier_version": classified["classification"]["classifier_version"],
            }
        ),
        question_agenda=agenda,
        template_version=TEMPLATE_VERSION,
        classifier_version=CLASSIFIER_VERSION,
        created_at=when.isoformat() if isinstance(when, datetime) else when,
    )


def understanding_identity(understanding: EventUnderstanding | dict[str, Any]) -> tuple:
    if isinstance(understanding, EventUnderstanding):
        return (
            understanding.event_id,
            understanding.package_version,
            understanding.classifier_version,
            understanding.template_version,
        )
    return (
        str(understanding["event_id"]),
        int(understanding["package_version"]),
        str(understanding["classifier_version"]),
        str(understanding["template_version"]),
    )


def semantic_fingerprint(understanding: EventUnderstanding) -> dict[str, Any]:
    """Comparable payload excluding created_at."""
    data = understanding.model_dump(mode="json")
    data.pop("created_at", None)
    return data
