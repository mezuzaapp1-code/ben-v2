"""Local verification for ben.ops JSON logging v1. Run with server on BASE_URL."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8002"
LOG_PATH = Path(sys.argv[2]) if len(sys.argv) > 2 else None
TENANT = "00000000-0000-0000-0000-000000000001"

REQUIRED_OPS = {
    "GET /health",
    "GET /ready",
    "POST /council",
    "provider_openai",
    "provider_anthropic",
    "synthesis",
    "db_ping",
}
OPTIONAL_OPS = {"db_migration_lookup"}
REQUIRED_FIELDS = {"timestamp", "subsystem", "operation", "request_id", "duration_ms", "outcome"}
SECRET_PATTERNS = [
    re.compile(r"sk-[a-zA-Z0-9]{10,}"),
    re.compile(r"sk_ant[a-zA-Z0-9_-]{10,}"),
    re.compile(r"Bearer\s+[a-zA-Z0-9._-]+"),
    re.compile(r"postgresql://[^\s\"]+", re.I),
]
FALLBACK_MARKER = "log serialization failed"


def parse_json_lines(raw: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        # Join wrapped PowerShell lines if needed
        if not line.endswith("}"):
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


def load_logs_from_file(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    records = parse_json_lines(text)
    if records:
        return records
    # Multiline JSON from PowerShell redirect: stitch lines starting with {
    buf = ""
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("{"):
            if buf:
                try:
                    records.append(json.loads(buf))
                except json.JSONDecodeError:
                    pass
            buf = s
        elif buf and s and not s.startswith("INFO:") and not s.startswith("At "):
            buf += s
        if buf.endswith("}"):
            try:
                records.append(json.loads(buf))
            except json.JSONDecodeError:
                pass
            buf = ""
    if buf:
        try:
            records.append(json.loads(buf))
        except json.JSONDecodeError:
            pass
    return records


def check_secrets(text: str) -> list[str]:
    hits = []
    for pat in SECRET_PATTERNS:
        if pat.search(text):
            hits.append(pat.pattern)
    return hits


def main() -> int:
    print(f"BASE={BASE}")
    council_body = {
        "tenant_id": TENANT,
        "question": "JSON logging v1 verification smoke?",
        "experts": ["Legal"],
    }
    with httpx.Client(timeout=120.0) as client:
        h = client.get(f"{BASE}/health")
        r = client.get(f"{BASE}/ready")
        c = client.post(f"{BASE}/council", json=council_body)

    print(f"GET /health -> {h.status_code}")
    print(f"GET /ready -> {r.status_code}")
    print(f"POST /council -> {c.status_code}")

    council_json = c.json() if c.headers.get("content-type", "").startswith("application/json") else {}
    shape_keys = sorted(council_json.keys()) if isinstance(council_json, dict) else []
    print(f"council response keys: {shape_keys[:20]}")

    if LOG_PATH is None:
        print("No log file path; API smoke only.")
        return 0 if c.status_code == 200 else 1

    records = load_logs_from_file(LOG_PATH)
    print(f"Parsed {len(records)} JSON log records from {LOG_PATH}")

    ops_seen: set[str] = set()
    failures: list[str] = []
    for rec in records:
        op = rec.get("operation")
        if op:
            ops_seen.add(str(op))
        if op in REQUIRED_OPS | OPTIONAL_OPS or (op and "GET /" in str(op)):
            missing = REQUIRED_FIELDS - set(rec.keys())
            if missing and rec.get("level") == "INFO" and "completed" in str(rec.get("message", "")):
                failures.append(f"{op} missing fields: {sorted(missing)}")
        raw_line = json.dumps(rec)
        sec = check_secrets(raw_line)
        if sec:
            failures.append(f"secret pattern in log op={op}: {sec}")

    for op in REQUIRED_OPS:
        if op not in ops_seen:
            failures.append(f"missing operation in logs: {op}")

    if FALLBACK_MARKER in LOG_PATH.read_text(encoding="utf-8", errors="replace"):
        failures.append("serialization fallback appeared in log stream")

    # Sample lines for report
    samples: dict[str, dict] = {}
    for rec in records:
        op = rec.get("operation")
        if op and op not in samples and rec.get("outcome"):
            samples[str(op)] = rec

    print("\n--- SAMPLE JSON LINES ---")
    for op in sorted(samples.keys()):
        print(json.dumps(samples[op], ensure_ascii=False))

    print("\n--- OPS SEEN ---", sorted(ops_seen))
    if failures:
        print("\n--- FAILURES ---")
        for f in failures:
            print(f"  {f}")
        return 1
    print("\nAll log checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
