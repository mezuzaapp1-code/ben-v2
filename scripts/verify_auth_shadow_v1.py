"""Verify auth shadow mode v1 (local server required)."""
from __future__ import annotations

import json
import os
import sys
import time

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8004"
TENANT = "00000000-0000-0000-0000-000000000001"
COUNCIL_KEYS = frozenset({"cost_usd", "council", "question", "request_id", "synthesis"})


def main() -> int:
    failures: list[str] = []
    os.environ.setdefault("ENFORCE_AUTH", "false")
    os.environ.setdefault("AUTH_SHADOW_MODE", "true")

    with httpx.Client(timeout=60.0) as client:
        h = client.get(f"{BASE}/health")
        r = client.get(f"{BASE}/ready")
        c0 = client.post(f"{BASE}/council", json={"tenant_id": TENANT, "question": "auth shadow no header?"})
        c1 = client.post(
            f"{BASE}/council",
            json={"tenant_id": TENANT, "question": "auth shadow bad token?"},
            headers={"Authorization": "Bearer not-a-real-jwt"},
        )

    print(f"GET /health -> {h.status_code}")
    print(json.dumps(h.json().get("checks", {}), indent=2))
    print(f"GET /ready -> {r.status_code} auth={r.json().get('auth')}")
    print(f"POST /council no auth -> {c0.status_code}")
    print(f"POST /council bad auth -> {c1.status_code}")

    checks = h.json().get("checks", {})
    if checks.get("auth_enforcement") is not False:
        failures.append("auth_enforcement should be false")
    if checks.get("auth_shadow_mode") is not True:
        failures.append("auth_shadow_mode should be true")

    for label, resp in [("no_auth", c0), ("bad_auth", c1)]:
        if resp.status_code != 200:
            failures.append(f"{label}: expected 200 got {resp.status_code}")
        data = resp.json()
        if set(data.keys()) != COUNCIL_KEYS:
            failures.append(f"{label}: keys {sorted(data.keys())}")
        if not data.get("request_id"):
            failures.append(f"{label}: missing request_id")
        blob = json.dumps(data)
        if "Bearer" in blob or "eyJ" in blob:
            failures.append(f"{label}: possible token leak in body")

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(" ", f)
        return 1
    print("\nAll shadow-mode checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
