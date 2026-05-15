"""Local verification for timeout budget alignment v1."""
from __future__ import annotations

import json
import os
import sys
import time

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8003"
TENANT = "00000000-0000-0000-0000-000000000001"
COUNCIL_KEYS = frozenset({"cost_usd", "council", "question", "request_id", "synthesis"})


def check_council(body: dict, label: str) -> list[str]:
    failures: list[str] = []
    t0 = time.perf_counter()
    with httpx.Client(timeout=35.0) as client:
        r = client.post(f"{BASE}/council", json=body)
    elapsed = time.perf_counter() - t0
    print(f"{label}: HTTP {r.status_code} in {elapsed:.2f}s")
    if r.status_code != 200:
        failures.append(f"{label}: status {r.status_code}")
        return failures
    data = r.json()
    blob = json.dumps(data)
    if "Traceback" in blob or "HTTPStatusError" in blob:
        failures.append(f"{label}: leaked error text")
    if set(data.keys()) != COUNCIL_KEYS:
        failures.append(f"{label}: keys {sorted(data.keys())}")
    if not data.get("request_id"):
        failures.append(f"{label}: missing request_id")
    experts = data.get("council") or []
    if len(experts) != 3:
        failures.append(f"{label}: expert count {len(experts)}")
    for ex in experts:
        resp = str(ex.get("response", ""))
        if "Traceback" in resp:
            failures.append(f"{label}: traceback in expert response")
    if elapsed > 28.0:
        failures.append(f"{label}: exceeded DELIBERATE 25s budget (+grace) at {elapsed:.2f}s")
    print(f"  synthesis={'null' if data.get('synthesis') is None else 'present'} cost_usd={data.get('cost_usd')}")
    return failures


def main() -> int:
    failures: list[str] = []
    with httpx.Client(timeout=10.0) as client:
        for path in ("/health", "/ready"):
            t0 = time.perf_counter()
            r = client.get(f"{BASE}{path}")
            elapsed = time.perf_counter() - t0
            print(f"GET {path}: {r.status_code} in {elapsed:.2f}s")
            if elapsed > 6.0:
                failures.append(f"{path} exceeded FAST 5s budget at {elapsed:.2f}s")
            if path == "/health" and not r.json().get("request_id"):
                failures.append("/health missing request_id")

    failures.extend(
        check_council(
            {"tenant_id": TENANT, "question": "Timeout alignment smoke: one-line risk check?"},
            "council_happy",
        )
    )

    # Invalid synthesis model via env override in subprocess not available here;
    # use invalid model in request is not supported — skip unless server started with env.
    if os.getenv("VERIFY_INVALID_SYNTHESIS") == "1":
        failures.extend(
            check_council(
                {"tenant_id": TENANT, "question": "Invalid synthesis path test"},
                "council_invalid_synth",
            )
        )

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(" ", f)
        return 1
    print("\nAll checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
