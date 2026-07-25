"""E1 claim extractor — atomic claims from NewsArticle title+summary.

Emits multi-axis classification: epistemic_type, semantic_domains, source_strength.
Spans reference ``title`` or ``summary`` only. Default engine is deterministic heuristic.
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
    claim_fingerprint,
    normalize_claim_text,
    parse_extracted_claims,
)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'])")
_METRIC = re.compile(
    r"(\$[\d,.]+|\d+(?:\.\d+)?%|\d[\d,]*(?:\.\d+)?\s*(?:billion|million|bn|mn|trillion)|"
    r"\brevenue\b|\bguidance\b|\bearnings\b|\bprofit\b)",
    re.I,
)
_MARKET = re.compile(
    r"\b(shares?|stock|nasdaq|nyse|s&p|dow|index|trading|rally|selloff|futures?)\b",
    re.I,
)
_COMPANY = re.compile(
    r"\b(corp|inc|ltd|company|firm|ceo|cfo|executive|board)\b",
    re.I,
)
_TECH = re.compile(
    r"\b(chip|semiconductor|ai|software|cloud|gpu|model|algorithm)\b",
    re.I,
)
_REGULATION = re.compile(
    r"\b(regulator|sec|ftc|doj|antitrust|compliance|sanction)\b",
    re.I,
)
_LEGAL = re.compile(
    r"\b(lawsuit|court|indictment|settlement|judge|attorney|prosecut)\b",
    re.I,
)
_SECURITY = re.compile(
    r"\b(cyber|hack|breach|malware|ransomware|vulnerability)\b",
    re.I,
)
_SUPPLY = re.compile(
    r"\b(supply chain|factory|shipment|logistics|shortage)\b",
    re.I,
)
_PRODUCT = re.compile(
    r"\b(product|device|iphone|launch|release|sku)\b",
    re.I,
)
_PREDICTION = re.compile(
    r"\b(will|expected to|likely to|forecast|outlook|predicts?|may|might|could|"
    r"paves the way|signals that|would mean)\b",
    re.I,
)
_OPINION = re.compile(
    r"\b(we believe|in our view|opinion|argues that|should|must)\b",
    re.I,
)
_CORRECTION = re.compile(
    r"\b(corrects?|correction|erratum|mistakenly|previously reported|clarif(?:y|ies|ied))\b",
    re.I,
)
_ALLEGATION = re.compile(
    r"\b(alleged(?:ly)?|accused|accusation|suspected of|under investigation for)\b",
    re.I,
)
_ATTRIBUTION = re.compile(
    r"^(?P<attr>(?:according to|per|said|says|alleged(?:ly)?|reported(?:ly)?|"
    r"claimed|claims|sources? say(?:s)?|police said|officials? said)"
    r"[^:.]{0,80}(?::|,)?\s*)",
    re.I,
)
_MID_ATTR = re.compile(
    r"\b([A-Z][\w.&'-]+(?:\s+[A-Z][\w.&'-]+){0,3}\s+(?:said|says|told|alleged|claimed))\b"
)
_UNCERTAINTY = re.compile(
    r"\b(allegedly|reportedly|unconfirmed|possible|possibly|apparently|"
    r"estimated|about|roughly|around|up to|at least|nearly)\b",
    re.I,
)
_WIRE = re.compile(r"\b(reuters|bloomberg|ap|associated press|afp)\b", re.I)
_OFFICIAL = re.compile(
    r"\b(sec filing|company statement|press release|regulatory filing|official said)\b",
    re.I,
)
_MAJOR = re.compile(
    r"\b(new york times|washington post|wall street journal|financial times|bbc|cnn)\b",
    re.I,
)

LlmExtractFn = Callable[[str, str | None], Awaitable[Any]]


def _locate(field_text: str, excerpt: str) -> tuple[int | None, int | None]:
    idx = field_text.find(excerpt)
    if idx < 0:
        low = field_text.lower()
        el = excerpt.lower()
        idx = low.find(el)
        if idx < 0:
            return None, None
    return idx, idx + len(excerpt)


def _attribution_of(sentence: str) -> str | None:
    m = _ATTRIBUTION.match(sentence)
    if m:
        return normalize_claim_text(m.group("attr"))
    mid = _MID_ATTR.search(sentence)
    return mid.group(1) if mid else None


def _uncertainty_of(sentence: str) -> str | None:
    hits = _UNCERTAINTY.findall(sentence)
    if not hits:
        return None
    seen: list[str] = []
    for h in hits:
        if h.lower() not in {s.lower() for s in seen}:
            seen.append(h)
    return ", ".join(seen)


def _semantic_domains(sentence: str) -> list[str]:
    domains: list[str] = []
    checks = (
        (_COMPANY, "company"),
        (_METRIC, "financial"),
        (_MARKET, "market"),
        (_PRODUCT, "product"),
        (_TECH, "technology"),
        (_REGULATION, "regulation"),
        (_LEGAL, "legal"),
        (_SECURITY, "security"),
        (_SUPPLY, "supply_chain"),
    )
    for rx, name in checks:
        if rx.search(sentence) and name not in domains:
            domains.append(name)
    if not domains:
        domains.append("other")
    return domains


def _epistemic(sentence: str, attribution: str | None, uncertainty: str | None) -> str:
    if _CORRECTION.search(sentence):
        return "correction"
    if _ALLEGATION.search(sentence) or (uncertainty and "alleged" in uncertainty.lower()):
        return "allegation"
    if _OPINION.search(sentence):
        return "opinion"
    if _PREDICTION.search(sentence):
        return "prediction"
    if attribution:
        return "attributed_statement"
    return "fact"


def _source_strength(title: str, summary: str | None, sentence: str) -> str:
    blob = f"{title}\n{summary or ''}\n{sentence}"
    if _OFFICIAL.search(blob):
        return "official"
    if _WIRE.search(blob):
        return "wire"
    if _MAJOR.search(blob):
        return "major_media"
    return "unknown"


def _corrects_ref(sentence: str) -> str | None:
    m = re.search(
        r"(?:corrects?|clarif(?:y|ies|ied)|previously reported)\s+(.+)$",
        sentence,
        re.I,
    )
    if m:
        return normalize_claim_text(m.group(0))
    if _CORRECTION.search(sentence):
        return normalize_claim_text(sentence)
    return None


def _should_skip_title_as_fact(title: str, summary: str | None) -> bool:
    if not summary or not summary.strip():
        return True
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
        attr = _attribution_of(text)
        unc = _uncertainty_of(text)
        epistemic = _epistemic(text, attr, unc)
        domains = _semantic_domains(text)
        strength = _source_strength(title, summary, text)
        start, end = _locate(field_text, sentence.strip())
        excerpt = sentence.strip()
        if start is None:
            excerpt = text
            start, end = _locate(field_text, excerpt)

        # Epistemic safety: force attribution on attributed/allegation paths.
        if epistemic in ("attributed_statement", "allegation") and not attr:
            attr = "attributed source"
            if "said" in text.lower() or "according" in text.lower() or "alleged" in text.lower():
                attr = _attribution_of(text) or attr

        corrects = _corrects_ref(text) if epistemic == "correction" else None
        if epistemic == "correction" and not corrects:
            corrects = text

        try:
            candidate = ExtractedClaim(
                text=text,
                epistemic_type=epistemic,  # type: ignore[arg-type]
                semantic_domains=domains,  # type: ignore[arg-type]
                source_strength=strength,  # type: ignore[arg-type]
                source_field=field,  # type: ignore[arg-type]
                source_excerpt=excerpt,
                source_start=start,
                source_end=end,
                attribution=attr,
                uncertainty=unc,
                corrects_ref=corrects,
            )
        except ValidationError:
            return

        fp = claim_fingerprint(
            text=candidate.text,
            epistemic_type=candidate.epistemic_type,
            semantic_domains=list(candidate.semantic_domains),
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
        for part in parts[:8]:
            _add("summary", summary_text, part)

    title_text = (title or "").strip()
    if title_text and not _should_skip_title_as_fact(title_text, summary):
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
