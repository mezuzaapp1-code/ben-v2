#!/usr/bin/env python3
"""Run Gate P provider-file intelligence measurement.

Does not change production retrieval, PER_FILE_MAX_CHARS, or chunk FTS flags.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from tests.gate_p.run import persist, run_gate_p


async def main() -> int:
    tmp = Path("/tmp/gate_p_gold.pdf")
    result = await run_gate_p(tmp)
    dests = [
        Path("/opt/cursor/artifacts"),
        _ROOT / "tasks" / "research" / "gate_p",
        _ROOT / "tests" / "fixtures" / "gate_p",
    ]
    persist(result, dests)
    slim_status = {
        "gate_p_status": result["gate_p_status"],
        "modes": {
            name: {
                "status": item.get("status"),
                "blocked_reason": item.get("blocked_reason"),
                "summary": item.get("summary"),
            }
            for name, item in (result.get("_full_modes") or {}).items()
        },
        "verdict": result.get("verdict"),
    }
    print(json.dumps(slim_status, indent=2, default=str))
    print("wrote", dests[0] / "GATE_P_REPORT.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
