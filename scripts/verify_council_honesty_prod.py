"""Production smoke for council honesty fields (no secrets printed)."""
from __future__ import annotations

import json
import sys

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "https://ben-v2-production.up.railway.app"
TENANT = "00000000-0000-0000-0000-000000000001"
TOP_KEYS = frozenset({"cost_usd", "council", "question", "request_id", "synthesis"})


def main() -> int:
    failures: list[str] = []
    with httpx.Client(timeout=120.0) as client:
        h = client.get(f"{BASE}/health")
        r = client.get(f"{BASE}/ready")
        c = client.post(
            f"{BASE}/council",
            json={"tenant_id": TENANT, "question": "Council honesty prod smoke: one line each."},
        )

    print(f"GET /health -> {h.status_code}")
    print(f"GET /ready -> {r.status_code}")
    print(f"POST /council -> {c.status_code}")

    if h.status_code != 200:
        failures.append(f"health status {h.status_code}")
    if r.status_code != 200:
        failures.append(f"ready status {r.status_code}")
    if c.status_code != 200:
        failures.append(f"council status {c.status_code}")
        print(c.text[:500])
        return 1

    data = c.json()
    keys = set(data.keys())
    if keys != TOP_KEYS:
        failures.append(f"top keys {sorted(keys)}")
    if not data.get("request_id"):
        failures.append("missing request_id")
    try:
        float(data["cost_usd"])
    except (TypeError, ValueError):
        failures.append("cost_usd not numeric")

    council = data.get("council")
    if not isinstance(council, list) or len(council) != 3:
        failures.append("council array invalid")
    else:
        for i, m in enumerate(council):
            for field in ("expert", "provider", "model", "outcome", "response"):
                if field not in m:
                    failures.append(f"member[{i}] missing {field}")
            if m.get("outcome") not in ("ok", "degraded", "timeout", "error"):
                failures.append(f"member[{i}] bad outcome {m.get('outcome')}")
        print("experts:", [(x["expert"], x["outcome"], x["provider"]) for x in council])
        syn = data.get("synthesis")
        if syn:
            print("agreement_estimate:", syn.get("agreement_estimate"))

    blob = json.dumps(data)
    if "HTTPStatusError" in blob or "Traceback" in blob:
        failures.append("leak in response body")

    if failures:
        print("FAILURES:", failures)
        return 1
    print("council_honesty_prod_smoke=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
