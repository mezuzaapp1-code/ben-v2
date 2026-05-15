"""Production smoke for JSON logging merge verification."""
from __future__ import annotations

import json
import re
import sys

import httpx

BASE = "https://ben-v2-production.up.railway.app"
TENANT = "00000000-0000-0000-0000-000000000001"
SECRET_PATTERNS = [
    re.compile(r"sk-[a-zA-Z0-9]{10,}"),
    re.compile(r"sk_ant[a-zA-Z0-9_-]{10,}"),
    re.compile(r"Bearer\s+\S+"),
    re.compile(r"postgresql://", re.I),
]
COUNCIL_KEYS = frozenset({"cost_usd", "council", "question", "request_id", "synthesis"})


def check_secrets(text: str) -> list[str]:
    return [p.pattern for p in SECRET_PATTERNS if p.search(text)]


def main() -> int:
    results: list[tuple[str, str, str]] = []
    with httpx.Client(timeout=120.0) as client:
        h = client.get(f"{BASE}/health")
        r = client.get(f"{BASE}/ready")
        c = client.post(
            f"{BASE}/council",
            json={
                "tenant_id": TENANT,
                "question": "Production JSON logging smoke: one-line legal risk check?",
                "experts": ["Legal"],
            },
        )

    print("=== HTTP ===")
    print(f"GET /health -> {h.status_code}")
    print(f"GET /ready -> {r.status_code}")
    print(f"POST /council -> {c.status_code}")

    health = h.json() if h.headers.get("content-type", "").startswith("application/json") else {}
    ready = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    council = c.json() if c.headers.get("content-type", "").startswith("application/json") else {}

    print("\n=== /health (redacted) ===")
    print(json.dumps({k: health.get(k) for k in ("status", "version", "request_id", "checks")}, indent=2))

    print("\n=== /ready ===")
    print(json.dumps(ready, indent=2))

    print("\n=== /council summary ===")
    experts = council.get("council") if isinstance(council.get("council"), list) else []
    print(
        json.dumps(
            {
                "keys": sorted(council.keys()) if isinstance(council, dict) else [],
                "expert_count": len(experts),
                "has_synthesis": council.get("synthesis") is not None,
                "synthesis_null": council.get("synthesis") is None,
                "cost_usd_type": type(council.get("cost_usd")).__name__,
                "request_id_present": bool(council.get("request_id")),
            },
            indent=2,
        )
    )

    failures: list[str] = []

    def ok(name: str, cond: bool, note: str = "") -> None:
        results.append((name, "PASS" if cond else "FAIL", note))
        if not cond:
            failures.append(f"{name}: {note}")

    ok("health_http_200", h.status_code == 200, str(h.status_code))
    ok("health_status_healthy", health.get("status") == "healthy", str(health.get("status")))
    ok("health_request_id", bool(health.get("request_id")), "missing")
    ok("ready_http_200", r.status_code == 200, str(r.status_code))
    ok("ready_true", ready.get("ready") is True, str(ready.get("ready")))
    ok(
        "migration_head",
        ready.get("migration_head") == "002_ko_synthesis_jsonb",
        str(ready.get("migration_head")),
    )
    ok("ready_request_id", bool(ready.get("request_id")), "missing")
    ok("council_http_200", c.status_code == 200, str(c.status_code))
    ok("council_keys", set(council.keys()) == COUNCIL_KEYS, str(sorted(council.keys())))
    ok("council_experts_3", len(experts) == 3, str(len(experts)))
    syn = council.get("synthesis")
    ok(
        "synthesis_present_or_null",
        syn is None or (isinstance(syn, dict) and len(syn) > 0) or isinstance(syn, str),
        type(syn).__name__,
    )
    cost = council.get("cost_usd")
    ok("cost_usd_numeric", isinstance(cost, (int, float)) and not isinstance(cost, bool), str(cost))
    ok("council_request_id", bool(council.get("request_id")), "missing")

    blob = json.dumps(council)
    ok("no_HTTPStatusError", "HTTPStatusError" not in blob, "found in body")
    ok("no_traceback", "Traceback" not in blob, "found in body")
    sec = check_secrets(blob)
    ok("no_secrets_response", not sec, str(sec))

    ver = str(health.get("version", ""))
    ok("version_contains_82739c2_or_short", "82739c2" in ver or ver == "82739c2"[:7], ver)

    print("\n=== RESULTS ===")
    for name, status, note in results:
        print(f"{name}: {status} {note}")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
