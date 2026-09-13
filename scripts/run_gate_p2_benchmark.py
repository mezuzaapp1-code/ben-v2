#!/usr/bin/env python3
"""Gate P2 native-document probe. Does not change production or print secrets."""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from tests.gate_p2.credentials import assert_no_secret_values
from tests.gate_p2.run import persist, run_gate_p2


def main() -> int:
    payload = run_gate_p2()
    assert_no_secret_values(payload)
    dests = [
        Path("/opt/cursor/artifacts"),
        _ROOT / "tasks" / "research" / "gate_p2",
        _ROOT / "tests" / "fixtures" / "gate_p2",
    ]
    persist(payload, dests)
    print(
        json.dumps(
            {
                "gate_p2_status": payload["gate_p2_status"],
                "KEY_PRESENT": payload["secure_environment"]["KEY_PRESENT"],
                "safe_injection_possible": payload["secure_environment"]["safe_injection_possible"],
                "action": payload["secure_environment"]["action"],
            },
            indent=2,
        )
    )
    return 0 if payload["gate_p2_status"] != "BLOCKED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
