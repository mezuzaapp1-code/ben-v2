"""R-019 verification: parse Railway logs for shadow_auth_check (no secrets printed)."""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

REQUIRED_OUTCOMES = ("auth_missing", "auth_invalid", "auth_valid")
JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
BEARER_RE = re.compile(r"Bearer\s+[A-Za-z0-9._-]{20,}", re.I)
SK_RE = re.compile(r"sk_(test|live)_[A-Za-z0-9]+")


def _redact_sample(line: str, max_len: int = 220) -> str:
    s = JWT_RE.sub("eyJ…[REDACTED]", line)
    s = BEARER_RE.sub("Bearer [REDACTED]", s)
    s = SK_RE.sub("sk_…[REDACTED]", s)
    if len(s) > max_len:
        s = s[:max_len] + "…"
    return s


def parse_log_file(path: Path) -> dict:
    outcomes: Counter[str] = Counter()
    shadow_lines: list[dict] = []
    request_id_lines = 0
    jwt_leaks = 0
    bearer_leaks = 0
    sk_leaks = 0
    auth_header_leaks = 0

    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if JWT_RE.search(raw):
            jwt_leaks += 1
        if BEARER_RE.search(raw):
            bearer_leaks += 1
        if SK_RE.search(raw):
            sk_leaks += 1
        if re.search(r'"authorization"\s*:', raw, re.I) or "Authorization:" in raw:
            auth_header_leaks += 1

        if "shadow_auth_check" not in raw:
            continue

        obj: dict | None = None
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            # try substring JSON
            m = re.search(r"\{.*\}", raw)
            if m:
                try:
                    obj = json.loads(m.group(0))
                except json.JSONDecodeError:
                    obj = None

        if obj:
            if obj.get("operation") == "shadow_auth_check" or obj.get("operation") is None:
                outcome = obj.get("outcome")
                if outcome in REQUIRED_OUTCOMES + ("auth_error",):
                    outcomes[outcome] += 1
                if obj.get("request_id"):
                    request_id_lines += 1
                if obj.get("subsystem") == "auth" or obj.get("subsystem") is None:
                    shadow_lines.append(obj)
        else:
            for o in REQUIRED_OUTCOMES + ("auth_error",):
                if f'"outcome": "{o}"' in raw or f'"outcome":"{o}"' in raw:
                    outcomes[o] += 1
            if "request_id" in raw:
                request_id_lines += 1

    return {
        "outcomes": dict(outcomes),
        "shadow_event_count": sum(outcomes.values()),
        "request_id_lines": request_id_lines,
        "jwt_leaks": jwt_leaks,
        "bearer_leaks": bearer_leaks,
        "sk_leaks": sk_leaks,
        "auth_header_leaks": auth_header_leaks,
        "samples": shadow_lines[:3],
    }


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: verify_r019_production_logs.py railway_shadow_logs.txt")
        return 2
    path = Path(sys.argv[1])
    if not path.is_file():
        print(f"log_file=MISSING path={path}")
        return 2

    r = parse_log_file(path)
    print("outcomes:", r["outcomes"])
    print("shadow_event_count:", r["shadow_event_count"])
    print("request_id_lines:", r["request_id_lines"])
    print("jwt_leak_lines:", r["jwt_leaks"])
    print("bearer_leak_lines:", r["bearer_leaks"])
    print("sk_leak_lines:", r["sk_leaks"])
    print("auth_header_leak_lines:", r["auth_header_leaks"])

    missing = [o for o in REQUIRED_OUTCOMES if r["outcomes"].get(o, 0) < 1]
    leaks = r["jwt_leaks"] + r["bearer_leaks"] + r["sk_leaks"] + r["auth_header_leaks"]

    for i, sample in enumerate(r["samples"]):
        redacted = {k: sample.get(k) for k in ("timestamp", "level", "subsystem", "operation", "outcome", "request_id", "message")}
        print(f"sample_{i}:", json.dumps(redacted))

    if missing:
        print("NOT_VERIFIED_OUTCOMES:", ",".join(missing))
    if r["request_id_lines"] < 1:
        print("NOT_VERIFIED: request_id")
    if leaks:
        print("FAIL: log leakage detected")
        return 1
    if missing or r["request_id_lines"] < 1:
        return 1
    print("r019_log_verification=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
