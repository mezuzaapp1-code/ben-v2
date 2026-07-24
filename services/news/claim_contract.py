"""E1 NewsClaim contract — atomic, provenance-bound claims (not Events)."""
from __future__ import annotations

import hashlib
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

EXTRACTOR_VERSION = "e1.1"
HEURISTIC_PROVIDER = "heuristic"
HEURISTIC_MODEL = "rules-e1.1"

ClaimType = Literal["occurrence", "metric", "market", "implication"]
ClaimRole = Literal["factual", "interpretive"]
SourceField = Literal["title", "summary"]
ClaimStatus = Literal["extracted", "failed", "superseded"]
ExtractionStatus = Literal["pending", "succeeded", "failed", "skipped"]

_WS = re.compile(r"\s+")


def normalize_claim_text(text: str) -> str:
    return _WS.sub(" ", text.strip())


def content_fingerprint(*, title: str, summary: str | None) -> str:
    raw = f"{title or ''}\n{summary or ''}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def claim_fingerprint(
    *,
    text: str,
    claim_type: str,
    source_field: str,
    source_start: int | None,
    source_end: int | None,
) -> str:
    key = (
        f"{normalize_claim_text(text).lower()}|{claim_type}|{source_field}|"
        f"{source_start}|{source_end}"
    )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


class ExtractedClaim(BaseModel):
    """Validated claim candidate before persistence."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(..., min_length=1, max_length=2000)
    claim_type: ClaimType
    role: ClaimRole
    source_field: SourceField
    source_excerpt: str = Field(..., min_length=1, max_length=4000)
    source_start: int | None = Field(None, ge=0)
    source_end: int | None = Field(None, ge=0)
    attribution: str | None = Field(None, max_length=1000)
    uncertainty: str | None = Field(None, max_length=1000)

    @field_validator("text", "source_excerpt", "attribution", "uncertainty", mode="before")
    @classmethod
    def _strip(cls, v: Any) -> Any:
        if isinstance(v, str):
            return normalize_claim_text(v) if v.strip() else v
        return v

    @model_validator(mode="after")
    def _enforce_rules(self) -> ExtractedClaim:
        if self.claim_type == "implication" and self.role != "interpretive":
            raise ValueError("implication claims must be role=interpretive")
        if self.role == "interpretive" and self.claim_type not in ("implication",):
            # Allow only implication as interpretive in E1 taxonomy.
            raise ValueError("interpretive role is reserved for implication claims in E1")
        if self.source_start is not None and self.source_end is not None:
            if self.source_end < self.source_start:
                raise ValueError("source_end must be >= source_start")
        if not self.source_excerpt.strip():
            raise ValueError("source_excerpt is required")
        return self


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
