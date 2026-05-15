"""Phase A auth verification: API leakage + request_id (no secrets printed)."""
from __future__ import annotations

import json
import re
import sys

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "https://ben-v2-production.up.railway.app"
TENANT = "00000000-0000-0000-0000-000000000001"
JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
COUNCIL_KEYS = frozenset({"cost_usd", "council", "question", "request_id", "synthesis"})
CHAT_KEYS = frozenset({"cost_usd", "model_used", "response", "thread_id"})


def _leak_check(label: str, text: str, failures: list[str]) -> None:
    if "Bearer " in text and label != "headers_intentional":
        failures.append(f"{label}: contains Bearer prefix in body")
    if JWT_RE.search(text):
        failures.append(f"{label}: possible JWT in payload")


def main() -> int:
    failures: list[str] = []
    with httpx.Client(timeout=120.0) as client:
        c0 = client.post(
            f"{BASE}/council",
            json={"tenant_id": TENANT, "question": "phase A auth_missing smoke"},
        )
        c1 = client.post(
            f"{BASE}/council",
            json={"tenant_id": TENANT, "question": "phase A auth_invalid smoke"},
            headers={"Authorization": "Bearer not-a-real-jwt"},
        )
        chat = client.post(
            f"{BASE}/chat",
            json={"message": "phase A chat auth_missing", "tenant_id": TENANT, "tier": "free"},
        )

    for label, resp, keys in [
        ("council_no_auth", c0, COUNCIL_KEYS),
        ("council_bad_auth", c1, COUNCIL_KEYS),
        ("chat_no_auth", chat, CHAT_KEYS),
    ]:
        print(f"{label} status={resp.status_code}")
        if resp.status_code != 200:
            failures.append(f"{label}: expected 200 got {resp.status_code}")
            continue
        data = resp.json()
        if set(data.keys()) != keys:
            failures.append(f"{label}: keys {sorted(data.keys())}")
        rid = data.get("request_id") or data.get("thread_id")
        if not rid:
            failures.append(f"{label}: missing request_id/thread_id")
        else:
            print(f"{label} id_present=True")
        _leak_check(label, json.dumps(data), failures)

    if failures:
        print("FAILURES:")
        for f in failures:
            print(" ", f)
        return 1
    print("phase_a_api_checks=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
