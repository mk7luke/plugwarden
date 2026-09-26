"""Screenshot helper: python dev/shoot.py <url-path> <out.png> [--w 1440 --h 900] [--full] [--click selector ...] [--wait ms]"""
import argparse, asyncio
from playwright.async_api import async_playwright
ap = argparse.ArgumentParser()
ap.add_argument("path"); ap.add_argument("out")
ap.add_argument("--w", type=int, default=1440); ap.add_argument("--h", type=int, default=900)
ap.add_argument("--full", action="store_true"); ap.add_argument("--click", action="append", default=[])
ap.add_argument("--wait", type=int, default=800); ap.add_argument("--base", default="http://127.0.0.1:8095")
a = ap.parse_args()
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": a.w, "height": a.h})
        errs = []
        pg.on("console", lambda m: m.type == "error" and errs.append(m.text))
        pg.on("pageerror", lambda e: errs.append(str(e)))
        await pg.goto(a.base + a.path, wait_until="networkidle")
        await pg.wait_for_timeout(a.wait)
        for sel in a.click:
            await pg.click(sel); await pg.wait_for_timeout(a.wait)
        await pg.screenshot(path=a.out, full_page=a.full)
        await b.close()
        for e in errs: print("CONSOLE ERROR:", e)
asyncio.run(main())
