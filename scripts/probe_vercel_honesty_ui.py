import re
import sys

import httpx

b = "https://ben-v2.vercel.app"
h = httpx.get(b, timeout=30).text
m = re.search(r'src="(/assets/index-[^"]+\.js)"', h)
if not m:
    print("js=NOT_FOUND")
    sys.exit(0)
t = httpx.get(b + m.group(1), timeout=60).text
print("js_asset", m.group(1))
print("has_expert_status", "expert_status" in t)
print("has_disclaimer", "Based on available expert responses" in t)
print("has_Unavailable_timeout", "Unavailable: timeout" in t)
