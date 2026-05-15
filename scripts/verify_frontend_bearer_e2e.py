"""E2E checks for Clerk sign-in UI and Bearer headers (no token printing)."""
from __future__ import annotations

import json
import os
import re
import sys

BASE = os.environ.get("BEN_FRONTEND_URL", "https://ben-v2.vercel.app")
API = os.environ.get("BEN_API_BASE", "https://ben-v2-production.up.railway.app")


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright=NOT_INSTALLED")
        return 2

    failures: list[str] = []
    chat_headers: dict[str, str] | None = None
    council_headers: dict[str, str] | None = None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        def on_request(req):
            nonlocal chat_headers, council_headers
            if req.method != "POST":
                return
            if req.url.rstrip("/").endswith("/chat"):
                chat_headers = dict(req.headers)
            elif req.url.rstrip("/").endswith("/council"):
                council_headers = dict(req.headers)

        page.on("request", on_request)
        page.goto(BASE, wait_until="networkidle", timeout=60_000)

        sign_in = page.get_by_role("button", name=re.compile(r"sign in", re.I))
        visible = sign_in.count() > 0 and sign_in.first.is_visible()
        print(f"sign_in_button_visible={visible}")
        if not visible:
            failures.append("sign_in_button_not_visible")

        email = os.environ.get("CLERK_TEST_EMAIL", "").strip()
        password = os.environ.get("CLERK_TEST_PASSWORD", "").strip()
        if email and password:
            sign_in.first.click()
            page.wait_for_timeout(2000)
            # Clerk modal — common selectors
            for sel in [
                'input[name="identifier"]',
                'input[type="email"]',
                'input[name="emailAddress"]',
            ]:
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
            page.wait_for_timeout(5000)

            input_box = page.locator('input[placeholder*="Message"]')
            if input_box.count():
                input_box.first.fill("bearer e2e smoke test")
                page.get_by_role("button", name=re.compile(r"^send$", re.I)).click()
                page.wait_for_timeout(45_000)
                page.get_by_role("button", name=re.compile(r"council", re.I)).click()
                page.wait_for_timeout(90_000)
            else:
                failures.append("message_input_not_found")
        else:
            print("signed_in_flow=SKIPPED_NO_TEST_CREDENTIALS")

        browser.close()

    def check_auth_header(label: str, headers: dict[str, str] | None, required: bool) -> None:
        if headers is None:
            if required:
                failures.append(f"{label}_request_not_observed")
            return
        auth = headers.get("authorization") or headers.get("Authorization", "")
        has_bearer = auth.startswith("Bearer ") and len(auth) > 20
        print(f"{label}_authorization_bearer={'PRESENT' if has_bearer else 'MISSING'}")
        if required and not has_bearer:
            failures.append(f"{label}_missing_bearer")
        # Never print token
        if "eyJ" in json.dumps(headers):
            failures.append(f"{label}_possible_jwt_in_headers_dump")

    require_bearer = bool(os.environ.get("CLERK_TEST_EMAIL"))
    check_auth_header("chat", chat_headers, require_bearer)
    check_auth_header("council", council_headers, require_bearer)

    if failures:
        print("FAILURES:", ", ".join(failures))
        return 1
    print("e2e_checks=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
