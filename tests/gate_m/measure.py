"""Score current Gate 3D prefix injection against Gate M gold labels.

Does not enable FTS, embeddings, or change retrieval. Calls
load_ready_files_context with production defaults.
"""
from __future__ import annotations

import json
import re
import time
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from services.workspace_files.document_parser import PdfDocumentParser
from services.workspace_files.extraction_pipeline import _legacy_projection, derive_lifecycle
from services.workspace_files.file_resolver import PER_FILE_MAX_CHARS
from services.workspace_files.service import load_ready_files_context
from tests.gate_m.gold_document import GOLD_FILENAME, PAGES
from tests.gate_m.gold_questions import QUESTIONS
from tests.gate_m.pdf_builder import make_multiline_pdf

ORG = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1")
WS = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb1")
FILE_ID = uuid.UUID("cccccccc-cccc-cccc-cccc-ccccccccccc1")

FAILURE_CATEGORIES = (
    "LEXICAL_MISMATCH",
    "SEMANTIC_MISMATCH",
    "WRONG_SOURCE",
    "EXCEPTION_MISSED",
    "MULTI_HOP",
    "GLOBAL_QUESTION",
    "TABLE_LAYOUT",
    "CROSS_FILE",
    "RANKING_FAILURE",
    "CONTEXT_LOSS",
    "PROCESSING_FAILURE",
    "MODEL_REASONING_FAILURE",
    "UNANSWERABLE_FAILURE",
    "OTHER",
)

RESIDUALS = (
    "NO_ANSWER_EXISTS",
    "QUESTION_AMBIGUOUS",
    "SOURCE_PROCESSING_DEFECT",
    "EVIDENCE_EXISTS_NON_MATERIAL",
    "MATERIAL_RECOVERABLE_LOSS",
    "NONE",
)


def norm(text: str | None) -> str:
    body = unicodedata.normalize("NFKC", str(text or ""))
    body = body.replace("\u00a0", " ")
    return re.sub(r"\s+", " ", body).strip().lower()


def contains(haystack: str, needle: str) -> bool:
    n = norm(needle)
    return bool(n) and n in norm(haystack)


@dataclass
class PageExtract:
    page: int
    text: str
    status: str


def extract_gold_pdf(tmp_pdf: Path) -> dict[str, Any]:
    tmp_pdf.write_bytes(make_multiline_pdf(PAGES))
    t0 = time.perf_counter()
    doc = PdfDocumentParser().parse(
        tmp_pdf, media_type="application/pdf", filename=GOLD_FILENAME
    )
    parse_ms = round((time.perf_counter() - t0) * 1000.0, 1)
    lifecycle, truncated = derive_lifecycle(doc, chunk_count=0)
    status, extracted, code, msg = _legacy_projection(doc, lifecycle)
    pages = [
        PageExtract(page=p.page_number, text=p.text or "", status=p.status)
        for p in doc.pages
    ]
    return {
        "parser_id": doc.parser_id,
        "source_page_count": doc.source_page_count,
        "lifecycle": lifecycle,
        "legacy_status": status,
        "extracted_text": extracted or "",
        "failure_code": code,
        "failure_message": msg,
        "truncated": truncated,
        "parse_ms": parse_ms,
        "pages": pages,
        "extracted_chars": len(extracted or ""),
        "prefix_budget": PER_FILE_MAX_CHARS,
        "prefix_text": (extracted or "")[:PER_FILE_MAX_CHARS],
    }


class _Row:
    def __init__(self, text: str):
        self.org_id = ORG
        self.workspace_id = WS
        self.status = "ready"
        self.extracted_text = text
        self.display_name = GOLD_FILENAME
        self.original_filename = GOLD_FILENAME
        self.created_at = 1
        self.id = FILE_ID


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, *a, **k):
        return _FakeResult(self._rows)


async def inject_context(extracted_text: str, question: str):
    rows = [_Row(extracted_text)]
    session = _FakeSession(rows)

    def _factory():
        return session

    import services.workspace_files.service as svc

    original = svc.get_db_session
    svc.get_db_session = _factory  # type: ignore[assignment]
    t0 = time.perf_counter()
    try:
        ctx = await load_ready_files_context(
            ORG,
            WS,
            max_chars=12_000,
            user_query=question,
        )
    finally:
        svc.get_db_session = original  # type: ignore[assignment]
    latency_ms = round((time.perf_counter() - t0) * 1000.0, 1)
    return ctx, latency_ms


