"""Regenerate the README screenshots from a running dev server (dev/serve.sh on 18095).

    .venv/bin/python dev/readme_shots.py [--base http://127.0.0.1:18095] [--out docs/screenshots] [--only stats,matrix]

Read-only: every POST/PUT/DELETE except the deploy *plan* preview and the update *plan* is aborted.
"""
import argparse, asyncio, re
from pathlib import Path
from playwright.async_api import async_playwright

ap = argparse.ArgumentParser()
ap.add_argument("--base", default="http://127.0.0.1:18095")
ap.add_argument("--out", default="docs/screenshots")
ap.add_argument("--only", default="", help="comma-separated shot names (file names without .png); default: all")
a = ap.parse_args()
ONLY = set(filter(None, a.only.split(",")))
want = lambda name: not ONLY or name in ONLY
OUT = Path(a.out); OUT.mkdir(parents=True, exist_ok=True)
ALLOWED_WRITES = re.compile(r"/api/v2/(deploy/plan|updates/plan)$")


async def guard(route):
    r = route.request
    if r.method != "GET" and not ALLOWED_WRITES.search(r.url.split("?")[0]):
        return await route.abort()
    await route.continue_()


async def page(browser, w=1440, h=900, theme="dark"):
    ctx = await browser.new_context(viewport={"width": w, "height": h}, device_scale_factor=2 if w < 800 else 1,
                                    color_scheme=theme)
    p = await ctx.new_page()
    await p.route("**/*", guard)
    return p


async def go(p, path, theme="dark", wait=1500):
    await p.goto(f"{a.base}/?theme={theme}#{path}", wait_until="networkidle")
    await p.wait_for_timeout(wait)


async def main():
    async with async_playwright() as pw:
        b = await pw.chromium.launch()

        if want("dashboard"):
            p = await page(b)
            await go(p, "/dashboard")
            await p.screenshot(path=OUT / "dashboard.png")

        if want("dashboard-light"):
            p = await page(b, theme="light")
            await go(p, "/dashboard", theme="light")
            await p.screenshot(path=OUT / "dashboard-light.png")

        if want("matrix"):
            p = await page(b)
            await go(p, "/plugins")
            await p.screenshot(path=OUT / "matrix.png")

        if want("review-sheet"):
            p = await page(b)
            await go(p, "/dashboard")
            await p.get_by_role("button", name=re.compile(r"Review & update all")).click()
            await p.wait_for_timeout(2500)
            det = p.locator("details summary", has_text=re.compile("What's new")).first
            if await det.count():
                await det.click(); await p.wait_for_timeout(400)
            await p.screenshot(path=OUT / "review-sheet.png")

        if want("server-health"):
            p = await page(b)
            await go(p, "/servers/M1-hub01")
            await p.screenshot(path=OUT / "server-health.png")

        if want("deploy-guard"):
            p = await page(b)
            await go(p, "/deploy?source=elChapo01&targets=M1-hub01,M3-hunger01,M4-skyblock01&paths=LuckPerms/config.yml", wait=3500)
            await p.screenshot(path=OUT / "deploy-guard.png")

        if want("activity"):
            p = await page(b)
            await go(p, "/activity")
            await p.screenshot(path=OUT / "activity.png")

        if want("palette"):
            p = await page(b)
            await go(p, "/dashboard")
            await p.keyboard.press("Control+k"); await p.wait_for_timeout(300)
            await p.keyboard.type("update core"); await p.wait_for_timeout(600)
            await p.screenshot(path=OUT / "palette.png")

        # Stats: wait for the load sequence (gauge sweep, lines, count-ups) to finish.
        if want("stats"):
            p = await page(b)
            await go(p, "/stats", wait=4000)
            await p.screenshot(path=OUT / "stats.png")

        if want("mobile-dashboard"):
            p = await page(b, w=390, h=844)
            await go(p, "/dashboard")
            await p.screenshot(path=OUT / "mobile-dashboard.png")

        await b.close()


asyncio.run(main())
