"""Production smoke + wall-clock timing for timeout alignment verification."""
from __future__ import annotations

import json
import re
import sys
import time

import httpx

BASE = "https://ben-v2-production.up.railway.app"
TENANT = "00000000-0000-0000-0000-000000000001"
COUNCIL_KEYS = frozenset({"cost_usd", "council", "question", "request_id", "synthesis"})
SECRET_PATTERNS = [
    re.compile(r"sk-[a-zA-Z0-9]{10,}"),
    re.compile(r"postgresql://", re.I),
]


def main() -> int:
    timings: dict[str, float] = {}
    failures: list[str] = []

    with httpx.Client(timeout=35.0) as client:
        for path in ("/health", "/ready"):
            t0 = time.perf_counter()
            r = client.get(f"{BASE}{path}")
            timings[path] = time.perf_counter() - t0
            print(f"GET {path} -> {r.status_code} in {timings[path]:.2f}s")

        t0 = time.perf_counter()
        c = client.post(
            f"{BASE}/council",
            json={
                "tenant_id": TENANT,
                "question": "Production timeout alignment smoke: one-line legal risk?",
            },
        )
        timings["/council"] = time.perf_counter() - t0
        print(f"POST /council -> {c.status_code} in {timings['/council']:.2f}s")

        h = client.get(f"{BASE}/health")
        r = client.get(f"{BASE}/ready")

    health = h.json()
    ready = r.json()
    council = c.json()

    print("\n=== /health ===")
    print(json.dumps({k: health.get(k) for k in ("status", "version", "request_id", "checks")}, indent=2))
    print("\n=== /ready ===")
    print(json.dumps(ready, indent=2))
    print("\n=== /council summary ===")
    experts = council.get("council") if isinstance(council.get("council"), list) else []
    print(
        json.dumps(
            {
                "keys": sorted(council.keys()),
                "expert_count": len(experts),
                "synthesis": "null" if council.get("synthesis") is None else "present",
                "cost_usd": council.get("cost_usd"),
            },
            indent=2,
        )
    )

    def ok(name: str, cond: bool, note: str = "") -> None:
        if not cond:
            failures.append(f"{name}: {note}")

    ok("health_200", h.status_code == 200, str(h.status_code))
    ok("health_healthy", health.get("status") == "healthy", str(health.get("status")))
    ok("health_request_id", bool(health.get("request_id")))
    ok("ready_200", r.status_code == 200, str(r.status_code))
    ok("ready_true", ready.get("ready") is True, str(ready.get("ready")))
    ok("migration", ready.get("migration_head") == "002_ko_synthesis_jsonb", str(ready.get("migration_head")))
    ok("council_200", c.status_code == 200, str(c.status_code))
    ok("council_keys", set(council.keys()) == COUNCIL_KEYS, str(sorted(council.keys())))
    ok("experts_3", len(experts) == 3, str(len(experts)))
    syn = council.get("synthesis")
    ok("synthesis_ok", syn is None or isinstance(syn, (dict, str)))
    cost = council.get("cost_usd")
    ok("cost_numeric", isinstance(cost, (int, float)) and not isinstance(cost, bool))
    blob = json.dumps(council)
    ok("no_5xx", c.status_code < 500)
    ok("no_HTTPStatusError", "HTTPStatusError" not in blob)
    ok("no_traceback", "Traceback" not in blob)
    ok("no_secrets", not any(p.search(blob) for p in SECRET_PATTERNS))

    print("\n=== TIMINGS (wall-clock) ===")
    for k, v in timings.items():
        print(f"  {k}: {v:.2f}s")

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(" ", f)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