def locator_in_text(locator: str, text: str) -> bool:
    return contains(text, locator)


def decisive_in_source(question: dict[str, Any], extracted: str, pages: list[PageExtract]) -> bool:
    spans = question.get("decisive_evidence") or []
    if not spans:
        # Unanswerable with no locator: fact must be absent from source.
        return True
    for span in spans:
        loc = span.get("text_or_locator") or ""
        if not locator_in_text(loc, extracted):
            page_no = int(span.get("page") or 0)
            page_txt = next((p.text for p in pages if p.page == page_no), "")
            if not locator_in_text(loc, page_txt):
                return False
    return True


def decisive_in_injected(question: dict[str, Any], injected: str) -> bool:
    spans = question.get("decisive_evidence") or []
    if not spans:
        return True
    return all(locator_in_text(span.get("text_or_locator") or "", injected) for span in spans)


def extractive_answer_ok(question: dict[str, Any], injected: str) -> bool:
    """Perfect extractive reader over the injected block only. Not an LLM."""
    if not question["answerable"]:
        return True
    must = [m for m in (question.get("gold_must_all") or []) if m]
    if must:
        return all(contains(injected, m) for m in must)
    needles = [question["gold_answer"], *question.get("gold_answer_aliases", [])]
    return any(contains(injected, n) for n in needles if n)


def unanswerable_false_positive(question: dict[str, Any], injected: str) -> bool:
    """True when a listed attractor is in context (risk, not automatic fail)."""
    if question["answerable"]:
        return False
    return any(contains(injected, a) for a in question.get("false_attractors") or [])


def classify_failure(
    question: dict[str, Any],
    *,
    in_source: bool,
    in_injected: bool,
    answer_ok: bool,
    processing_ok: bool,
) -> tuple[str | None, str]:
    category = question["category"]
    if not question["answerable"]:
        if not in_source and answer_ok:
            return None, "NONE"
        if not answer_ok:
            return "UNANSWERABLE_FAILURE", "NO_ANSWER_EXISTS"
        return None, "NONE"

    if not processing_ok or not in_source:
        return "PROCESSING_FAILURE", "SOURCE_PROCESSING_DEFECT"

    if not in_injected:
        # Prefix injection never searched; a missing late span is context loss.
        # Keep a more specific first-failure label when the question type names it.
        if category == "EXCEPTION":
            return "EXCEPTION_MISSED", "MATERIAL_RECOVERABLE_LOSS"
        if category == "MULTI_HOP":
            return "MULTI_HOP", "MATERIAL_RECOVERABLE_LOSS"
        if category == "GLOBAL":
            return "GLOBAL_QUESTION", "MATERIAL_RECOVERABLE_LOSS"
        return "CONTEXT_LOSS", "MATERIAL_RECOVERABLE_LOSS"

    if not answer_ok:
        if category == "TABLE":
            return "TABLE_LAYOUT", "EVIDENCE_EXISTS_NON_MATERIAL"
        if category == "MULTI_HOP":
            return "MODEL_REASONING_FAILURE", "EVIDENCE_EXISTS_NON_MATERIAL"
        if category == "GLOBAL":
            return "MODEL_REASONING_FAILURE", "EVIDENCE_EXISTS_NON_MATERIAL"
        if category == "LEXICAL_VARIATION":
            return "SEMANTIC_MISMATCH", "EVIDENCE_EXISTS_NON_MATERIAL"
        return "MODEL_REASONING_FAILURE", "EVIDENCE_EXISTS_NON_MATERIAL"

    return None, "NONE"


