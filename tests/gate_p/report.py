"""Assemble Gate P reports without changing production flags."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _cell(summary: dict[str, Any], key: str) -> str:
    value = summary.get(key)
    if value is None:
        return "n/a"
    return str(value)


def build_markdown(payload: dict[str, Any]) -> str:
    modes = payload.get("modes") or {}
    matrix = payload.get("capability_matrix") or []
    lines = [
        "# GATE P — Provider File Intelligence Benchmark",
        "",
        f"GATE P STATUS: {payload.get('gate_p_status')}",
        "",
        "Measurement only. Production retrieval, PER_FILE_MAX_CHARS, chunk FTS,",
        "embeddings, and provider adapters were not changed.",
        "",
        "## PROVIDER CAPABILITY MATRIX",
        "",
        "| Provider | Current BEN integration | Direct PDF | Provider retrieval | Citations | Credentials usable? | Extra setup? | Safe now? |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in matrix:
        lines.append(
            "| {provider} | {current_ben_integration} | {direct_pdf_document_input} | "
            "{provider_retrieval_file_search} | {citation_evidence_support} | "
            "{existing_credentials_usable} | {additional_setup_required} | "
            "{safe_to_benchmark_now} |".format(**{k: str(row.get(k, "")).replace("|", "/") for k in row})
        )
    lines.extend(["", "## MODE RESULTS", ""])
    order = [
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
    for mode in order:
        item = modes.get(mode) or {}
        status = item.get("status", "MISSING")
        summary = item.get("summary") or {}
        lines.append(f"### {mode}")
        lines.append("")
        lines.append(f"- Status: {status}")
        if item.get("blocked_reason"):
            lines.append(f"- Blocked reason: {item['blocked_reason']}")
        if item.get("model"):
            lines.append(f"- Model: {item['model']}")
        lines.append(f"- Answer correctness: {_cell(summary, 'answer_correctness')}")
        lines.append(f"- Decisive-span recall: {_cell(summary, 'decisive_span_recall')}")
        lines.append(f"- MRL: {_cell(summary, 'mrl')} ({_cell(summary, 'mrl_rate')}%)")
        lines.append(f"- Unanswerable precision: {_cell(summary, 'unanswerable_precision')}")
        lines.append(f"- Early: {_cell(summary, 'early')}")
        lines.append(f"- Middle: {_cell(summary, 'middle')}")
        lines.append(f"- Late: {_cell(summary, 'late')}")
        lines.append(f"- Exceptions: {_cell(summary, 'exceptions')}")
        lines.append(f"- Multi-hop: {_cell(summary, 'multi_hop')}")
        lines.append(f"- Global: {_cell(summary, 'global')}")
        lines.append(f"- Table: {_cell(summary, 'table')}")
        lines.append(f"- Latency: {_cell(summary, 'mean_latency_ms')} ms mean")
        lines.append(f"- Tokens in/out: {_cell(summary, 'input_tokens')} / {_cell(summary, 'output_tokens')}")
        lines.append(f"- Approx cost: {_cell(summary, 'approx_cost_usd')}")
        if summary.get("failure_frontier"):
            lines.append(f"- Failure frontier: {summary['failure_frontier']}")
        lines.append("")

    lines.extend(
        [
            "## COMPARISON TABLE",
            "",
            "| Mode | Answer Correctness | Decisive Recall | MRL | Unanswerable | Early | Middle | Late | Exceptions | Multi-hop | Global | Latency | Tokens | Cost |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
        ]
    )
    for mode in order:
        item = modes.get(mode) or {}
        s = item.get("summary") or {}
        tokens = "n/a"
        if s.get("input_tokens") is not None:
            tokens = f"{s.get('input_tokens')}/{s.get('output_tokens')}"
        lines.append(
            "| {mode} | {ac} | {dr} | {mrl} | {ua} | {early} | {mid} | {late} | {ex} | {mh} | {gl} | {lat} | {tok} | {cost} |".format(
                mode=mode,
                ac=_cell(s, "answer_correctness"),
                dr=_cell(s, "decisive_span_recall"),
                mrl=f"{_cell(s, 'mrl')} ({_cell(s, 'mrl_rate')}%)",
                ua=_cell(s, "unanswerable_precision"),
                early=_cell(s, "early"),
                mid=_cell(s, "middle"),
                late=_cell(s, "late"),
                ex=_cell(s, "exceptions"),
                mh=_cell(s, "multi_hop"),
                gl=_cell(s, "global"),
                lat=_cell(s, "mean_latency_ms"),
                tok=tokens,
                cost=_cell(s, "approx_cost_usd"),
            )
        )
    verdict = payload.get("verdict") or {}
    lines.extend(["", "## VERDICT", ""])
    for key in (
        "best_overall",
        "best_retrieval",
        "best_full_document_reasoning",
        "best_cost_performance",
        "best_exception_detection",
        "document_position_effect",
        "important_finding",
        "ben_architectural_implication",
        "should_gate_d_still_proceed",
        "why",
        "smallest_next_step",
        "residuals",
        "classifications",
    ):
        label = key.replace("_", " ").upper()
        value = verdict.get(key)
        if isinstance(value, (dict, list)):
            lines.append(f"**{label}:**")
            lines.append("")
            lines.append("```json")
            lines.append(json.dumps(value, indent=2))
            lines.append("```")
        else:
            lines.append(f"**{label}:** {value}")
        lines.append("")
    lines.extend(
        [
            "## FILES CREATED/MODIFIED",
            "",
        ]
    )
    for path in payload.get("files") or []:
        lines.append(f"- `{path}`")
    lines.extend(
        [
            "",
            "## TESTS",
            "",
            payload.get("tests") or "see runner output",
            "",
            "STOP.",
            "",
        ]
    )
    return "\n".join(lines)


def write_reports(payload: dict[str, Any], dest_dir: Path) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    (dest_dir / "gate_p_results.json").write_text(
        json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8"
    )
    (dest_dir / "GATE_P_REPORT.md").write_text(build_markdown(payload) + "\n", encoding="utf-8")
