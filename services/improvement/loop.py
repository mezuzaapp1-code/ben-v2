"""I1 loop: classify → contract → ≤3 candidates → PASS/REJECT/NEEDS_REVIEW.

Runs only in-process against isolated FTS. Never merges, deploys, or flips
production flags. Stops on first PASS, three REJECTs, or architecture need.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from services.improvement.capabilities import audit_static, probe_postgres, report as capability_report
from services.improvement.classify import classify_and_apply
from services.improvement.contract import contract_for
from services.improvement.evaluate import (
    COMMITTED_BASELINE,
    ISOLATION_TESTS,
    compare_candidate,
    latency_stats,
    scan_hardcoding,
    targeted_from_rows,
)
from services.improvement.schema import FailureRecord
from services.workspace_files.query_expansion import extra_tsquery_atoms

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "gate_i1"
GATE_P_BASELINE = ROOT / "tests" / "fixtures" / "gate_p" / "gate_p_baseline.json"
EXPAND_PATH = ROOT / "services" / "workspace_files" / "query_expansion.py"
RETRIEVER_PATH = ROOT / "services" / "workspace_files" / "chunk_retriever.py"

CANDIDATES = (
    {
        "candidate_id": "C1_PREFIX",
        "expand_mode": "prefix",
        "change_summary": (
            "Append expander-generated stem:* atoms to the existing simple OR "
            "tsquery. One FTS round-trip. Same org/workspace/file filters."
        ),
    },
    {
        "candidate_id": "C2_VARIANTS",
        "expand_mode": "variants",
        "change_summary": (
            "Append exact inflection stems without prefix matching. Same query "
            "prep module; no :* operator."
        ),
    },
    {
        "candidate_id": "C3_SECOND_QUERY",
        "expand_mode": "needs_review",
        "change_summary": (
            "A second FTS round-trip / hit-union when the first query returns "
            "non-decisive chunks. Requires retrieval orchestration beyond query "
            "prep — out of I1 allowed modules if C1/C2 fail."
        ),
    },
)

MAX_CANDIDATES = 3


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _fts_baseline_rows(blob: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = (blob.get("mode_rows") or {}).get("BEN_EXISTING_FTS") or []
    if rows:
        return list(rows)
    nested = (blob.get("modes") or {}).get("BEN_EXISTING_FTS") or {}
    return list(nested.get("rows") or [])


def load_fts_baseline() -> dict[str, Any]:
    payload = json.loads(GATE_P_BASELINE.read_text(encoding="utf-8"))
    modes = payload.get("modes") or {}
    fts = modes.get("BEN_EXISTING_FTS") or {}
    return {
        "summary": fts.get("summary") or {},
        "rows": _fts_baseline_rows(payload),
    }


def seed_failure_from_baseline(*, task_id: str = "M09") -> FailureRecord:
    """Select the first-case row. Classifier must still ignore task_id."""
    blob = json.loads(GATE_P_BASELINE.read_text(encoding="utf-8"))
    rows = _fts_baseline_rows(blob)
    row = next((r for r in rows if str(r.get("question_id")) == task_id), None)
    if row is None:
        raise RuntimeError(f"Gate P FTS baseline missing seed task {task_id}")
    expected = ""
    # Prefer locator text from gold if present on the row; else gold answer.
    expected = str(row.get("gold_answer") or "")
    # The FTS row does not store the locator; look it up by query text only via gold.
    from tests.gate_m.gold_questions import QUESTIONS

    gold = next((q for q in QUESTIONS if q["question"] == row.get("question")), None)
    locator = ""
    if gold:
        spans = gold.get("decisive_evidence") or []
        if spans:
            locator = str(spans[0].get("text_or_locator") or "")
        expected = locator or str(gold.get("gold_answer") or expected)
    retrieved = f"pages={row.get('evidence_pages')}"
    return FailureRecord(
        failure_id=f"i1-{task_id.lower()}-fts",
        benchmark_id="gate_p_ben_existing_fts",
        task_id=str(row.get("question_id") or task_id),
        failure_class="OTHER",
        user_query=str(row.get("question") or ""),
        expected_evidence=expected,
        retrieved_evidence=retrieved,
        actual_answer="",
        expected_answer=str(row.get("gold_answer") or ""),
        source_files=[
            (item.get("name") if isinstance(item, dict) else str(item))
            for item in (row.get("used_files") or [])
        ],
        retrieval_mode=str(row.get("retrieval_mode") or "chunks"),
        timestamp=_now(),
        severity="medium",
        security_relevant=False,
        repeat_count=1,
        prior_label=str(row.get("failure_category") or "") or None,
        retrieved_empty=int(row.get("chunks_selected") or 0) == 0,
        evidence_pages=[int(p) for p in (row.get("evidence_pages") or [])],
        notes="Seeded from committed Gate P FTS baseline; classifier ignores task_id.",
    )


def _set_expand(mode: str) -> str | None:
    prev = os.environ.get("BEN_FTS_LEXICAL_EXPAND")
    if mode in {"", "off"}:
        os.environ.pop("BEN_FTS_LEXICAL_EXPAND", None)
    else:
        os.environ["BEN_FTS_LEXICAL_EXPAND"] = mode
    return prev


def _restore_expand(prev: str | None) -> None:
    if prev is None:
        os.environ.pop("BEN_FTS_LEXICAL_EXPAND", None)
    else:
        os.environ["BEN_FTS_LEXICAL_EXPAND"] = prev


async def _run_fts() -> dict[str, Any]:
    from tests.gate_m.pdf_builder import make_multiline_pdf
    from tests.gate_m.gold_document import PAGES
    from tests.gate_p.fts_isolated import run_fts_isolated
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        pdf = Path(tmp) / "gold.pdf"
        pdf.write_bytes(make_multiline_pdf(PAGES))
        return await run_fts_isolated(pdf)


def _run_isolation(mode: str) -> dict[str, Any]:
    env = os.environ.copy()
    env["BEN_FTS_LEXICAL_EXPAND"] = mode
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *ISOLATION_TESTS],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": (proc.stdout or "")[-2000:],
        "stderr": (proc.stderr or "")[-2000:],
        "tests": list(ISOLATION_TESTS),
        "expand_mode": mode,
    }


def _architecture_needed(problem_class: str) -> bool:
    return problem_class not in {"LEXICAL_VARIATION"}


async def run_loop(*, seed_task_id: str = "M09") -> dict[str, Any]:
    failure = classify_and_apply(seed_failure_from_baseline(task_id=seed_task_id))
    contract = contract_for(failure.failure_class)
    probe = await probe_postgres()
    caps = capability_report(audit_static(), probe)

    result: dict[str, Any] = {
        "gate": "I1",
        "timestamp": _now(),
        "production_changed": False,
        "merged": False,
        "deployed": False,
        "failure_record": failure.to_dict(),
        "fix_contract": contract.to_dict(),
        "capabilities": caps,
        "committed_baseline": COMMITTED_BASELINE,
        "candidates": [],
        "pattern": "generic_query_prep_prefix",
        "files_changed": [
            "services/workspace_files/query_expansion.py",
            "services/workspace_files/chunk_retriever.py",
            "services/improvement/",
            "scripts/run_gate_i1.py",
            "tests/test_gate_i1_improvement_loop.py",
            "tests/test_query_expansion.py",
            "tests/fixtures/gate_i1/",
        ],
    }

    if _architecture_needed(failure.failure_class):
        result["gate_status"] = "NEEDS_REVIEW"
        result["decision"] = "NEEDS_REVIEW"
        result["stop_reason"] = (
            f"classified {failure.failure_class}; lexical query-prep cannot "
            "honestly claim this class. Higher-complexity architecture required."
        )
        result["recommendation"] = (
            "Do not implement a lexical expander for this class. Open a human "
            "review for ranking / architecture. Do not merge. Do not deploy."
        )
        return result

    hardcoding = scan_hardcoding([EXPAND_PATH, RETRIEVER_PATH])
    prev = _set_expand("off")
    try:
        control = await _run_fts()
    finally:
        _restore_expand(prev)
    control_lat = latency_stats(control.get("rows") or [])
    result["control"] = {
        "summary": control.get("summary"),
        "latency": control_lat,
        "expand_mode": lexical_expand_mode() if False else "off",
    }

    tried = 0
    final_decision = "REJECT"
    stop_reason = "three candidates exhausted"
    for spec in CANDIDATES:
        if tried >= MAX_CANDIDATES:
            break
        mode = spec["expand_mode"]
        if mode == "needs_review":
            cand = {
                "candidate_id": spec["candidate_id"],
                "change_summary": spec["change_summary"],
                "expand_mode": mode,
                "benchmark_result": {},
                "latency_delta": {},
                "regressions": [
                    "solving the residual would require a second FTS round-trip "
                    "and hit union (beyond query-prep scope)"
                ],
                "decision": "NEEDS_REVIEW",
                "notes": "Not executed. Adaptive stop: higher-complexity architecture.",
            }
            result["candidates"].append(cand)
            final_decision = "NEEDS_REVIEW"
            stop_reason = "higher-complexity architecture required for C3"
            break

        tried += 1
        prev = _set_expand(mode)
        try:
            measured = await _run_fts()
            isolation = _run_isolation(mode)
        finally:
            _restore_expand(prev)

        summary = measured.get("summary") or {}
        rows = measured.get("rows") or []
        targeted = targeted_from_rows(
            rows,
            user_query=failure.user_query,
            expected_evidence=failure.expected_evidence,
        )
        # Extra generic check: expander must not mention the seed task id.
        if seed_task_id.lower() in EXPAND_PATH.read_text(encoding="utf-8").casefold():
            hardcoding = {
                **hardcoding,
                "ok": False,
                "hits": list(hardcoding.get("hits") or [])
                + [{"path": str(EXPAND_PATH), "needle": seed_task_id.lower()}],
            }
        decision, regressions, latency_delta = compare_candidate(
            summary=summary,
            rows=rows,
            control_latency=control_lat,
            targeted=targeted,
            isolation=isolation,
            hardcoding=hardcoding,
        )
        sample_tokens = extra_tsquery_atoms(
            __import__(
                "services.workspace_files.chunk_retriever", fromlist=["normalize_query_tokens"]
            ).normalize_query_tokens(failure.user_query),
            mode=mode,
        )
        cand = {
            "candidate_id": spec["candidate_id"],
            "change_summary": spec["change_summary"],
            "expand_mode": mode,
            "extra_atoms_for_seed_query": sample_tokens,
            "benchmark_result": {
                "answer_correctness": summary.get("answer_correctness"),
                "decisive_span_recall": summary.get("decisive_span_recall"),
                "unanswerable_precision": summary.get("unanswerable_precision"),
                "mrl": summary.get("mrl"),
                "failure_frontier": summary.get("failure_frontier"),
                "status": summary.get("status"),
            },
            "latency_delta": latency_delta,
            "regressions": regressions,
            "decision": decision,
            "targeted": targeted,
            "isolation": {
                "ok": isolation.get("ok"),
                "returncode": isolation.get("returncode"),
                "tests": isolation.get("tests"),
            },
            "hardcoding": hardcoding,
        }
        result["candidates"].append(cand)
        if decision == "PASS":
            final_decision = "PASS"
            stop_reason = f"{spec['candidate_id']} passed all regression gates"
            break
        if tried >= MAX_CANDIDATES:
            final_decision = "REJECT"
            stop_reason = "three candidates failed the regression contract"
            break

    result["decision"] = final_decision
    result["gate_status"] = final_decision
    result["stop_reason"] = stop_reason
    if final_decision == "PASS":
        result["recommendation"] = (
            "Candidate is eligible for a human-reviewed implementation gate. "
            "Keep BEN_FTS_LEXICAL_EXPAND default off. Do not merge this branch "
            "as production. Do not deploy. Do not change Railway FTS flags. "
            "Next gate: reviewed enablement of prefix expansion on a canary "
            "workspace after security sign-off."
        )
    elif final_decision == "NEEDS_REVIEW":
        result["recommendation"] = (
            "Stop. The measured miss needs a ranking or dual-query architecture "
            "beyond I1. Do not add embeddings. Do not merge. Do not deploy."
        )
    else:
        result["recommendation"] = (
            "No candidate met the regression contract. Leave expansion off. "
            "Do not merge. Do not deploy."
        )
    result["pattern_generic"] = hardcoding.get("ok", False) and final_decision in {
        "PASS",
        "REJECT",
        "NEEDS_REVIEW",
    }
    return result


def write_fixtures(payload: Mapping[str, Any], dest: Path | None = None) -> Path:
    dest = dest or FIXTURE_DIR
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "i1_result.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (dest / "failure_record.json").write_text(
        json.dumps(payload.get("failure_record") or {}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (dest / "fix_contract.json").write_text(
        json.dumps(payload.get("fix_contract") or {}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (dest / "capabilities_audit.json").write_text(
        json.dumps(payload.get("capabilities") or {}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return dest / "i1_result.json"