async def run_benchmark(tmp_pdf: Path) -> dict[str, Any]:
    extracted = extract_gold_pdf(tmp_pdf)
    processing_ok = (
        extracted["legacy_status"] == "ready"
        and extracted["source_page_count"] == len(PAGES)
        and bool(extracted["extracted_text"])
    )
    rows_out: list[dict[str, Any]] = []
    for question in QUESTIONS:
        ctx, latency_ms = await inject_context(extracted["extracted_text"], question["question"])
        injected = ctx.block or ""
        in_source = decisive_in_source(question, extracted["extracted_text"], extracted["pages"])
        in_injected = decisive_in_injected(question, injected)
        answer_ok = extractive_answer_ok(question, injected)
        if not question["answerable"]:
            forbidden = list(question.get("forbidden_answers") or [])
            leaked = any(contains(extracted["extracted_text"], a) for a in forbidden if a)
            answer_ok = not leaked
        fail_cat, residual = classify_failure(
            question,
            in_source=in_source,
            in_injected=in_injected,
            answer_ok=answer_ok,
            processing_ok=processing_ok,
        )
        evidence = ctx.response_evidence or {}
        pages_in_evidence = [
            item.get("page")
            for item in (evidence.get("evidence") or [])
            if isinstance(item, dict) and item.get("page") is not None
        ]
        gold_pages = [s.get("page") for s in question.get("decisive_evidence") or [] if s.get("page")]
        citation_observable = bool(pages_in_evidence)
        citation_ok = None
        if citation_observable and gold_pages:
            citation_ok = any(p in pages_in_evidence for p in gold_pages)
        gold_page_markers_in_injected = [
            p
            for p in gold_pages
            if contains(injected, f"[[PAGE {p}]]")
        ]
        mrl = residual == "MATERIAL_RECOVERABLE_LOSS"
        passed = fail_cat is None and (in_injected if question["answerable"] else answer_ok)
        if question["answerable"]:
            passed = in_injected and answer_ok and processing_ok
        else:
            passed = answer_ok and processing_ok
        rows_out.append(
            {
                "question_id": question["question_id"],
                "question": question["question"],
                "category": question["category"],
                "answerable": question["answerable"],
                "gold_answer": question["gold_answer"],
                "expected_answerability": question["answerable"],
                "processing_ok": processing_ok,
                "decisive_in_source": in_source,
                "decisive_in_injected": in_injected,
                "extractive_answer_ok": answer_ok,
                "pass": passed,
                "mrl": mrl,
                "failure_category": fail_cat,
                "residual": residual,
                "used_files": list(ctx.used_files or ()),
                "retrieval_mode": ctx.retrieval_mode,
                "fallback_reason": ctx.fallback_reason,
                "response_evidence_mode": (evidence or {}).get("retrieval_mode"),
                "response_evidence_pages": pages_in_evidence,
                "citation_observable": citation_observable,
                "citation_page_accuracy": citation_ok,
                "gold_page_markers_in_injected": gold_page_markers_in_injected,
                "injected_chars": ctx.chars,
                "injected_truncated": ctx.truncated,
                "injected_block_chars": len(injected),
                "latency_ms": latency_ms,
                "false_attractor_in_injected": unanswerable_false_positive(question, injected),
            }
        )

    answerable = [r for r in rows_out if r["answerable"]]
    unanswerable = [r for r in rows_out if not r["answerable"]]
    mrl_rows = [r for r in rows_out if r["mrl"]]
    frontier: dict[str, int] = {k: 0 for k in FAILURE_CATEGORIES}
    for r in rows_out:
        if r["failure_category"]:
            frontier[r["failure_category"]] += 1

    citation_scored = [r for r in rows_out if r["citation_observable"] and r["answerable"]]
    page_marker_scored = [r for r in answerable if r["gold_page_markers_in_injected"] is not None]

    summary = {
        "questions": len(rows_out),
        "answerable": len(answerable),
        "unanswerable": len(unanswerable),
        "answer_correctness": f"{sum(1 for r in rows_out if r['pass'])}/{len(rows_out)}",
        "answer_correctness_n": sum(1 for r in rows_out if r["pass"]),
        "decisive_span_recall": (
            f"{sum(1 for r in answerable if r['decisive_in_injected'])}/{len(answerable)}"
            if answerable
            else "n/a"
        ),
        "decisive_span_recall_n": sum(1 for r in answerable if r["decisive_in_injected"]),
        "citation_page_accuracy": (
            "not_observable_on_prefix_fallback"
            if not citation_scored
            else f"{sum(1 for r in citation_scored if r['citation_page_accuracy'])}/{len(citation_scored)}"
        ),
        "gold_page_marker_in_injected": (
            f"{sum(1 for r in answerable if r['gold_page_markers_in_injected'])}/{len(answerable)}"
        ),
        "unanswerable_precision": (
            f"{sum(1 for r in unanswerable if r['pass'])}/{len(unanswerable)}"
            if unanswerable
            else "n/a"
        ),
        "unanswerable_false_attractor_in_prefix": sum(
            1 for r in unanswerable if r["false_attractor_in_injected"]
        ),
        "mrl": len(mrl_rows),
        "mrl_rate": round(100.0 * len(mrl_rows) / len(rows_out), 1) if rows_out else 0.0,
        "failure_frontier": {k: v for k, v in frontier.items() if v},
        "retrieval_mode_observed": rows_out[0]["retrieval_mode"] if rows_out else None,
        "per_file_max_chars": PER_FILE_MAX_CHARS,
        "extracted_chars": extracted["extracted_chars"],
        "prefix_chars": len(extracted["prefix_text"]),
        "source_page_count": extracted["source_page_count"],
        "parse_ms": extracted["parse_ms"],
        "mean_inject_latency_ms": round(
            sum(r["latency_ms"] for r in rows_out) / len(rows_out), 2
        )
        if rows_out
        else None,
    }
    return {
        "extraction": {
            k: v
            for k, v in extracted.items()
            if k not in {"extracted_text", "prefix_text", "pages"}
        },
        "prefix_preview": extracted["prefix_text"][:400],
        "summary": summary,
        "rows": rows_out,
        "chunk_retrieval_note": (
            "FTS/chunk retrieval was not enabled. This measures Gate 3D prefix_fallback."
        ),
    }


