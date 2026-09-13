"""Score Gate P rows. Reuses Gate M extractive definitions. Does not change gold labels."""
from __future__ import annotations

from collections import Counter
from typing import Any

from tests.gate_m.gold_questions import QUESTIONS
from tests.gate_m.measure import contains, extractive_answer_ok
from tests.gate_p.position import position_bucket

QUESTION_BY_ID = {q["question_id"]: q for q in QUESTIONS}


def gold_for(row: dict[str, Any]) -> dict[str, Any]:
    return QUESTION_BY_ID[row["question_id"]]


def answer_text_ok(question: dict[str, Any], answer: str) -> bool:
    if not question["answerable"]:
        body = answer or ""
        if contains(body, "not established") or contains(body, "not mentioned"):
            return True
        forbidden = list(question.get("forbidden_answers") or [])
        if any(contains(body, a) for a in forbidden if a):
            return False
        attractors = list(question.get("false_attractors") or [])
        if any(contains(body, a) for a in attractors if a) and "not" not in (body or "").lower():
            return False
        return contains(body, "not established") or contains(body, "does not")
    return extractive_answer_ok(question, answer)


def classify_row(
    question: dict[str, Any],
    *,
    in_source: bool,
    in_injected: bool | None,
    answer_ok: bool,
    processing_ok: bool,
    retrieval_kind: str,
    retrieved_empty: bool = False,
) -> tuple[str | None, str]:
    """First material failure. in_injected None means NOT_OBSERVABLE."""
    if not question["answerable"]:
        if answer_ok:
            return None, "NONE"
        return "UNANSWERABLE_FAILURE", "NO_ANSWER_EXISTS"

    if not processing_ok or not in_source:
        return "PROCESSING_FAILURE", "SOURCE_PROCESSING_DEFECT"

    if in_injected is False:
        category = question["category"]
        if category == "EXCEPTION":
            return "EXCEPTION_MISSED", "MATERIAL_RECOVERABLE_LOSS"
        if category == "MULTI_HOP":
            return "MULTI_HOP", "MATERIAL_RECOVERABLE_LOSS"
        if category == "GLOBAL":
            return "GLOBAL_QUESTION", "MATERIAL_RECOVERABLE_LOSS"
        if retrieval_kind == "fts":
            if retrieved_empty:
                return "LEXICAL_MISMATCH", "MATERIAL_RECOVERABLE_LOSS"
            return "RANKING_FAILURE", "MATERIAL_RECOVERABLE_LOSS"
        if retrieval_kind == "prefix":
            return "CONTEXT_LOSS", "MATERIAL_RECOVERABLE_LOSS"
        return "OTHER", "MATERIAL_RECOVERABLE_LOSS"

    if in_injected is True and not answer_ok:
        if question["category"] == "TABLE":
            return "TABLE_LAYOUT", "EVIDENCE_EXISTS_NON_MATERIAL"
        return "MODEL_REASONING_FAILURE", "EVIDENCE_EXISTS_NON_MATERIAL"

    if in_injected is None and not answer_ok:
        return "OTHER", "EVIDENCE_EXISTS_NON_MATERIAL"

    if not answer_ok:
        return "MODEL_REASONING_FAILURE", "EVIDENCE_EXISTS_NON_MATERIAL"
    return None, "NONE"


def summarize_mode(mode: str, rows: list[dict[str, Any]], *, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    answerable = [r for r in rows if r.get("answerable")]
    unanswerable = [r for r in rows if not r.get("answerable")]
    passed = [r for r in rows if r.get("pass")]
    mrl = [r for r in rows if r.get("mrl")]
    frontier: Counter[str] = Counter(r["failure_category"] for r in rows if r.get("failure_category"))
    recall_obs = [r for r in answerable if r.get("decisive_in_injected") is not None]
    citation_obs = [r for r in answerable if r.get("citation_page_accuracy") is not None]

    def _rate(subset: list[dict[str, Any]]) -> str:
        if not subset:
            return "n/a"
        return f"{sum(1 for r in subset if r.get('pass'))}/{len(subset)}"

    by_pos: dict[str, list[dict[str, Any]]] = {"EARLY": [], "MIDDLE": [], "LATE": []}
    for r in answerable:
        bucket = r.get("position") or position_bucket(gold_for(r))
        if bucket in by_pos:
            by_pos[bucket].append(r)

    def _cat(name: str) -> str:
        sub = [r for r in answerable if r.get("category") == name]
        return _rate(sub)

    latency_vals = [r["latency_ms"] for r in rows if isinstance(r.get("latency_ms"), (int, float))]
    in_tok = sum(int(r.get("input_tokens") or 0) for r in rows)
    out_tok = sum(int(r.get("output_tokens") or 0) for r in rows)
    cost = sum(float(r.get("cost_usd") or 0.0) for r in rows)

    recall = (
        "NOT_OBSERVABLE"
        if answerable and not recall_obs
        else (
            f"{sum(1 for r in recall_obs if r.get('decisive_in_injected'))}/{len(recall_obs)}"
            if recall_obs
            else "n/a"
        )
    )
    citation = (
        "NOT_OBSERVABLE"
        if answerable and not citation_obs
        else (
            f"{sum(1 for r in citation_obs if r.get('citation_page_accuracy'))}/{len(citation_obs)}"
            if citation_obs
            else "n/a"
        )
    )

    summary: dict[str, Any] = {
        "mode": mode,
        "status": "measured" if rows else "blocked",
        "questions": len(rows),
        "answer_correctness": f"{len(passed)}/{len(rows)}" if rows else "n/a",
        "answer_correctness_n": len(passed),
        "decisive_span_recall": recall,
        "mrl": len(mrl),
        "mrl_rate": round(100.0 * len(mrl) / len(rows), 1) if rows else None,
        "unanswerable_precision": _rate(unanswerable),
        "citation_page_accuracy": citation,
        "early": _rate(by_pos["EARLY"]),
        "middle": _rate(by_pos["MIDDLE"]),
        "late": _rate(by_pos["LATE"]),
        "exceptions": _cat("EXCEPTION"),
        "multi_hop": _cat("MULTI_HOP"),
        "global": _cat("GLOBAL"),
        "table": _cat("TABLE"),
        "failure_frontier": {k: v for k, v in frontier.most_common() if k},
        "mean_latency_ms": round(sum(latency_vals) / len(latency_vals), 2) if latency_vals else None,
        "input_tokens": in_tok or None,
        "output_tokens": out_tok or None,
        "approx_cost_usd": round(cost, 4) if cost else None,
    }
    if extra:
        summary.update(extra)
    return summary
