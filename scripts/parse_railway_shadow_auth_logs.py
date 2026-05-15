"""Parse Railway log text for shadow_auth_check outcomes (stdin or file)."""
from __future__ import annotations

import json
import re
import sys

OUTCOMES = ("auth_missing", "auth_invalid", "auth_valid", "auth_error")


def main() -> int:
    text = sys.stdin.read() if len(sys.argv) < 2 else open(sys.argv[1], encoding="utf-8").read()
    found = {o: 0 for o in OUTCOMES}
    request_ids = 0
    jwt_hits = 0
    for line in text.splitlines():
        if "shadow_auth_check" not in line and "shadow auth check" not in line:
            continue
        for o in OUTCOMES:
            if f'"outcome": "{o}"' in line or f"outcome={o}" in line or f'"{o}"' in line and "outcome" in line:
                found[o] += 1
        if "request_id" in line:
            request_ids += 1
        if re.search(r"eyJ[A-Za-z0-9_-]{10,}\.", line):
            jwt_hits += 1
        try:
            obj = json.loads(line)
            outcome = obj.get("outcome")
            if outcome in found:
                found[outcome] += 1
            if obj.get("request_id"):
                request_ids += 1
        except json.JSONDecodeError:
            pass

    print("shadow_auth_outcomes:", found)
    print("lines_with_request_id=", request_ids)
    print("jwt_leak_lines=", jwt_hits)
    missing = [o for o in ("auth_missing", "auth_invalid", "auth_valid") if found[o] < 1]
    if missing:
        print("NOT_VERIFIED_OUTCOMES:", ",".join(missing))
        return 1
    if jwt_hits:
        print("FAIL: possible JWT in log lines")
        return 1
    print("railway_shadow_log_parse=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