def _stable_for_commit(result: dict[str, Any]) -> dict[str, Any]:
    """Drop machine-timing fields so the committed fixture is reproducible."""
    payload = json.loads(json.dumps(result, default=str))
    extraction = payload.get("extraction") or {}
    extraction.pop("parse_ms", None)
    payload["extraction"] = extraction
    summary = payload.get("summary") or {}
    summary.pop("parse_ms", None)
    summary.pop("mean_inject_latency_ms", None)
    payload["summary"] = summary
    for row in payload.get("rows") or []:
        row.pop("latency_ms", None)
    return payload


def write_reports(result: dict[str, Any], dest_dir: Path, *, stable: bool = False) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    payload = _stable_for_commit(result) if stable else result
    json_path = dest_dir / "gate_m_baseline.json"
    json_path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    s = payload["summary"]
    md = [
        "# Gate M baseline",
        "",
        f"- Questions: {s['questions']}",
        f"- Answer correctness (extractive from injected context): {s['answer_correctness']}",
        f"- Decisive-span recall: {s['decisive_span_recall']}",
        f"- Citation/page accuracy: {s['citation_page_accuracy']}",
        f"- Gold page marker in injected prefix: {s['gold_page_marker_in_injected']}",
        f"- Unanswerable precision (source does not contain the requested fact): {s['unanswerable_precision']}",
        f"- MRL: {s['mrl']} ({s['mrl_rate']}%)",
        f"- Observed retrieval mode: {s['retrieval_mode_observed']}",
        f"- PER_FILE_MAX_CHARS: {s['per_file_max_chars']}",
        f"- Extracted chars: {s['extracted_chars']}",
        f"- Prefix chars: {s['prefix_chars']}",
    ]
    if s.get("mean_inject_latency_ms") is not None:
        md.append(f"- Mean inject latency_ms: {s['mean_inject_latency_ms']}")
    md.extend(
        [
            "",
            "## Failure frontier",
            "",
        ]
    )
    if s["failure_frontier"]:
        for k, v in sorted(s["failure_frontier"].items(), key=lambda kv: -kv[1]):
            md.append(f"- {k}: {v}")
    else:
        md.append("- none")
    md.append("")
    md.append("Answer correctness is extractive recoverability from the injected prefix,")
    md.append("not an LLM grade. Prefix_fallback evidence units do not carry page numbers.")
    (dest_dir / "gate_m_baseline.md").write_text("\n".join(md) + "\n", encoding="utf-8")
