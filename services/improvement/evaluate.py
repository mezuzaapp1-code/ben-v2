"""Compare a candidate FTS run against the committed Gate P FTS baseline."""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any, Mapping

_FRAC = re.compile(r"^(\d+)\s*/\s*(\d+)$")

COMMITTED_BASELINE = {
    "mode": "BEN_EXISTING_FTS",
    "answer_correctness": "48/50",
    "decisive_span_recall": "42/43",
    "unanswerable_precision": "7/7",
    "mrl": 1,
}

FTS_TIMEOUT_MS = 200.0
HARDCODE_NEEDLES = (
    "m09",
    "hamelacha",
    "ha-melacha",
    "12 hamelacha",
    "ben-gold-sa",
    "named delivery place is 12",
)

ISOLATION_TESTS = (
    "tests/test_document_intelligence_gate4a.py::test_same_filename_across_workspaces_no_leak",
    "tests/test_document_intelligence_gate4a.py::test_cross_org_isolation",
)


def parse_frac(value: Any) -> tuple[int, int] | None:
    if value is None:
        return None
    m = _FRAC.match(str(value).strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def frac_decreased(candidate: Any, baseline: Any) -> bool:
    c = parse_frac(candidate)
    b = parse_frac(baseline)
    if not c or not b:
        return True
    if c[1] != b[1]:
        return True
    return c[0] < b[0]


def mean(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 3)


def scan_hardcoding(paths: list[Path]) -> dict[str, Any]:
    hits: list[dict[str, str]] = []
    for path in paths:
        if not path.is_file():
            continue
        body = path.read_text(encoding="utf-8").casefold()
        for needle in HARDCODE_NEEDLES:
            if needle in body:
                hits.append({"path": str(path), "needle": needle})
    return {"scanned": [str(p) for p in paths], "hits": hits, "ok": not hits}


def _row_for_query(rows: list[Mapping[str, Any]], user_query: str) -> Mapping[str, Any] | None:
    target = " ".join((user_query or "").split()).casefold()
    for row in rows:
        q = " ".join(str(row.get("question") or "").split()).casefold()
        if q == target:
            return row
    return None


def targeted_from_rows(
    rows: list[Mapping[str, Any]],
    *,
    user_query: str,
    expected_evidence: str,
) -> dict[str, Any]:
    row = _row_for_query(list(rows), user_query)
    if row is None:
        return {"ok": False, "reason": "no_benchmark_row_for_query"}
    injected_ok = bool(row.get("decisive_in_injected"))
    answer_ok = bool(row.get("extractive_answer_ok") or row.get("pass"))
    pages = list(row.get("evidence_pages") or [])
    return {
        "ok": bool(injected_ok and answer_ok),
        "decisive_in_injected": injected_ok,
        "extractive_answer_ok": answer_ok,
        "evidence_pages": pages,
        "retrieval_mode": row.get("retrieval_mode"),
        "matched_on": "user_query",
        "expected_evidence": expected_evidence,
    }


def latency_stats(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    fts = [float(r["fts_latency_ms"]) for r in rows if isinstance(r.get("fts_latency_ms"), (int, float))]
    wall = [float(r["latency_ms"]) for r in rows if isinstance(r.get("latency_ms"), (int, float))]
    return {
        "mean_fts_latency_ms": mean(fts),
        "max_fts_latency_ms": max(fts) if fts else None,
        "mean_wall_latency_ms": mean(wall),
        "n_fts": len(fts),
    }


def compare_candidate(
    *,
    summary: Mapping[str, Any],
    rows: list[Mapping[str, Any]],
    control_latency: Mapping[str, Any] | None,
    targeted: Mapping[str, Any],
    isolation: Mapping[str, Any],
    hardcoding: Mapping[str, Any],
    http_errors: int = 0,
) -> tuple[str, list[str], dict[str, Any]]:
    """Return (decision, regressions, latency_delta). PASS only if every gate holds."""
    regressions: list[str] = []
    base = COMMITTED_BASELINE
    if frac_decreased(summary.get("answer_correctness"), base["answer_correctness"]):
        regressions.append(
            f"correctness {summary.get('answer_correctness')} < {base['answer_correctness']}"
        )
    if frac_decreased(summary.get("decisive_span_recall"), base["decisive_span_recall"]):
        regressions.append(
            f"decisive recall {summary.get('decisive_span_recall')} < {base['decisive_span_recall']}"
        )
    if str(summary.get("unanswerable_precision")) != base["unanswerable_precision"]:
        regressions.append(
            f"unanswerable {summary.get('unanswerable_precision')} != {base['unanswerable_precision']}"
        )
    if not targeted.get("ok"):
        regressions.append("lexical-class targeted span not recovered")
    if isolation.get("ok") is not True:
        regressions.append(f"isolation tests failed: {isolation.get('reason') or isolation}")
    if hardcoding.get("ok") is not True:
        regressions.append(f"benchmark-specific hardcoding: {hardcoding.get('hits')}")
    if http_errors:
        regressions.append(f"new 5xx/error behavior: {http_errors}")

    cand_lat = latency_stats(rows)
    control_mean = (control_latency or {}).get("mean_fts_latency_ms")
    cand_mean = cand_lat.get("mean_fts_latency_ms")
    cand_max = cand_lat.get("max_fts_latency_ms")
    if cand_max is not None and cand_max > FTS_TIMEOUT_MS:
        regressions.append(f"fts latency {cand_max}ms exceeds {FTS_TIMEOUT_MS}ms timeout")
    if cand_mean is not None and control_mean:
        if cand_mean > max(2.0 * float(control_mean), float(control_mean) + 50.0):
            regressions.append(
                f"mean fts latency {cand_mean}ms exceeds 2× control {control_mean}ms"
            )

    used = []
    for row in rows:
        names = []
        for item in row.get("used_files") or []:
            if isinstance(item, dict):
                names.append(str(item.get("name") or item.get("id") or ""))
            else:
                names.append(str(item))
        used.extend(names)
    unexpected = sorted({n for n in used if n and "gold" not in n.casefold() and n not in {"ben_gold_supply_agreement.pdf"}})
    # Isolated FTS uses only the gold file; any extra name is source expansion.
    gold_ok = {"ben_gold_supply_agreement.pdf"}
    extra_sources = sorted({n for n in used if n and n not in gold_ok})
    if extra_sources:
        regressions.append(f"unrelated source expansion: {extra_sources}")

    latency_delta = {
        "candidate": cand_lat,
        "control": control_latency or {},
        "timeout_ms": FTS_TIMEOUT_MS,
        "unexpected_files": unexpected,
    }
    decision = "PASS" if not regressions else "REJECT"
    return decision, regressions, latency_delta


def assert_no_eval(path: Path) -> None:
    """Cheap static check that candidate modules do not eval user text."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"eval", "exec"}:
            raise AssertionError(f"{path} calls {node.func.id}")
