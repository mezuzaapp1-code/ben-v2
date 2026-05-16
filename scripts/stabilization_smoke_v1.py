"""Stabilization checkpoint v1 — production API smoke (unsigned + health)."""
from __future__ import annotations

import json
import re
import sys
import time

import httpx

BASE = "https://ben-v2-production.up.railway.app"
FRONTEND = "https://ben-v2.vercel.app"
ANON = "00000000-0000-0000-0000-000000000001"
SECRET = re.compile(r"sk-[a-zA-Z0-9]{10,}")


def ok(name: str, cond: bool, note: str = "") -> bool:
    if not cond:
        print(f"FAIL {name}: {note}")
    else:
        print(f"PASS {name}")
    return cond


def main() -> int:
    failures = 0
    with httpx.Client(timeout=40.0) as client:
        h = client.get(f"{BASE}/health")
        r = client.get(f"{BASE}/ready")
        failures += not ok("health_200", h.status_code == 200, str(h.status_code))
        health = h.json()
        failures += not ok("tenant_binding_flag", health.get("checks", {}).get("tenant_binding_enabled") is True)
        failures += not ok("auth_enforcement_off", health.get("checks", {}).get("auth_enforcement") is False)

        chat = client.post(
            f"{BASE}/chat",
            json={"message": "Stabilization smoke: one line.", "tier": "free"},
        )
        failures += not ok("unsigned_chat_200", chat.status_code == 200, str(chat.status_code))
        chat_body = chat.json()
        failures += not ok("chat_thread_id", bool(chat_body.get("thread_id")))
        failures += not ok("chat_no_secrets", not SECRET.search(json.dumps(chat_body)))

        council = client.post(
            f"{BASE}/council",
            json={"question": "Stabilization smoke: one-line legal risk?"},
        )
        failures += not ok("unsigned_council_200", council.status_code == 200, str(council.status_code))
        council_body = council.json()
        experts = council_body.get("council") or []
        failures += not ok("council_3_experts", len(experts) == 3, str(len(experts)))
        strategy = next((e for e in experts if "Strategy" in (e.get("expert") or "")), None)
        if strategy:
            failures += not ok(
                "gemini_strategy_ok",
                strategy.get("outcome") == "ok" and "gemini" in (strategy.get("model") or "").lower(),
                json.dumps({k: strategy.get(k) for k in ("outcome", "model", "provider")}),
            )
        else:
            failures += 1
            print("FAIL gemini_strategy_ok: no Strategy expert")

        threads = client.get(f"{BASE}/api/threads")
        failures += not ok("unsigned_threads_200", threads.status_code == 200, str(threads.status_code))

        forged = client.post(
            f"{BASE}/chat",
            json={
                "message": "forge",
                "tier": "free",
                "tenant_id": "22222222-2222-2222-2222-222222222222",
            },
        )
        failures += not ok("forged_tenant_unsigned_200", forged.status_code == 200, str(forged.status_code))

        fe = client.get(FRONTEND)
        failures += not ok("vercel_frontend_200", fe.status_code == 200, str(fe.status_code))
        failures += not ok(
            "vercel_has_clerk_bundle",
            "clerk" in fe.text.lower() or "pk_" in fe.text,
            "heuristic",
        )

    print(f"\nTotal failures: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
