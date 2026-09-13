"""Gate P comparison report. No production changes."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tests.gate_p.capability import capability_matrix

MODE_ORDER = [
    "BEN_PREFIX_2000",
    "BEN_EXISTING_FTS",
    "OPENAI_NATIVE_DOCUMENT",
    "OPENAI_FILE_SEARCH",
    "CLAUDE_NATIVE_DOCUMENT",
    "CLAUDE_PROVIDER_RETRIEVAL",
    "GEMINI_NATIVE_PDF",
    "GEMINI_FILE_SEARCH",
    "GROK_NATIVE_DOCUMENT",
]


def _cell(summary: dict[str, Any], key: str) -> str:
    if summary.get("status") == "blocked":
        if key in {"mode"}:
            return str(summary.get(key) or "")
        if key == "answer_correctness":
            return "BLOCKED"
        return "—"
    val = summary.get(key)
    if val is None:
        return "—"
    return str(val)


def comparison_rows(modes: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for name in MODE_ORDER:
        payload = modes.get(name) or {}
        s = payload.get("summary") or {"mode": name, "status": "blocked"}
        rows.append(
            {
                "mode": name,
                "status": s.get("status"),
                "answer_correctness": _cell(s, "answer_correctness"),
                "decisive_recall": _cell(s, "decisive_span_recall"),
                "mrl": _cell(s, "mrl"),
                "unanswerable": _cell(s, "unanswerable_precision"),
                "early": _cell(s, "early"),
                "middle": _cell(s, "middle"),
                "late": _cell(s, "late"),
                "exceptions": _cell(s, "exceptions"),
                "multi_hop": _cell(s, "multi_hop"),
                "global": _cell(s, "global"),
                "latency": _cell(s, "mean_latency_ms"),
                "tokens": (
                    "—"
                    if s.get("status") == "blocked"
                    else f"{s.get('input_tokens') or 0}/{s.get('output_tokens') or 0}"
                ),
                "cost": _cell(s, "approx_cost_usd"),
                "blocked_reason": s.get("blocked_reason"),
            }
        )
    return rows


def classify_strategy(name: str, summary: dict[str, Any]) -> str:
    status = summary.get("status")
    if summary.get("not_applicable"):
        return "REJECT"
    if status == "blocked":
        return "BLOCKED"
    if name == "CLAUDE_PROVIDER_RETRIEVAL":
        return "REJECT"
    if name == "BEN_PREFIX_2000":
        return "FALLBACK"
    if name == "BEN_EXISTING_FTS":
        mrl = summary.get("mrl")
        late = summary.get("late") or ""
        if isinstance(mrl, int) and mrl == 0:
            return "USE"
        if late not in {"n/a", "—"} and late.startswith("0/"):
            return "ADAPT"
        return "USE"
    if "NATIVE" in name or name == "GEMINI_NATIVE_PDF":
        return "ESCALATION"
    if "FILE_SEARCH" in name:
        return "ADAPT"
    return "ADAPT"


def markdown_report(result: dict[str, Any]) -> str:
    lines = ["# Gate P baseline", ""]
    lines.append(f"- Status: {result.get('gate_status')}")
    lines.append(f"- Gold: BEN Gold Supply Agreement, 18 pages, 50 questions")
    lines.append("")
    lines.append("## Capability matrix")
    lines.append("")
    for row in capability_matrix():
        lines.append(f"### {row['provider']}")
        lines.append(f"- BEN integration: {row['current_ben_integration']}")
        lines.append(f"- Direct PDF: {row['direct_pdf_document_input']}")
        lines.append(f"- Provider retrieval: {row['provider_retrieval_file_search']}")
        lines.append(f"- Citations: {row['citation_evidence_support']}")
        lines.append(f"- Credentials usable: {row['existing_credentials_usable']}")
        lines.append(f"- Safe to benchmark now: {row['safe_to_benchmark_now']}")
        if row.get("blocked_reason"):
            lines.append(f"- Blocked: {row['blocked_reason']}")
        lines.append("")
    lines.append("## Comparison")
    lines.append("")
    lines.append(
        "| Mode | Answer | Recall | MRL | Unans | Early | Middle | Late | Exc | Multi | Global | Latency | Tokens | Cost |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for row in result.get("comparison") or []:
        lines.append(
            "| {mode} | {answer_correctness} | {decisive_recall} | {mrl} | {unanswerable} | {early} | {middle} | {late} | {exceptions} | {multi_hop} | {global} | {latency} | {tokens} | {cost} |".format(
                **row
            )
        )
    lines.append("")
    rec = result.get("recommendation") or {}
    for key, val in rec.items():
        lines.append(f"- {key}: {val}")
    lines.append("")
    return "\n".join(lines)


def _stable(result: dict[str, Any]) -> dict[str, Any]:
    payload = json.loads(json.dumps(result, default=str))
    for mode in (payload.get("modes") or {}).values():
        summary = (mode or {}).get("summary") or {}
        summary.pop("mean_latency_ms", None)
        mode["summary"] = summary
    for rows in (payload.get("mode_rows") or {}).values():
        for row in rows or []:
            row.pop("latency_ms", None)
            row.pop("fts_latency_ms", None)
    for row in payload.get("comparison") or []:
        if row.get("status") == "measured":
            row["latency"] = "local"
    return payload


def write_reports(result: dict[str, Any], dest: Path, *, stable: bool = False) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    payload = _stable(result) if stable else json.loads(json.dumps(result, default=str))
    (dest / "gate_p_baseline.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8"
    )
    (dest / "gate_p_baseline.md").write_text(markdown_report(payload), encoding="utf-8")
    (dest / "capability_matrix.json").write_text(
        json.dumps({"matrix": capability_matrix()}, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
