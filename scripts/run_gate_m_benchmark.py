#!/usr/bin/env python3
"""Run Gate M gold-file measurement. Does not change retrieval or production flags."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from tests.gate_m.gold_questions import QUESTIONS
from tests.gate_m.measure import run_benchmark, write_reports


async def main() -> int:
    tmp = Path("/tmp/gate_m_gold.pdf")
    result = await run_benchmark(tmp)
    out_dir = Path("/opt/cursor/artifacts")
    write_reports(result, out_dir)
    fixture = _ROOT / "tests" / "fixtures" / "gate_m"
    fixture.mkdir(parents=True, exist_ok=True)
    pack = {
        "document": "ben_gold_supply_agreement.pdf",
        "question_count": len(QUESTIONS),
        "questions": QUESTIONS,
    }
    (fixture / "gold_questions.json").write_text(
        json.dumps(pack, indent=2, ensure_ascii=True) + "\n", encoding="utf-8"
    )
    write_reports(result, fixture, stable=True)
    summary = result["summary"]
    print(json.dumps(summary, indent=2))
    print(f"wrote {out_dir / 'gate_m_baseline.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
