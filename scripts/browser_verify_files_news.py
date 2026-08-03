"""Browser verification for News + File Library (Mission Control UI)."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
load_dotenv(_ROOT / ".env")
load_dotenv(_ROOT / "frontend" / ".env")
load_dotenv(Path(r"C:\BEN-V2") / ".env")

OUT = _ROOT / "data" / "browser_verify"
OUT.mkdir(parents=True, exist_ok=True)


async def api_checks() -> None:
    import httpx

    async with httpx.AsyncClient(base_url="http://127.0.0.1:8002", timeout=60.0) as client:
        news = await client.get("/api/news/top", params={"limit": 5})
        news.raise_for_status()
        body = news.json()
        assert isinstance(body.get("items"), list), body
        (OUT / "news_api.json").write_text(str({"status": news.status_code, "count": len(body["items"])}), encoding="utf-8")
        print("news_api_status", news.status_code, "items", len(body["items"]))


async def browser_checks() -> None:
    from playwright.async_api import async_playwright

    beta_pass = (os.getenv("VITE_BETA_PASSCODE") or os.getenv("BEN_BETA_PASSCODE") or "").strip()
    anon_org = (os.getenv("BEN_ANONYMOUS_ORG_ID") or "00000000-0000-0000-0000-000000000001").strip()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1400, "height": 900})
        page = await context.new_page()
        page.on("console", lambda msg: print("console", msg.type, msg.text[:180]))

        if beta_pass:
            await context.add_init_script(
                f"""
                localStorage.setItem('basalt-app-authorized', 'true');
                localStorage.setItem('basalt-beta-alias', 'browser-verify');
                localStorage.setItem('basalt-beta-org-id', '{anon_org}');
                """
            )

        await page.goto("http://127.0.0.1:5173/", wait_until="networkidle", timeout=90000)
        await page.wait_for_timeout(1500)

        # Desktop Mission Control may already be open; otherwise open menu.
        if not await page.get_by_text("Mission Control").count():
            await page.get_by_role("button", name="Open menu").click(timeout=5000)
            await page.wait_for_timeout(400)

        await page.screenshot(path=str(OUT / "01_home.png"), full_page=True)

        # --- News ---
        await page.get_by_role("button", name="News").click(timeout=5000)
        await page.wait_for_timeout(2500)
        await page.screenshot(path=str(OUT / "02_news.png"), full_page=True)
        news_html = await page.content()
        assert "Service is temporarily unavailable" not in news_html, "News shows unavailable"
        print("news_ui_ok")

        # Close news overlay
        close = page.locator(".news-overlay button[aria-label*='Close' i], .news-overlay__close, button:has-text('Close')").first
        if await close.count():
            try:
                await close.click(timeout=2000)
                await page.wait_for_timeout(400)
            except Exception:
                await page.keyboard.press("Escape")

        # --- Files ---
        if not await page.get_by_text("Mission Control").count():
            await page.get_by_role("button", name="Open menu").click(timeout=5000)
        await page.get_by_role("button", name="Files").click(timeout=5000)
        await page.wait_for_timeout(1200)
        await page.screenshot(path=str(OUT / "03_files.png"), full_page=True)

        upload_btn = page.locator("button.files-upload-btn")
        assert await upload_btn.count(), "Upload button missing"
        print("files_upload_button_visible")

        # Drive the File Library hidden input (same path as Upload button → picker).
        sample = OUT / "sample_upload.txt"
        sample.write_text("BROWSER_UI_UPLOAD_TOKEN for workspace files", encoding="utf-8")
        file_input = page.locator(".files-overlay input.files-file-input")
        assert await file_input.count(), "hidden file input missing"
        # Ensure input is interactable even if parent disabled transiently.
        await page.evaluate(
            """() => {
              const input = document.querySelector('.files-overlay input.files-file-input');
              if (input) { input.disabled = false; input.removeAttribute('disabled'); }
            }"""
        )
        async with page.expect_response(
            lambda r: "/api/workspaces/" in r.url and r.request.method == "POST",
            timeout=45000,
        ) as resp_info:
            await file_input.set_input_files(str(sample))
            # React 19 sometimes needs an explicit change dispatch after set_input_files.
            await page.evaluate(
                """() => {
                  const input = document.querySelector('.files-overlay input.files-file-input');
                  if (input) input.dispatchEvent(new Event('change', { bubbles: true }));
                }"""
            )
        upload_resp = await resp_info.value
        print("files_upload_http", upload_resp.status, (await upload_resp.text())[:240])
        await page.wait_for_timeout(2000)
        await page.screenshot(path=str(OUT / "04_files_after_upload.png"), full_page=True)
        body_txt = await page.inner_text(".files-overlay__panel")
        err = page.locator(".files-error")
        if await err.count():
            print("files_upload_error", (await err.first.inner_text())[:300])
        if "sample_upload.txt" in body_txt or "BROWSER_UI" in body_txt:
            print("files_upload_row_visible")
        elif "select an active workspace" in body_txt.lower() or "select a workspace" in body_txt.lower():
            print("files_needs_workspace_message_ok")
        else:
            print("files_upload_result_text", body_txt[:500].replace("\n", " | "))

        # Close File Library before composer interactions
        close_files = page.locator("button.files-overlay__close")
        if await close_files.count():
            await close_files.click(timeout=3000)
            await page.wait_for_timeout(400)
        else:
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(400)

        # --- Composer + ---
        plus = page.locator("button.composer-capsule__plus")
        await plus.click(timeout=8000)
        await page.wait_for_timeout(300)
        await page.screenshot(path=str(OUT / "05_composer_plus.png"), full_page=True)
        attach = page.get_by_role("menuitem", name="Attach file")
        assert await attach.count(), "Attach file menu item missing"
        print("composer_attach_menu_ok")

        await browser.close()


async def main() -> int:
    await api_checks()
    await browser_checks()
    print("OK artifacts", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
