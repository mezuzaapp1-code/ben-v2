"""E1 claim extractor — atomic claims from NewsArticle title+summary.

Articles store no full body; spans reference ``title`` or ``summary`` only.
Default engine is a deterministic heuristic (provider=heuristic). An injectable
``llm_extract_fn`` may supply raw JSON for LLM-backed extraction.
"""
from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import ValidationError

from services.news.claim_contract import (
    EXTRACTOR_VERSION,
    HEURISTIC_MODEL,
    HEURISTIC_PROVIDER,
    ExtractedClaim,
    ExtractionResult,
    normalize_claim_text,
    parse_extracted_claims,
)

# Sentence-ish split; keep attribution attached to the clause.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'])")
_METRIC = re.compile(
    r"(\$[\d,.]+|\d+(?:\.\d+)?%|\d[\d,]*(?:\.\d+)?\s*(?:billion|million|bn|mn|trillion))",
    re.I,
)
_MARKET = re.compile(
    r"\b(shares?|stock|nasdaq|nyse|s&p|dow|index|trading|rally|selloff|futures?)\b",
    re.I,
)
_IMPLICATION = re.compile(
    r"\b(may|might|could|suggests?|implies?|means that|expected to|likely to|"
    r"paves the way|signals that|would mean)\b",
    re.I,
)
_ATTRIBUTION = re.compile(
    r"^(?P<attr>(?:according to|per|said|says|alleged(?:ly)?|reported(?:ly)?|"
    r"claimed|claims|sources? say(?:s)?|police said|officials? said)"
    r"[^:.]{0,80}(?::|,)?\s*)",
    re.I,
)
_UNCERTAINTY = re.compile(
    r"\b(allegedly|reportedly|unconfirmed|possible|possibly|apparently|"
    r"estimated|about|roughly|around|up to|at least|nearly)\b",
    re.I,
)

LlmExtractFn = Callable[[str, str | None], Awaitable[Any]]


def _locate(field_text: str, excerpt: str) -> tuple[int | None, int | None]:
    idx = field_text.find(excerpt)
    if idx < 0:
        # try normalized loose search
        low = field_text.lower()
        el = excerpt.lower()
        idx = low.find(el)
        if idx < 0:
            return None, None
    return idx, idx + len(excerpt)


def _classify(sentence: str) -> tuple[str, str]:
    if _IMPLICATION.search(sentence):
        return "implication", "interpretive"
    if _MARKET.search(sentence):
        return "market", "factual"
    if _METRIC.search(sentence):
        return "metric", "factual"
    return "occurrence", "factual"


def _attribution_of(sentence: str) -> str | None:
    m = _ATTRIBUTION.match(sentence)
    if not m:
        # also scan mid-sentence "X said"
        mid = re.search(
            r"\b([A-Z][\w.&'-]+(?:\s+[A-Z][\w.&'-]+){0,3}\s+(?:said|says|told|alleged))\b",
            sentence,
        )
        return mid.group(1) if mid else None
    return normalize_claim_text(m.group("attr"))


def _uncertainty_of(sentence: str) -> str | None:
    hits = _UNCERTAINTY.findall(sentence)
    if not hits:
        return None
    # preserve first distinct markers in order of appearance
    seen: list[str] = []
    for h in hits:
        low = h.lower()
        if low not in {s.lower() for s in seen}:
            seen.append(h)
    return ", ".join(seen)


def _should_skip_title_as_fact(title: str, summary: str | None) -> bool:
    """Do not treat headline as a fact unless body/summary supports it."""
    if not summary or not summary.strip():
        return True
    # Require substantial overlap of significant tokens.
    t_tokens = {w.lower() for w in re.findall(r"[A-Za-z0-9$%]{3,}", title)}
    s_tokens = {w.lower() for w in re.findall(r"[A-Za-z0-9$%]{3,}", summary)}
    if not t_tokens:
        return True
    overlap = len(t_tokens & s_tokens) / max(len(t_tokens), 1)
    return overlap < 0.35


def extract_claims_heuristic(*, title: str, summary: str | None) -> ExtractionResult:
    """Deterministic E1 extractor over title+summary only."""
    claims: list[ExtractedClaim] = []
    seen_fp: set[str] = set()

    def _add(field: str, field_text: str, sentence: str) -> None:
        text = normalize_claim_text(sentence)
        if len(text) < 12:
            return
        claim_type, role = _classify(text)
        # Never drop attribution/uncertainty language from the claim text.
        attr = _attribution_of(text)
        unc = _uncertainty_of(text)
        start, end = _locate(field_text, sentence.strip())
        excerpt = sentence.strip()
        if start is None:
            excerpt = text
            start, end = _locate(field_text, excerpt)
        candidate = ExtractedClaim(
            text=text,
            claim_type=claim_type,  # type: ignore[arg-type]
            role=role,  # type: ignore[arg-type]
            source_field=field,  # type: ignore[arg-type]
            source_excerpt=excerpt,
            source_start=start,
            source_end=end,
            attribution=attr,
            uncertainty=unc,
        )
        from services.news.claim_contract import claim_fingerprint

        fp = claim_fingerprint(
            text=candidate.text,
            claim_type=candidate.claim_type,
            source_field=candidate.source_field,
            source_start=candidate.source_start,
            source_end=candidate.source_end,
        )
        if fp in seen_fp:
            return
        seen_fp.add(fp)
        claims.append(candidate)

    summary_text = (summary or "").strip()
    if summary_text:
        parts = [p.strip() for p in _SENTENCE_SPLIT.split(summary_text) if p.strip()]
        # Prefer fewer atomic claims: cap and skip near-duplicates by length.
        for part in parts[:8]:
            _add("summary", summary_text, part)

    title_text = (title or "").strip()
    if title_text and not _should_skip_title_as_fact(title_text, summary):
        # Only add title if it looks like a supported metric/occurrence already in summary.
        _add("title", title_text, title_text)

    return ExtractionResult(
        claims=claims,
        provider=HEURISTIC_PROVIDER,
        model=HEURISTIC_MODEL,
        extractor_version=EXTRACTOR_VERSION,
    )


async def extract_claims(
    *,
    title: str,
    summary: str | None,
    llm_extract_fn: LlmExtractFn | None = None,
) -> ExtractionResult:
    """Run LLM extractor when provided; otherwise heuristic. Validates all claims."""
    if llm_extract_fn is None:
        return extract_claims_heuristic(title=title, summary=summary)

    raw = await llm_extract_fn(title, summary)
    if isinstance(raw, str):
        raw = _parse_json_blob(raw)
    try:
        claims = parse_extracted_claims(raw)
    except (ValidationError, ValueError, TypeError) as exc:
        raise ValueError(f"malformed_model_output: {exc}") from exc

    provider = "llm"
    model = "injected"
    if isinstance(raw, dict):
        provider = str(raw.get("provider") or provider)
        model = str(raw.get("model") or model)

    return ExtractionResult(
        claims=claims,
        provider=provider,
        model=model,
        extractor_version=EXTRACTOR_VERSION,
    )


def _parse_json_blob(text: str) -> Any:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"malformed_model_output: invalid JSON ({exc})") from exc
