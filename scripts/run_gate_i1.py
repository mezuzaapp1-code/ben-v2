#!/usr/bin/env python3
"""Run Gate I1 isolated improvement-loop prototype.

Does not change production flags, merge, or deploy.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from services.improvement.loop import run_loop, write_fixtures  # noqa: E402
from services.workspace_files.chunk_retriever import chunk_retrieval_enabled  # noqa: E402
from tests.gate_m.measure import WS as GATE_M_WS  # noqa: E402


def _markdown(payload: dict) -> str:
    failure = payload.get("failure_record") or {}
    contract = payload.get("fix_contract") or {}
    caps = payload.get("capabilities") or {}
    cands = payload.get("candidates") or []
    lines = [
        "# Gate I1 — automated improvement loop prototype",
        "",
        f"**Status:** GATE I1: {payload.get('gate_status')}",
        f"**Stop reason:** {payload.get('stop_reason')}",
        "",
        "Production unchanged. Not merged. Not deployed.",
        "",
        "## Failure record",
        "",
        f"- failure_id: `{failure.get('failure_id')}`",
        f"- benchmark_id: `{failure.get('benchmark_id')}`",
        f"- classified as: `{failure.get('failure_class')}` (prior label `{failure.get('prior_label')}`)",
        f"- user_query: {failure.get('user_query')}",
        f"- expected_evidence: {failure.get('expected_evidence')}",
        f"- retrieval_mode: `{failure.get('retrieval_mode')}`",
        f"- security_relevant: {failure.get('security_relevant')}",
        "",
        "## Fix Contract",
        "",
        f"- problem_class: `{contract.get('problem_class')}`",
        f"- scope: `{contract.get('scope')}` risk `{contract.get('risk_level')}`",
        f"- hypothesis: {contract.get('hypothesis')}",
        f"- allowed_modules: {', '.join(contract.get('allowed_modules') or [])}",
        f"- forbidden_modules: {', '.join(contract.get('forbidden_modules') or [])}",
        "",
        "## Existing-capabilities audit",
        "",
        f"- USE: {', '.join(caps.get('use') or [])}",
        f"- ADAPT: {', '.join(caps.get('adapt') or [])}",
        f"- LEARN: {', '.join(caps.get('learn') or [])}",
        f"- REJECT: {', '.join(caps.get('reject') or [])}",
        f"- chosen: {caps.get('chosen_mechanism')}",
        "",
        "## Candidates",
        "",
    ]
    for c in cands:
        bench = c.get("benchmark_result") or {}
        lines.extend(
            [
                f"### {c.get('candidate_id')} — {c.get('decision')}",
                "",
                f"- change: {c.get('change_summary')}",
                f"- extra atoms: `{c.get('extra_atoms_for_seed_query')}`",
                f"- correctness: {bench.get('answer_correctness')}",
                f"- decisive recall: {bench.get('decisive_span_recall')}",
                f"- unanswerable: {bench.get('unanswerable_precision')}",
                f"- MRL: {bench.get('mrl')}",
                f"- regressions: {c.get('regressions') or 'none'}",
                "",
            ]
        )
    lines.extend(
        [
            "## Recommendation",
            "",
            str(payload.get("recommendation") or ""),
            "",
            f"Pattern generic: {payload.get('pattern_generic')}",
            "",
            f"Production FTS still off for Gate M workspace: `{not chunk_retrieval_enabled(GATE_M_WS)}`",
            "",
        ]
    )
    return "\n".join(lines) + "\n"


async def main() -> int:
    os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://ben:ben@127.0.0.1:5432/ben")
    os.environ.setdefault("BEN_TEST_PG_DSN", "postgresql://ben:ben@127.0.0.1:5432/ben")
    payload = await run_loop()
    fixture = write_fixtures(payload)
    md = _markdown(payload)
    (fixture.parent / "gate_i1_report.md").write_text(md, encoding="utf-8")
    artifacts = Path("/opt/cursor/artifacts")
    artifacts.mkdir(parents=True, exist_ok=True)
    (artifacts / "i1_result.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (artifacts / "gate_i1_report.md").write_text(md, encoding="utf-8")
    print(md)
    print(json.dumps({"gate_status": payload.get("gate_status"), "decision": payload.get("decision")}, indent=2))
    print(f"wrote {fixture}")
    return 0 if payload.get("gate_status") in {"PASS", "REJECT", "NEEDS_REVIEW"} else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
