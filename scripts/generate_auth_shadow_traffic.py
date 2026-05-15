"""Generate prod auth shadow traffic: missing, invalid, optional valid (signed-in)."""
from __future__ import annotations

import os
import re
import sys
import time

import httpx

BASE = os.environ.get("BEN_API_BASE", "https://ben-v2-production.up.railway.app")
TENANT = "00000000-0000-0000-0000-000000000001"


def _post(client: httpx.Client, path: str, headers: dict | None = None) -> int:
    h = headers or {}
    if path == "/chat":
        r = client.post(
            f"{BASE}/chat",
            json={"message": "R-019 shadow traffic chat", "tenant_id": TENANT, "tier": "free"},
            headers=h,
        )
    else:
        r = client.post(
            f"{BASE}/council",
            json={"tenant_id": TENANT, "question": "R-019 shadow traffic council?"},
            headers=h,
        )
    print(f"POST {path} -> {r.status_code}")
    return r.status_code


def _signed_in_bearer() -> str | None:
    try:
        from clerk_session_bearer import get_bearer

        b = get_bearer()
        if b:
            print("signed_in=clerk_session_token")
            return b
    except Exception:
        pass

    email = os.environ.get("CLERK_TEST_EMAIL", "").strip()
    password = os.environ.get("CLERK_TEST_PASSWORD", "").strip()
    if not email or not password:
        return None
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright=NOT_INSTALLED signed_in=SKIPPED")
        return None

    token_holder: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        def on_request(req):
            auth = req.headers.get("authorization") or req.headers.get("Authorization", "")
            if auth.startswith("Bearer ") and len(auth) > 30:
                token_holder.append(auth)

        page.on("request", on_request)
        page.goto(
            os.environ.get("BEN_FRONTEND_URL", "https://ben-v2.vercel.app"),
            wait_until="networkidle",
            timeout=60_000,
        )
        page.get_by_role("button", name=re.compile(r"sign in", re.I)).first.click()
        page.wait_for_timeout(2000)
        for sel in ['input[name="identifier"]', 'input[type="email"]', 'input[name="emailAddress"]']:
            if page.locator(sel).count():
                page.locator(sel).first.fill(email)
                break
        for sel in ['button:has-text("Continue")', 'button[type="submit"]']:
            if page.locator(sel).count():
                page.locator(sel).first.click()
                break
        page.wait_for_timeout(1500)
        for sel in ['input[name="password"]', 'input[type="password"]']:
            if page.locator(sel).count():
                page.locator(sel).first.fill(password)
                break
        for sel in ['button:has-text("Continue")', 'button:has-text("Sign in")', 'button[type="submit"]']:
            if page.locator(sel).count():
                page.locator(sel).first.click()
                break
        page.wait_for_timeout(8000)
        browser.close()

    if not token_holder:
        print("signed_in=NO_BEARER_CAPTURED")
        return None
    print("signed_in=OK bearer_captured")
    return token_holder[-1]


def main() -> int:
    invalid = {"Authorization": "Bearer not-a-real-jwt"}
    signed = _signed_in_bearer()
    signed_headers = {"Authorization": signed} if signed else None

    with httpx.Client(timeout=120.0) as client:
        _post(client, "/chat")
        _post(client, "/council")
        _post(client, "/chat", invalid)
        _post(client, "/council", invalid)
        if signed_headers:
            _post(client, "/chat", signed_headers)
            _post(client, "/council", signed_headers)
        else:
            print("signed_in_traffic=SKIPPED")

    print("traffic_generation=done")
    time.sleep(3)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
