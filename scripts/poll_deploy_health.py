"""Poll Railway / Vercel deploy health."""
import re
import sys
import time

import httpx

BASE = "https://ben-v2-production.up.railway.app"
FE = "https://ben-v2.vercel.app"


def main() -> int:
    for i in range(12):
        h = httpx.get(f"{BASE}/health", timeout=15)
        j = h.json()
        v = j.get("version", "?")
        db = (j.get("checks") or {}).get("database")
        print(f"Railway {i+1}: status={h.status_code} version={v} db={db}")
        if h.status_code == 200 and db == "ok":
            break
        time.sleep(10)
    fe = httpx.get(FE, timeout=20)
    print(f"Vercel HTML: {fe.status_code}")
    m = re.search(r'src="(/assets/[^"]+\.js)"', fe.text)
    if m:
        js = httpx.get(FE.rstrip("/") + m.group(1), timeout=60)
        print(f"JS: {m.group(1)} status={js.status_code} bytes={len(js.content)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
