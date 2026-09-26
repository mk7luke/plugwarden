#!/usr/bin/env python3
"""Keyboard-focus regression check for every modal surface (dialogs, sheets, drawers, palette).

For each modal: open it the way a user would, then assert that
  1. focus lands inside the modal,
  2. 25 x Tab and 10 x Shift+Tab never leave it,
  3. Escape closes it and focus returns to a live element outside any modal
     (for hand-offs such as plugin drawer -> Remove dialog: back to the original opener).

Read-only: every mutating request (POST/PUT/DELETE) is aborted in the browser, so nothing reaches the server.

Usage:  .venv/bin/python dev/a11y_focus.py [base_url]      (default http://127.0.0.1:18095/)
Exit code 0 when every check passes, 1 otherwise.
"""
import sys
import time

from playwright.sync_api import sync_playwright

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:18095/").rstrip("/") + "/"
MODAL = '[aria-modal="true"]'

results = []


def active_in(page, sel):
    return page.evaluate("(s) => { const a = document.activeElement; return !!a && a !== document.body && !!a.closest(s); }", sel)


def describe_active(page):
    return page.evaluate("""() => { const a = document.activeElement; if (!a || a === document.body) return 'BODY';
      return (a.tagName.toLowerCase() + ' ' + (a.getAttribute('aria-label') || a.textContent || '').trim()).slice(0, 60); }""")


def check(page, name, sel, opener_desc=None, tabs=25, back=10):
    ok, notes = True, []
    try:
        page.wait_for_selector(sel, timeout=8000)
    except Exception:
        results.append((name, False, "modal did not open"))
        return
    time.sleep(0.4)
    if not active_in(page, sel):
        ok = False
        notes.append(f"initial focus outside: {describe_active(page)}")
    escapes = 0
    for key, n in (("Tab", tabs), ("Shift+Tab", back)):
        for _ in range(n):
            page.keyboard.press(key)
            if not active_in(page, sel):
                escapes += 1
    if escapes:
        ok = False
        notes.append(f"{escapes} of {tabs + back} Tab stops outside")
    page.keyboard.press("Escape")
    time.sleep(0.35)
    if page.locator(sel).count():
        ok = False
        notes.append("Escape did not close it")
    else:
        where = describe_active(page)
        if where == "BODY" or active_in(page, MODAL):
            ok = False
            notes.append(f"focus not restored ({where})")
        elif opener_desc and opener_desc not in where:
            ok = False
            notes.append(f"focus returned to '{where}', expected '{opener_desc}'")
    results.append((name, ok, "; ".join(notes) or "ok"))


def main():
    with sync_playwright() as p:
        b = p.chromium.launch()
        errs = []

        def page(w=1440, h=900):
            pg = b.new_page(viewport={"width": w, "height": h})
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.route("**/api/v2/**", lambda r: r.abort() if r.request.method in ("POST", "PUT", "DELETE", "PATCH") else r.continue_())
            return pg

        x = page()
        # 1. Command palette
        x.goto(BASE + "#/"); x.wait_for_selector(".tile", timeout=30000)
        x.locator(".topbar input, .search-btn, [aria-label*='Search']").first.focus()
        x.keyboard.press("Control+k")
        check(x, "command palette", ".palette")

        # 2. Shortcut sheet
        x.locator("nav a").first.focus(); x.keyboard.press("?")
        check(x, "shortcut sheet", '[aria-modal="true"]')

        # 3. Review sheet from a server page
        x.goto(BASE + "#/servers/elChapo01")
        x.wait_for_selector(".page-head button:has-text('Review '):not([disabled])", timeout=30000)
        btn = x.locator(".page-head button:has-text('Review '):not([disabled])").first
        btn.focus(); x.keyboard.press("Enter")
        check(x, "review sheet", ".sheet", opener_desc="Review")

        # 4. Map-source dialog (row menu)
        chip = x.locator("button.tag-btn:visible").first
        if chip.count():
            chip.focus(); x.keyboard.press("Enter")
            check(x, "map-source dialog", '[aria-modal="true"]', opener_desc="No update source")

        # 5. Plugin drawer, and 6. drawer -> Remove hand-off (the r7 P1)
        x.goto(BASE + "#/plugins"); x.wait_for_selector(".mx-open", timeout=30000)
        opener = x.locator(".mx-open:not([disabled])").first
        name = opener.get_attribute("aria-label")
        opener.focus(); x.keyboard.press("Enter")
        check(x, "plugin drawer", ".sheet", opener_desc=name)
        opener.focus(); x.keyboard.press("Enter")
        x.wait_for_selector(".sheet")
        x.locator(".sheet button:has-text('Remove from servers')").click()
        check(x, "drawer → Remove dialog", '[aria-labelledby="rm-t"]', opener_desc=name)

        # 7. Palette -> Remove hand-off
        x.goto(BASE + "#/"); x.wait_for_selector(".tile")
        x.keyboard.press("Control+k"); x.wait_for_selector(".palette input:focus")
        x.keyboard.type("remove coreprotect", delay=15); time.sleep(0.4); x.keyboard.press("Enter")
        check(x, "palette → Remove dialog", '[aria-labelledby="rm-t"]')

        # 8. Confirm dialog (turning on automatic installs; the save request is aborted anyway)
        x.goto(BASE + "#/updates"); x.wait_for_selector("text=Check & apply", timeout=30000)
        for _ in range(3):
            if not x.locator(".scrim").count():
                break
            x.keyboard.press("Escape"); time.sleep(0.3)
        if x.locator(".scrim").count():
            results.append(("(state) leftover modal before confirm test", False, describe_active(x)))
        x.locator("label:has-text('Check & apply')").first.click()
        save = x.locator("button:has-text('Save policy')")
        save.focus(); x.keyboard.press("Enter")
        check(x, "confirm dialog", '[role="alertdialog"], .dialog[aria-modal="true"]', opener_desc="Save policy")

        # 9. Navigation drawer on a phone
        m = page(390, 844)
        m.goto(BASE + "#/"); m.wait_for_selector(".tile", timeout=30000)
        mb = m.locator("button[aria-label='Open menu']")
        mb.focus(); m.keyboard.press("Enter")
        check(m, "nav drawer (390)", ".drawer", opener_desc="Open menu")

        b.close()

    width = max(len(n) for n, _, _ in results)
    for n, ok, note in results:
        print(f"{'PASS' if ok else 'FAIL'}  {n.ljust(width)}  {note}")
    if errs:
        print("page errors:", errs)
    failed = [r for r in results if not r[1]] or errs
    print(f"\n{len(results) - len([r for r in results if not r[1]])}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
