"""Classify a measured failure from evidence, not question IDs."""

from __future__ import annotations

from services.improvement.records import FailureRecord, assert_known_class
from services.workspace_files.file_resolver import significant_tokens_in_order

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
    """Return a taxonomy label. Ignores benchmark task ids."""
    if record.security_relevant:
        return "PERMISSION_FAILURE"

    component = (record.component or "").casefold()
    notes = (record.notes or "").casefold()
    if "routing" in component:
        return "ROUTING_FAILURE"
    if component.startswith("tool") or "tool_failure" in notes:
        return "TOOL_FAILURE"
    if "structured" in notes or "json_schema" in notes:
        return "STRUCTURED_OUTPUT_FAILURE"
    if "business_memory" in component or "memory write" in notes:
        return "BUSINESS_MEMORY_FAILURE"
    if "agent_action" in component:
        return "AGENT_ACTION_FAILURE"

    query = record.input_reference or ""
    expected = record.expected_evidence or ""
    retrieved = record.observed_evidence or ""
    actual = record.actual_behavior or ""
    gold = record.expected_behavior or ""
    mode = (record.retrieval_mode or "").strip().lower()

    overlap = token_jaccard(query, expected)
    expected_in_retrieved = span_present(expected, retrieved)
    empty = record.retrieved_empty or (
        not retrieved.strip() and mode in {"empty", "off", ""}
    )

    if "leak:" in retrieved.casefold() or "other:" in retrieved.casefold():
        return "WRONG_SOURCE"

    if mode in {"prefix_fallback", "off"} and not expected_in_retrieved and not empty:
        return "LEXICAL_VARIATION" if overlap < _LEXICAL_JACCARD else "CONTEXT_LOSS"

    if empty and not expected_in_retrieved:
        return "LEXICAL_VARIATION" if overlap < _LEXICAL_JACCARD else "CONTEXT_LOSS"

    if expected_in_retrieved and gold and not span_present(gold, actual or retrieved):
        if "table" in notes or "|" in expected:
            return "TABLE_LAYOUT"
        return "MODEL_REASONING_FAILURE"

    if not expected_in_retrieved:
        return "LEXICAL_VARIATION" if overlap < _LEXICAL_JACCARD else "RANKING_FAILURE"

    return "OTHER"


def classify_and_apply(record: FailureRecord) -> FailureRecord:
    record.failure_class = assert_known_class(classify_failure_record(record))
    return record
