"""Shared Gate P scoring on top of Gate M gold labels.

Prefix/FTS rows reuse Gate M extractive recoverability.
Provider rows score the model JSON separately from evidence availability.
"""
from __future__ import annotations

import json
import re
from typing import Any

from tests.gate_m.gold_questions import QUESTIONS
from tests.gate_m.measure import (
    classify_failure,
    contains,
    extractive_answer_ok,
    unanswerable_false_positive,
)
from tests.gate_p.position import EARLY, LATE, MIDDLE, SPAN, has_band, question_position

ANSWER_CONTRACT = {
    "answer": "...",
    "answerable": True,
    "supporting_evidence": ["..."],
    "page": None,
}

NEUTRAL_INSTRUCTIONS = """You are answering a question about one supplied document.

Rules:
- Use only the supplied document. Do not use outside knowledge.
- If the document does not establish the requested fact, set answerable to false.
- Do not guess missing numbers, names, rates, or clauses.
- When answerable, quote short verbatim spans from the document as supporting_evidence.
- Return JSON only, with this shape:
{"answer":"string","answerable":true,"supporting_evidence":["string"],"page":1}

page is the 1-based page number of the decisive evidence, or null if unknown/unanswerable.
"""


def parse_answer_json(raw: str | None) -> dict[str, Any] | None:
    text = str(raw or "").strip()
    if not text:
        return None
    candidates = [text]
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL | re.IGNORECASE)
    if fenced:
        candidates.insert(0, fenced.group(1))
    brace = re.search(r"\{.*\}", text, re.DOTALL)
    if brace:
        candidates.append(brace.group(0))
    for blob in candidates:
        try:
            data = json.loads(blob)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and "answer" in data:
            return data
    return None


def model_answer_ok(question: dict[str, Any], parsed: dict[str, Any] | None) -> bool:
    if not parsed:
        return False
    answer = str(parsed.get("answer") or "")
    answerable = parsed.get("answerable")
    if not question["answerable"]:
        if answerable is True:
            return False
        attractors = [a for a in (question.get("false_attractors") or []) if a]
        # A false attractor may appear as a denial ("does not mention ISO 14001").
        # Fail only when the model presents an attractor as the established answer
        # while claiming the question is answerable — already handled above — or
        # when it returns the attractor as the whole answer with no denial.
        if attractors and answerable is not False:
            return False
        lowered = answer.lower()
        denials = ("not established", "not mentioned", "does not", "no ", "not named", "absent")
        if any(contains(answer, a) for a in attractors) and not any(d in lowered for d in denials):
            return False
        return True
    if answerable is False:
        return False
    must = [m for m in (question.get("gold_must_all") or []) if m]
    if must:
        return all(contains(answer, m) for m in must)
    needles = [question["gold_answer"], *question.get("gold_answer_aliases", [])]
    return any(contains(answer, n) for n in needles if n)


def citation_page_ok(question: dict[str, Any], parsed: dict[str, Any] | None) -> bool | None:
    gold_pages = [int(s["page"]) for s in question.get("decisive_evidence") or [] if s.get("page")]
    if not gold_pages or not parsed:
        return None
    page = parsed.get("page")
    if page is None or page == "":
        return False
    try:
        return int(page) in gold_pages
    except (TypeError, ValueError):
        return False


def evidence_overlap(question: dict[str, Any], texts: list[str]) -> bool:
    spans = question.get("decisive_evidence") or []
    if not spans:
        return True
    blob = "\n".join(texts)
    return all(contains(blob, span.get("text_or_locator") or "") for span in spans)


