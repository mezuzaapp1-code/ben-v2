"""Classify a measured retrieval failure from evidence, not question IDs.

Uses token overlap between the user query and the expected evidence span,
plus whether retrieval returned chunks and whether the span was injected.
Does not import gold question IDs or switch on ``task_id``.
"""

from __future__ import annotations

from typing import Any

from services.workspace_files.file_resolver import significant_tokens_in_order
from services.improvement.schema import FAILURE_CLASSES, FailureRecord

_LEXICAL_JACCARD = 0.20


def token_jaccard(left: str, right: str) -> float:
    a = set(significant_tokens_in_order(left or ""))
    b = set(significant_tokens_in_order(right or ""))
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def span_present(expected: str, retrieved: str) -> bool:
    needle = " ".join((expected or "").split()).casefold()
    hay = " ".join((retrieved or "").split()).casefold()
    return bool(needle) and needle in hay


def classify_failure_record(record: FailureRecord) -> str:
    """Return one of FAILURE_CLASSES. Ignores ``task_id`` / question id."""
    query = record.user_query or ""
    expected = record.expected_evidence or ""
    retrieved = record.retrieved_evidence or ""
    actual = record.actual_answer or ""
    gold = record.expected_answer or ""
    mode = (record.retrieval_mode or "").strip().lower()
    sources = {str(s).strip().lower() for s in record.source_files if str(s).strip()}

    if record.security_relevant:
        return "WRONG_SOURCE"

    overlap = token_jaccard(query, expected)
    expected_in_retrieved = span_present(expected, retrieved)
    answer_in_retrieved = span_present(gold, retrieved) if gold else False
    empty = record.retrieved_empty or (
        not (retrieved or "").strip() and mode in {"empty", "off", ""}
    )

    if sources and retrieved:
        # Retrieved evidence naming a different file than the authorized set.
        leaked = [
            name
            for name in sources
            if name.startswith("other:") or name.startswith("leak:")
        ]
        if leaked:
            return "WRONG_SOURCE"

    if mode in {"prefix_fallback", "off"} and not expected_in_retrieved and not empty:
        if overlap < _LEXICAL_JACCARD:
            return "LEXICAL_VARIATION"
        return "CONTEXT_LOSS"

    if empty and not expected_in_retrieved:
        if overlap < _LEXICAL_JACCARD:
            return "LEXICAL_VARIATION"
        return "CONTEXT_LOSS"

    if expected_in_retrieved and gold and not span_present(gold, actual or retrieved):
        # Span was injected; remaining miss is reasoning / layout, not retrieval.
        if "table" in (record.notes or "").casefold() or "|" in expected:
            return "TABLE_LAYOUT"
        return "MODEL_REASONING_FAILURE"

    if expected_in_retrieved and (answer_in_retrieved or span_present(gold, actual)):
        return "OTHER"

    if not expected_in_retrieved:
        if overlap < _LEXICAL_JACCARD:
            return "LEXICAL_VARIATION"
        return "RANKING_FAILURE"

    return "OTHER"


def classify_and_apply(record: FailureRecord) -> FailureRecord:
    label = classify_failure_record(record)
    if label not in FAILURE_CLASSES:
        label = "OTHER"
    record.failure_class = label
    return record


def classify_payload(data: dict[str, Any]) -> str:
    return classify_failure_record(FailureRecord.from_mapping(data))
