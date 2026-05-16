"""Production smoke for Tenant Mode v2 deploy."""
from __future__ import annotations

import json
import os
import re
import sys
import time

import httpx

BASE = os.environ.get("BEN_API_BASE", "https://ben-v2-production.up.railway.app")
ANON = "00000000-0000-0000-0000-000000000001"
JSON_DETAIL = re.compile(r'^\s*\{\s*"detail"')


def ok(name: str, cond: bool, note: str = "") -> bool:
    status = "PASS" if cond else "FAIL"
    print(f"{status} {name}" + (f": {note}" if note else ""))
    return cond


def no_raw_json_error(body: dict | list | str) -> bool:
    if isinstance(body, dict):
        d = body.get("detail")
        if isinstance(d, str) and JSON_DETAIL.match(d):
            return False
        if isinstance(d, dict) and d.get("code") == "clerk_org_required":
            return True
    return True


def main() -> int:
    failures = 0
    jwt_no_org = os.environ.get("BEN_PROD_CLERK_JWT_NO_ORG", "").strip()
    jwt_with_org = os.environ.get("BEN_PROD_CLERK_JWT_WITH_ORG", "").strip()
    jwt_generic = os.environ.get("BEN_PROD_CLERK_JWT", "").strip()
    if not jwt_no_org and jwt_generic:
        jwt_no_org = jwt_generic

    with httpx.Client(timeout=45.0) as client:
        h = client.get(f"{BASE}/health")
        health = h.json() if h.status_code == 200 else {}
        checks = health.get("checks") or {}
        version = health.get("version", "?")
        print(f"health version={version}")
        failures += not ok("health_200", h.status_code == 200)
        failures += not ok("tenant_modes_enabled", checks.get("tenant_modes_enabled") is True, str(checks))
        failures += not ok(
            "require_org_for_signed_in_false",
            checks.get("require_org_for_signed_in") is False,
            str(checks),
        )

        chat = client.post(f"{BASE}/chat", json={"message": "Tenant v2 smoke: signed-out chat.", "tier": "free"})
        failures += not ok("signed_out_chat_200", chat.status_code == 200, str(chat.status_code))
        chat_j = chat.json()
        failures += not ok("signed_out_chat_no_raw_json", no_raw_json_error(chat_j))
        failures += not ok("signed_out_chat_has_response", bool(chat_j.get("response")))
        thread_a = chat_j.get("thread_id")

        council = client.post(
            f"{BASE}/council",
            json={"question": "Tenant v2 smoke: one-line risk?"},
        )
        failures += not ok("signed_out_council_200", council.status_code == 200, str(council.status_code))
        council_j = council.json()
        failures += not ok("signed_out_council_no_raw_json", no_raw_json_error(council_j))
        failures += not ok("signed_out_council_3_experts", len(council_j.get("council") or []) == 3)

        threads = client.get(f"{BASE}/api/threads")
        failures += not ok("signed_out_threads_200", threads.status_code == 200, str(threads.status_code))

        if thread_a:
            detail = client.get(f"{BASE}/api/threads/{thread_a}")
            failures += not ok(
                "signed_out_thread_rehydrate_200",
                detail.status_code == 200,
                str(detail.status_code),
            )
            if detail.status_code == 200:
                msgs = detail.json().get("messages") or []
                failures += not ok("signed_out_thread_has_messages", len(msgs) >= 1, str(len(msgs)))

        if jwt_no_org:
            headers = {"Authorization": f"Bearer {jwt_no_org}"}
            pc = client.post(
                f"{BASE}/chat",
                json={"message": "Tenant v2 smoke: personal chat.", "tier": "free"},
                headers=headers,
            )
            failures += not ok(
                "signed_in_no_org_chat",
                pc.status_code == 200,
                f"{pc.status_code} {pc.text[:200]}",
            )
            if pc.status_code == 200:
                pj = pc.json()
                failures += not ok("personal_chat_no_clerk_org_403", pc.status_code != 403)
                failures += not ok("personal_chat_no_raw_json", no_raw_json_error(pj))
                personal_thread = pj.get("thread_id")

                pco = client.post(
                    f"{BASE}/council",
                    json={"question": "Tenant v2 personal council smoke?"},
                    headers=headers,
                )
                failures += not ok("signed_in_no_org_council_200", pco.status_code == 200, str(pco.status_code))

                pt = client.get(f"{BASE}/api/threads", headers=headers)
                failures += not ok("signed_in_no_org_threads_200", pt.status_code == 200, str(pt.status_code))

                if personal_thread:
                    pd = client.get(f"{BASE}/api/threads/{personal_thread}", headers=headers)
                    failures += not ok(
                        "personal_thread_rehydrate_200",
                        pd.status_code == 200,
                        str(pd.status_code),
                    )
        else:
            print("PARTIAL signed_in_no_org_*: BEN_PROD_CLERK_JWT_NO_ORG not set")

        if jwt_with_org:
            headers = {"Authorization": f"Bearer {jwt_with_org}"}
            oc = client.post(
                f"{BASE}/chat",
                json={"message": "Tenant v2 smoke: org chat.", "tier": "free"},
                headers=headers,
            )
            failures += not ok("signed_in_with_org_chat_200", oc.status_code == 200, str(oc.status_code))
            failures += not ok("org_chat_no_raw_json", no_raw_json_error(oc.json() if oc.status_code == 200 else {}))
        else:
            print("PARTIAL signed_in_with_org_*: BEN_PROD_CLERK_JWT_WITH_ORG not set")

    print(f"\nTotal FAIL count: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
