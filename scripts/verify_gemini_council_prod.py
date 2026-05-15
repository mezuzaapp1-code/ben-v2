"""Production verification for 3-provider council (Gemini Strategy)."""
from __future__ import annotations

import json
import re
import sys

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "https://ben-v2-production.up.railway.app"
TENANT = "00000000-0000-0000-0000-000000000001"
QUESTION = (
    "Should BEN use static model routing or dynamic model routing for production council "
    "decisions? Compare reliability, cost, security, and reasoning diversity."
)
TOP_KEYS = frozenset({"cost_usd", "council", "question", "request_id", "synthesis"})
SECRET_PATTERNS = [
    re.compile(r"sk-[a-zA-Z0-9]{10,}"),
    re.compile(r"sk_ant[a-zA-Z0-9_-]{10,}"),
    re.compile(r"AIza[a-zA-Z0-9_-]{10,}"),
    re.compile(r"Bearer\s+[a-zA-Z0-9._-]{20,}"),
]


def main() -> int:
    failures: list[str] = []
    with httpx.Client(timeout=180.0) as client:
        h = client.get(f"{BASE}/health")
        r = client.get(f"{BASE}/ready")
        c = client.post(f"{BASE}/council", json={"tenant_id": TENANT, "question": QUESTION})

    print(f"GET /health -> {h.status_code}")
    checks = h.json().get("checks", {}) if h.status_code == 200 else {}
    print("health_checks:", {k: checks.get(k) for k in ("openai_configured", "anthropic_configured")})
    print(f"GET /ready -> {r.status_code}")
    print(f"POST /council -> {c.status_code}")

    if c.status_code != 200:
        failures.append(f"council status {c.status_code}")
        print(c.text[:800])
        return 1

    data = c.json()
    if set(data.keys()) != TOP_KEYS:
        failures.append(f"keys {sorted(data.keys())}")
    if not data.get("request_id"):
        failures.append("missing request_id")
    try:
        float(data["cost_usd"])
    except (TypeError, ValueError):
        failures.append("cost_usd not numeric")

    council = data.get("council") or []
    if len(council) != 3:
        failures.append(f"council len {len(council)}")

    expected = {
        "Legal Advisor": "anthropic",
        "Business Advisor": "openai",
        "Strategy Advisor": "google",
    }
    for m in council:
        name = m.get("expert", "")
        prov = m.get("provider", "")
        if name in expected and prov != expected[name]:
            failures.append(f"{name} provider={prov} expected={expected[name]}")
        for field in ("expert", "provider", "model", "outcome", "response"):
            if field not in m:
                failures.append(f"{name} missing {field}")

    strat = next((x for x in council if x.get("expert") == "Strategy Advisor"), {})
    print("strategy_metadata:", {
        "provider": strat.get("provider"),
        "model": strat.get("model"),
        "outcome": strat.get("outcome"),
        "response_prefix": (strat.get("response") or "")[:120],
    })

    if strat.get("provider") != "google":
        failures.append("strategy not google")
    if strat.get("outcome") != "ok":
        failures.append(f"strategy outcome={strat.get('outcome')}")
    model = str(strat.get("model", ""))
    if "gemini" not in model.lower():
        failures.append(f"strategy model unexpected: {model}")
    if model in ("gemini-1.5-flash", "gemini-1.5-pro"):
        failures.append(f"strategy on retired model id: {model}")

    blob = json.dumps(data)
    for pat in SECRET_PATTERNS:
        if pat.search(blob):
            failures.append("possible secret in response")
    if "Traceback" in blob or "HTTPStatusError" in blob:
        failures.append("exception leak")

    syn = data.get("synthesis")
    if syn:
        ae = syn.get("agreement_estimate", "")
        print("agreement_estimate:", ae)
        ok_count = sum(1 for x in council if x.get("outcome") == "ok")
        if strat.get("outcome") == "ok" and ok_count == 3:
            if "3/3" in str(ae) and "available" not in str(ae):
                failures.append("misleading agreement without available suffix")
        for m in council:
            if m.get("outcome") != "ok" and re.search(r"\b3\s*/\s*3\b", str(ae)):
                failures.append("fake 3/3 with failed expert")

    print("providers:", [(x["expert"], x["provider"], x["outcome"]) for x in council])

    if failures:
        print("FAILURES:", failures)
        return 1
    print("gemini_council_prod=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
