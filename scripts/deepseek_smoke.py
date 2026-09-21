"""Opt-in live provider smoke test. Requires backend DEEPSEEK_API_KEY."""
import asyncio
import os
from pathlib import Path
import sys

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.providers.base_provider import ProviderStreamEnd
from services.providers.deepseek_provider import DeepSeekProvider


async def main():
    if not os.getenv("DEEPSEEK_API_KEY", "").strip():
        print("LIVE PROVIDER VERIFICATION REQUIRED: configure DEEPSEEK_API_KEY in the backend environment")
        return 2
    adapter = DeepSeekProvider()
    async with httpx.AsyncClient(timeout=httpx.Timeout(90, connect=5)) as client:
        for model in ("deepseek-flash", "deepseek-v4-pro"):
            count = 0
            end = None
            try:
                async for item in adapter.stream_message(
                    client, model=model, message="Reply with the word OK.", tenant_id="ben-smoke-test",
                ):
                    if isinstance(item, ProviderStreamEnd):
                        end = item
                    else:
                        count += len(item)
                if not count or end is None:
                    print(f"FAIL {model}: incomplete response")
                    return 1
                print(f"PASS {model}: streamed_chars={count}, usage={end.usage.usage_status}")
            except httpx.HTTPStatusError as exc:
                print(f"FAIL {model}: HTTP {exc.response.status_code}")
                return 1
            except Exception as exc:
                print(f"FAIL {model}: {type(exc).__name__}")
                return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