def summarize_rows(rows: list[dict[str, Any]], *, methodology: str) -> dict[str, Any]:
    answerable = [r for r in rows if r.get("answerable")]
    unanswerable = [r for r in rows if not r.get("answerable")]
    recall_observable = [r for r in answerable if r.get("decisive_recall_status") != "NOT_OBSERVABLE"]
    recall_hits = sum(1 for r in recall_observable if r.get("decisive_in_injected"))
    passed = sum(1 for r in rows if r.get("pass"))
    mrl = sum(1 for r in rows if r.get("mrl"))
    citation_scored = [r for r in rows if r.get("citation_page_accuracy") is not None]
    exception_rows = [r for r in rows if r.get("category") == "EXCEPTION"]
    multi_rows = [r for r in rows if r.get("category") == "MULTI_HOP"]
    global_rows = [r for r in rows if r.get("category") == "GLOBAL"]
    table_rows = [r for r in rows if r.get("category") == "TABLE"]

    def _rate(subset: list[dict[str, Any]]) -> str:
        if not subset:
            return "n/a"
        return f"{sum(1 for r in subset if r.get('pass'))}/{len(subset)}"

    def _band(band: str) -> str:
        subset = [r for r in rows if r.get("position") == band and r.get("answerable")]
        return _rate(subset)

    frontier: dict[str, int] = {}
    for r in rows:
        cat = r.get("failure_category")
        if cat:
            frontier[cat] = frontier.get(cat, 0) + 1

    latencies = [r.get("latency_ms") for r in rows if isinstance(r.get("latency_ms"), (int, float))]
    in_tok = sum(int(r.get("input_tokens") or 0) for r in rows)
    out_tok = sum(int(r.get("output_tokens") or 0) for r in rows)
    cost = sum(float(r.get("cost_usd") or 0.0) for r in rows)

    return {
        "methodology": methodology,
        "questions": len(rows),
        "answerable": len(answerable),
        "unanswerable": len(unanswerable),
        "answer_correctness": f"{passed}/{len(rows)}" if rows else "n/a",
        "answer_correctness_n": passed,
        "decisive_span_recall": (
            "NOT_OBSERVABLE"
            if answerable and not recall_observable
            else f"{recall_hits}/{len(recall_observable)}"
            if recall_observable
            else "n/a"
        ),
        "decisive_span_recall_n": recall_hits,
        "mrl": mrl,
        "mrl_rate": round(100.0 * mrl / len(rows), 1) if rows else 0.0,
        "unanswerable_precision": _rate(unanswerable),
        "citation_page_accuracy": (
            f"{sum(1 for r in citation_scored if r.get('citation_page_accuracy'))}/{len(citation_scored)}"
            if citation_scored
            else "not_observable"
        ),
        "early": _band(EARLY),
        "middle": _band(MIDDLE),
        "late": _band(LATE),
        "span": _band(SPAN),
        "early_any": _rate([r for r in rows if r.get("answerable") and has_band(_q(r), EARLY)]),
        "middle_any": _rate([r for r in rows if r.get("answerable") and has_band(_q(r), MIDDLE)]),
        "late_any": _rate([r for r in rows if r.get("answerable") and has_band(_q(r), LATE)]),
        "exceptions": _rate(exception_rows),
        "multi_hop": _rate(multi_rows),
        "global": _rate(global_rows),
        "table": _rate(table_rows),
        "failure_frontier": frontier,
        "mean_latency_ms": round(sum(latencies) / len(latencies), 2) if latencies else None,
        "input_tokens": in_tok or None,
        "output_tokens": out_tok or None,
        "approx_cost_usd": round(cost, 6) if cost else None,
    }


def _q(row: dict[str, Any]) -> dict[str, Any]:
    found = next((q for q in QUESTIONS if q["question_id"] == row.get("question_id")), None)
    return found or {"decisive_evidence": [], "answerable": row.get("answerable")}


def score_extractive_row(
    question: dict[str, Any],
    *,
    injected: str,
    in_source: bool,
    processing_ok: bool,
    latency_ms: float | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    in_injected = True
    spans = question.get("decisive_evidence") or []
    if spans:
        in_injected = all(contains(injected, span.get("text_or_locator") or "") for span in spans)
    answer_ok = extractive_answer_ok(question, injected)
    if not question["answerable"]:
        answer_ok = True
    fail_cat, residual = classify_failure(
        question,
        in_source=in_source,
        in_injected=in_injected,
        answer_ok=answer_ok,
        processing_ok=processing_ok,
    )
    if question["answerable"]:
        passed = bool(in_injected and answer_ok and processing_ok)
    else:
        passed = bool(answer_ok and processing_ok)
    row = {
        "question_id": question["question_id"],
        "question": question["question"],
        "category": question["category"],
        "answerable": question["answerable"],
        "gold_answer": question["gold_answer"],
        "position": question_position(question),
        "processing_ok": processing_ok,
        "decisive_in_source": in_source,
        "decisive_in_injected": in_injected,
        "decisive_recall_status": "observed",
        "extractive_answer_ok": answer_ok,
        "model_answer_ok": None,
        "pass": passed,
        "mrl": residual == "MATERIAL_RECOVERABLE_LOSS",
        "failure_category": fail_cat,
        "residual": residual,
        "citation_page_accuracy": None,
        "false_attractor_in_injected": unanswerable_false_positive(question, injected),
        "injected_chars": len(injected or ""),
        "latency_ms": latency_ms,
        "input_tokens": None,
        "output_tokens": None,
        "cost_usd": None,
    }
    if extra:
        row.update(extra)
    return row
