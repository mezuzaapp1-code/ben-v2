"""Probe Vercel deploy for Clerk publishable key (presence only, never prints key)."""
from __future__ import annotations

import re
import sys

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "https://ben-v2.vercel.app"


def main() -> int:
    r = httpx.get(BASE, follow_redirects=True, timeout=30)
    print(f"frontend_status={r.status_code}")
    m = re.search(r'src="(/assets/[^"]+\.js)"', r.text)
    if not m:
        print("js_asset=NOT_FOUND")
        print("publishable_key_in_bundle=NOT_VERIFIED")
        return 1
    js_path = m.group(1)
    js_url = BASE.rstrip("/") + js_path
    jr = httpx.get(js_url, timeout=60)
    print(f"js_asset={js_path}")
    print(f"js_status={jr.status_code}")
    has_pk = bool(re.search(r"pk_(live|test)_[A-Za-z0-9]+", jr.text))
    has_sign_in = "Sign in" in jr.text or "SignIn" in jr.text
    print(f"sign_in_ui_hints={has_sign_in}")
    print(f"publishable_key_in_bundle={'PRESENT' if has_pk else 'MISSING'}")
    return 0 if has_pk else 1


if __name__ == "__main__":
    raise SystemExit(main())
