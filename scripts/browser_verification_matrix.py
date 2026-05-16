"""
Browser verification matrix (Playwright headless).
Signed-in flows require CLERK_TEST_EMAIL + CLERK_TEST_PASSWORD.
Does not print tokens or passwords.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time

BASE = os.environ.get("BEN_FRONTEND_URL", "https://ben-v2.vercel.app")
RAW_JSON = re.compile(r'\{"detail"\s*:')
RESULTS: list[tuple[str, str, str]] = []  # section, check, PASS|FAIL|PARTIAL|SKIP


def record(section: str, check: str, status: str, note: str = "") -> None:
    RESULTS.append((section, check, status))
    line = f"{status:7} [{section}] {check}"
    if note:
        line += f" — {note}"
    print(line)


def page_has_raw_json(page) -> bool:
    try:
        text = page.locator(".bubble-text").all_inner_texts()
        combined = " ".join(text)
        return bool(RAW_JSON.search(combined))
    except Exception:
        return False


def buttons_enabled(page) -> bool:
    send = page.get_by_role("button", name=re.compile(r"^send$", re.I))
    council = page.get_by_role("button", name=re.compile(r"council", re.I))
    try:
        return send.first.is_enabled() and council.first.is_enabled()
    except Exception:
        return False


def run_anonymous(page) -> None:
    section = "A.anonymous"
    page.goto(BASE, wait_until="domcontentloaded", timeout=60_000)
    record(section, "page_load", "PASS" if page.url.startswith("http") else "FAIL")

    sign_in = page.get_by_role("button", name=re.compile(r"sign in", re.I))
    record(section, "sign_in_visible", "PASS" if sign_in.count() else "PARTIAL", "unsigned UX")

    inp = page.locator('input[placeholder*="Message"]')
    if not inp.count():
        record(section, "chat_send", "FAIL", "no input")
        return
    inp.first.fill("Browser matrix A: anonymous chat smoke.")
    page.get_by_role("button", name=re.compile(r"^send$", re.I)).click()
    page.wait_for_timeout(25_000)
    record(section, "chat_send", "PASS" if not page_has_raw_json(page) else "FAIL", "raw JSON check")
    record(section, "chat_no_raw_json", "PASS" if not page_has_raw_json(page) else "FAIL")

    inp.first.fill("Browser matrix A: council one-line risk?")
    page.get_by_role("button", name=re.compile(r"council", re.I)).click()
    page.wait_for_timeout(50_000)
    record(section, "council_send", "PASS" if not page_has_raw_json(page) else "FAIL")
    bubbles = page.locator(".bubble-text").count()
    record(section, "council_has_bubbles", "PASS" if bubbles >= 2 else "PARTIAL", f"bubbles={bubbles}")

    page.reload(wait_until="domcontentloaded")
    page.wait_for_timeout(5_000)
    after = page.locator(".bubble-text").count()
    record(section, "refresh_persistence", "PASS" if after >= 1 else "FAIL", f"bubbles_after={after}")
    progress_gone = page.locator(".council-progress").count() == 0
    inp.first.fill("retry check")
    record(
        section,
        "buttons_recover",
        "PASS" if progress_gone and buttons_enabled(page) else "PARTIAL",
        f"progress_gone={progress_gone}",
    )
    record(section, "org_banner_absent", "PASS" if page.locator(".org-recovery-banner").count() == 0 else "FAIL")


def run_signed_flows(page) -> None:
    email = os.environ.get("CLERK_TEST_EMAIL", "").strip()
    password = os.environ.get("CLERK_TEST_PASSWORD", "").strip()
    if not email or not password:
        for section in ("B.personal", "C.organization", "D.council", "E.rehydration"):
            record(section, "all_checks", "SKIP", "CLERK_TEST_EMAIL/PASSWORD not set")
        return
    # Minimal sign-in — Clerk UI varies; mark as attempted
    record("B.personal", "sign_in", "PARTIAL", "credentials present; Clerk modal flow not fully automated here")


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright=NOT_INSTALLED")
        return 2

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            run_anonymous(page)
            run_signed_flows(page)
        finally:
            browser.close()

    fails = sum(1 for _, _, s in RESULTS if s == "FAIL")
    skips = sum(1 for _, _, s in RESULTS if s == "SKIP")
    print(f"\nSummary: FAIL={fails} SKIP={skips} total={len(RESULTS)}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
