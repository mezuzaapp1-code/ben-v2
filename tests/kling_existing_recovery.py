"""GET-only investigation of one human-supplied existing fal request. Never submit."""
import asyncio
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit, urljoin

import httpx

ROOT = "https://queue.fal.run/fal-ai/kling-video"
MODEL = "fal-ai/kling-video/o3/standard/image-to-video"


def shape(raw):
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError):
        return {"body_type": "non_json"}, None
    report = {"body_type": type(value).__name__}
    if isinstance(value, dict):
        report["fields"] = {k: type(value[k]).__name__ for k in
            ("status", "request_id", "response_url", "error", "error_type", "detail", "video", "metrics") if k in value}
        if value.get("status") in ("IN_QUEUE", "IN_PROGRESS", "COMPLETED", "NOT_FOUND"):
            report["state"] = value["status"]
        text = json.dumps(value).lower()
        report["classifications"] = [name for name, words in {
            "expired": ("expired", "expiration"), "not_found": ("not found", "not_found"),
            "not_stored": ("not stored", "storage is disabled", "not available", "store-io", "store_io", "disabled"),
            "auth": ("unauthorized", "permission", "forbidden", "authentication"),
            "pending": ("in_queue", "in_progress", "not ready")}.items() if any(w in text for w in words)]
    return report, value


def permitted(url, reference):
    u = urlsplit(url)
    roots = (ROOT, f"https://queue.fal.run/{MODEL}")
    allowed = {f"{root}/requests/{reference}{suffix}" for root in roots
               for suffix in ("", "/", "/status", "/status/", "/response", "/response/")}
    return (url in allowed and u.scheme == "https" and not u.username and not u.password
            and not u.query and not u.fragment)


async def investigate(reference, key, destination, *, transport=None):
    assert re.fullmatch(r"[0-9a-f-]{36}", reference) and key
    destination.mkdir(parents=True, exist_ok=True)
    report = {"schema_version": "kling-read-recovery-v1", "generation_requests": 0,
              "model": MODEL, "requests": [], "result_recovered": False}
    try:
        async with httpx.AsyncClient(transport=transport or httpx.AsyncHTTPTransport(retries=0),
                trust_env=False, follow_redirects=False, timeout=25) as client:
            for suffix in ("/status", ""):
                url = f"{ROOT}/requests/{reference}{suffix}"
                for _ in range(3):
                    assert permitted(url, reference), "Unapproved recovery endpoint"
                    async with client.stream("GET", url, headers={"Authorization": f"Key {key}"}) as response:
                        raw = bytearray()
                        async for chunk in response.aiter_bytes():
                            raw.extend(chunk[:65537-len(raw)])
                            if len(raw) > 65536:
                                break
                        summary, value = shape(bytes(raw)) if len(raw) <= 65536 else ({"body_type": "oversized"}, None)
                        summary.update(method="GET", endpoint=url.replace(reference, "{request_id}"),
                                       http_status=response.status_code)
                        location = urljoin(url, response.headers.get("location", ""))
                        if 300 <= response.status_code < 400:
                            summary["redirect_allowed"] = permitted(location, reference)
                            summary["redirect_endpoint"] = location.replace(reference, "{request_id}") if permitted(location, reference) else "unapproved"
                        report["requests"].append(summary)
                        if response.status_code in (301, 302, 303, 307, 308) and permitted(location, reference):
                            url = location
                            continue
                        if response.status_code in (401, 403):
                            return report
                        if suffix == "" and response.status_code == 200 and isinstance(value, dict) and isinstance(value.get("video"), dict):
                            # Keep private URLs encrypted, not in sanitized evidence.
                            from tests.kling_recovery_snapshot import seal
                            private = {"provider_request_id": reference, "provider_result": value,
                                       "original_ben_execution_id": "9a8a062c-9d30-41cb-bd3e-8f791f4360fd"}
                            seal(destination / "provider-result.enc", private, key)
                            report["result_recovered"] = True
                        break
    finally:
        text = json.dumps(report, indent=2)
        assert key not in text and reference not in text
        (destination / "investigation.json").write_text(text)
    return report


if __name__ == "__main__":
    # No generic HTTP dispatcher or POST path exists in this program.
    asyncio.run(investigate(os.environ["KLING_EXISTING_REQUEST_ID"], os.environ["FAL_KEY"], Path("recovery-evidence")))
