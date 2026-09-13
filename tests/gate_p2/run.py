"""Gate P2 runner: native PDF only, fail-closed without injectable keys."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tests.gate_p2.credentials import assert_no_secret_values, probe

BLOCKED_PROVIDER = {
    "model": "not_run",
    "correct": "BLOCKED",
    "unanswerable": "BLOCKED",
    "early": "BLOCKED",
    "middle": "BLOCKED",
    "late": "BLOCKED",
    "exceptions": "BLOCKED",
    "multi_hop": "BLOCKED",
    "global": "BLOCKED",
    "tables": "BLOCKED",
    "citations": "BLOCKED",
    "latency": "BLOCKED",
    "tokens": "BLOCKED",
    "cost": "BLOCKED",
    "DOCUMENT_AVAILABLE_TO_MODEL": "NOT_RUN",
    "FINAL_ANSWER_QUALITY": "NOT_RUN",
}


def _blocked_reason(probe_result: dict[str, Any]) -> str:
    railway = probe_result.get("railway") or {}
    return (
        "Provider keys are not present in this process, Cursor did not inject them, "
        f"and Railway cannot supply them "
        f"(me={railway.get('me_error')}; project_token={railway.get('project_token_error')}). "
        "No secrets were requested in chat. File Search was not used."
    )


def build_blocked_report(probe_result: dict[str, Any]) -> dict[str, Any]:
    reason = _blocked_reason(probe_result)
    providers = {
        "OPENAI": {**BLOCKED_PROVIDER, "blocked_reason": "OPENAI_API_KEY KEY_PRESENT=false"},
        "CLAUDE": {**BLOCKED_PROVIDER, "blocked_reason": "ANTHROPIC_API_KEY KEY_PRESENT=false"},
        "GEMINI": {
            **BLOCKED_PROVIDER,
            "blocked_reason": "GOOGLE_API_KEY/GEMINI_API_KEY KEY_PRESENT=false",
        },
        "GROK": {
            **BLOCKED_PROVIDER,
            "blocked_reason": (
                "XAI_API_KEY KEY_PRESENT=false; Responses/file path not entered"
            ),
        },
    }
    return {
        "gate_p2_status": "BLOCKED",
        "secure_environment": {
            "KEY_PRESENT": probe_result.get("KEY_PRESENT"),
            "cursor_injected_secret_names": probe_result.get("cursor_injected_secret_names"),
            "railway": {
                "RAILWAY_TOKEN": (probe_result.get("railway") or {}).get("RAILWAY_TOKEN"),
                "can_inject_provider_keys": (probe_result.get("railway") or {}).get(
                    "can_inject_provider_keys"
                ),
                "me_error": (probe_result.get("railway") or {}).get("me_error"),
                "project_token_error": (probe_result.get("railway") or {}).get(
                    "project_token_error"
                ),
            },
            "safe_injection_possible": False,
            "action": "STOP",
            "reason": reason,
        },
        "providers": providers,
        "comparison": {
            "BEN_PREFIX_2000": {
                "source": "committed Gate P / Gate M",
                "answer_correctness": "27/50",
                "decisive_span_recall": "21/43",
                "unanswerable": "7/7",
                "early": "19/20",
                "middle": "1/6",
                "late": "0/13",
                "exceptions": "1/5",
                "multi_hop": "1/6",
                "global": "2/5",
                "tables": "3/6",
            },
            "BEN_EXISTING_FTS": {
                "source": "committed Gate P isolated FTS",
                "answer_correctness": "48/50",
                "decisive_span_recall": "42/43",
                "unanswerable": "7/7",
                "early": "18/20",
                "middle": "6/6",
                "late": "13/13",
                "exceptions": "5/5",
                "multi_hop": "5/6",
                "global": "5/5",
                "tables": "6/6",
            },
            "OPENAI_NATIVE_DOCUMENT": "BLOCKED",
            "CLAUDE_NATIVE_DOCUMENT": "BLOCKED",
            "GEMINI_NATIVE_PDF": "BLOCKED",
            "GROK_NATIVE_DOCUMENT": "BLOCKED",
        },
        "verdict": {
            "best_cheap_retrieval": "BEN_EXISTING_FTS (committed Gate P)",
            "best_full_document_reasoning": "BLOCKED — native PDF modes not run",
            "best_exception_handling": "BEN_EXISTING_FTS 5/5 among measured modes",
            "best_multi_hop": "BEN_EXISTING_FTS 5/6 among measured modes",
            "best_global_document_understanding": "BEN_EXISTING_FTS 5/5 among measured modes",
            "best_table_understanding": "BEN_EXISTING_FTS 6/6 among measured modes",
            "best_cost_performance": "BEN_EXISTING_FTS ($0 provider spend)",
            "does_any_native_provider_materially_beat_ben_fts": "NOT_MEASURED",
            "m09": "Native PDF not run. Committed: prefix recovered M09; isolated FTS missed it (lexical paraphrase).",
            "m33": "Native PDF not run. Committed FTS retrieved quantity+price pages; remaining miss is computation, not retrieval.",
            "ben_routing_implication": (
                "Unchanged from Gate P until native PDF is measured: FTS remains the cheap measured "
                "retrieval path; native document stays a possible later escalation, not a replacement."
            ),
            "recommendation": (
                "Do not implement provider PDF support from this gate. Add OPENAI_API_KEY, "
                "ANTHROPIC_API_KEY, and GOOGLE_API_KEY to the BEN Document Benchmark Cursor "
                "environment via the dashboard secret injector, then re-run Gate P2. "
                "Do not paste key values into chat."
            ),
        },
        "production_invariants": {
            "provider_adapters_unchanged": True,
            "retrieval_unchanged": True,
            "fts_unchanged": True,
            "PER_FILE_MAX_CHARS_unchanged": True,
            "no_file_search_stores": True,
            "gold_unmodified": True,
        },
    }


def render_markdown(payload: dict[str, Any]) -> str:
    sec = payload["secure_environment"]
    kp = sec.get("KEY_PRESENT") or {}
    providers = payload["providers"]
    cmp_ = payload["comparison"]
    v = payload["verdict"]

    def kp_line(name: str) -> str:
        present = kp.get(name)
        return f"{name}: KEY_PRESENT={'true' if present else 'false'}"

    def block(name: str) -> list[str]:
        row = providers[name]
        return [
            f"{name}:",
            f"model: {row['model']}",
            f"correct: {row['correct']}",
            f"unanswerable: {row['unanswerable']}",
            f"early: {row['early']}",
            f"middle: {row['middle']}",
            f"late: {row['late']}",
            f"exceptions: {row['exceptions']}",
            f"multi-hop: {row['multi_hop']}",
            f"global: {row['global']}",
            f"tables: {row['tables']}",
            f"citations: {row['citations']}",
            f"latency: {row['latency']}",
            f"tokens: {row['tokens']}",
            f"cost: {row['cost']}",
            f"DOCUMENT_AVAILABLE_TO_MODEL: {row['DOCUMENT_AVAILABLE_TO_MODEL']}",
            f"FINAL ANSWER QUALITY: {row['FINAL_ANSWER_QUALITY']}",
            f"blocked_reason: {row.get('blocked_reason')}",
            "",
        ]

    lines = [
        "# GATE P2 — Native Document Provider Benchmark",
        "",
        f"GATE P2 STATUS: {payload['gate_p2_status']}",
        "",
        "RESEARCH / MEASUREMENT ONLY. Production adapters, retrieval, FTS,",
        "PER_FILE_MAX_CHARS, and gold labels were not changed.",
        "Provider File Search / vector stores were not used.",
        "",
        "SECURE ENVIRONMENT:",
        kp_line("OPENAI_API_KEY"),
        kp_line("ANTHROPIC_API_KEY"),
        kp_line("GOOGLE_API_KEY"),
        kp_line("GEMINI_API_KEY"),
        kp_line("XAI_API_KEY"),
        f"RAILWAY_TOKEN: KEY_PRESENT={'true' if (sec.get('railway') or {}).get('RAILWAY_TOKEN') else 'false'}",
        f"cursor_injected_secret_names: {', '.join(sec.get('cursor_injected_secret_names') or [])}",
        f"railway_can_inject_provider_keys: {(sec.get('railway') or {}).get('can_inject_provider_keys')}",
        f"railway_me_error: {(sec.get('railway') or {}).get('me_error')}",
        f"railway_project_token_error: {(sec.get('railway') or {}).get('project_token_error')}",
        f"safe_injection_possible: {sec.get('safe_injection_possible')}",
        f"action: {sec.get('action')}",
        f"reason: {sec.get('reason')}",
        "",
        *block("OPENAI"),
        *block("CLAUDE"),
        *block("GEMINI"),
        *block("GROK"),
        "COMPARISON:",
        "",
        "| Mode | Correct | Unanswerable | Early | Middle | Late | Exceptions | Multi-hop | Global | Tables |",
        "|---|---|---|---|---|---|---|---|---|---|",
        "| BEN prefix | {answer_correctness} | {unanswerable} | {early} | {middle} | {late} | {exceptions} | {multi_hop} | {global} | {tables} |".format(
            **cmp_["BEN_PREFIX_2000"]
        ),
        "| BEN FTS | {answer_correctness} | {unanswerable} | {early} | {middle} | {late} | {exceptions} | {multi_hop} | {global} | {tables} |".format(
            **cmp_["BEN_EXISTING_FTS"]
        ),
        "| OpenAI native | BLOCKED | BLOCKED | BLOCKED | BLOCKED | BLOCKED | BLOCKED | BLOCKED | BLOCKED | BLOCKED |",
        "| Claude native | BLOCKED | BLOCKED | BLOCKED | BLOCKED | BLOCKED | BLOCKED | BLOCKED | BLOCKED | BLOCKED |",
        "| Gemini native | BLOCKED | BLOCKED | BLOCKED | BLOCKED | BLOCKED | BLOCKED | BLOCKED | BLOCKED | BLOCKED |",
        "| Grok native | BLOCKED | BLOCKED | BLOCKED | BLOCKED | BLOCKED | BLOCKED | BLOCKED | BLOCKED | BLOCKED |",
        "",
        f"BEST CHEAP RETRIEVAL: {v['best_cheap_retrieval']}",
        f"BEST FULL-DOCUMENT REASONING: {v['best_full_document_reasoning']}",
        f"BEST EXCEPTION HANDLING: {v['best_exception_handling']}",
        f"BEST MULTI-HOP: {v['best_multi_hop']}",
        f"BEST GLOBAL DOCUMENT UNDERSTANDING: {v['best_global_document_understanding']}",
        f"BEST TABLE UNDERSTANDING: {v['best_table_understanding']}",
        f"BEST COST/PERFORMANCE: {v['best_cost_performance']}",
        f"DOES ANY NATIVE PROVIDER MATERIALLY BEAT BEN FTS? {v['does_any_native_provider_materially_beat_ben_fts']}",
        f"M09: {v['m09']}",
        f"M33: {v['m33']}",
        f"BEN ROUTING IMPLICATION: {v['ben_routing_implication']}",
        f"RECOMMENDATION: {v['recommendation']}",
        "",
        "STOP.",
        "",
        "Do not implement provider PDF support.",
        "Do not change production.",
        "",
    ]
    return "\n".join(lines)


def run_gate_p2() -> dict[str, Any]:
    probe_result = probe()
    assert_no_secret_values(probe_result)
    if not probe_result.get("safe_injection_possible"):
        payload = build_blocked_report(probe_result)
        assert_no_secret_values(payload)
        return payload
    # Live native-PDF execution is intentionally not implemented in this
    # fail-closed path. If keys later become present, a follow-up run should
    # call isolated provider document APIs without changing BEN adapters.
    payload = build_blocked_report(probe_result)
    payload["gate_p2_status"] = "PARTIAL"
    payload["secure_environment"]["action"] = "KEYS_PRESENT_BUT_LIVE_RUNNER_NOT_ENTERED"
    assert_no_secret_values(payload)
    return payload


def persist(payload: dict[str, Any], dests: list[Path]) -> None:
    assert_no_secret_values(payload)
    md = render_markdown(payload)
    for dest in dests:
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "gate_p2_results.json").write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )
        (dest / "GATE_P2_REPORT.md").write_text(md, encoding="utf-8")
