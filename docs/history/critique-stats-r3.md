# PlugWarden Stats page: Critique, round 3 (final check)

Evidence paths:
- `sc3/` = `<scratchpad>/shots/stats-critic-r3/` (mine, captured live on :18096)
- `sr3/` = `<scratchpad>/shots/stats-r3/` (designer)
- README candidate: `docs/screenshots/stats.png`

The same scripts as round 2 were re-run (`sc3/cap.py`, `cap3.py`, `cap4.py`, `cap5.py`). All testing was read-only. The edge states were produced by rewriting the `/api/v2/stats` response in the browser.

---

## Score: 9.5 / 10

## NITPICK-ONLY: **yes**

There are no P0s and no P1s. All seven round-2 P2s are fixed. Two new cosmetic items remain, both about how the new trend line is dated.

---

## Round-2 items: status

| # | Item | Status | Evidence |
|---|---|---|---|
| 1 | Weekly axis skips October | **Fixed** | The axis now reads Oct, Nov, Dec … Aug, Sep (`sc3/dark-1440-full.png`) |
| 2 | Backend em dashes and "pass force:true" | **Fixed** | `grep "—" app/engine.py` returns no user-visible strings. The shared-folder path now logs "…is shared with X; deleting anyway (force)" |
| 3 | Double colon in the Deploy failing list | **Fixed** | `deploy.js:98` is now `${server} · ${item}: ${detail}`. A repo-wide grep for "—" in UI strings (js and templates, comments excluded) finds nothing |
| 4 | Identification counted three ways | **Fixed** | The wording is plugins-first everywhere. Dashboard says "41 of 68 plugins identified · checked 5m ago" (`sc3/app-dashboard.png`). The Stats donut center says "60% of plugins identified". Update sources has an info tip defining "Identified". The fixtures match |
| 5 | No-check state repeats figures and renders "60 %" | **Fixed** | The hero is Network plus Shared plugins. The Update sources KPI is gone (the card covers it), and the Plugin spread subtitle no longer repeats "35 on just one" (`sc3/edge-nocheck.png`) |
| 6 | Footprint left column empty | **Fixed** | Four figures in a row (jars on disk, backups, jobs run, jobs by kind), then the disk list in two balanced columns of 5 (`sc3/dark-1440-full.png`) |
| 7 | "Busiest day" dropped at 390 | **Fixed** | It wraps to a second row: "BUSIEST DAY Mon, Aug 17 (89)" (`sc3/m390-0.png`) |

**Rechecked this round:**
- **Tests.** No console errors. scrollWidth equals the viewport at 1024 and 390. Reduced motion is final at 300 ms.
- **Numbers.** Everything still adds up: 69%, 60%, both histogram totals, per-server x/y.
- **Assistive technology.** Weekly keyboard stepping and the live region ("Week of Dec 8, 98 jars changed, 0 by PlugWarden jobs, 98 other") work. The donut legend labels read correctly. The new "Identified" tip is in the Tab order after the weekly chart.
- **README image.** `docs/screenshots/stats.png` matches the live 1440 × 900 frame apart from "Checked 3m/4m ago". It ends on whole cards and now carries a real trend line ("No change over 2 checks since Sep 27"). Nothing is faked.

---

## P0: none
## P1: none

## P2: nitpicks

1. **The trend date is in UTC, so it reads as tomorrow here.** The trend caption uses `shortDate(trend[0].at.slice(0, 10))`, which formats with `timeZone: "UTC"` (`stats.js:21, 224`). The checks ran at 2026-09-27 02:41 UTC, which is Sep 26, 7:41 PM on this machine (America/Los_Angeles). A US operator on the evening of Sep 26 reads "since Sep 27". UTC is right for the date-only week and day buckets, but the trend points are full timestamps. **Fix:** format trend dates with local time (`new Date(t.at).toLocaleDateString(undefined, { month: "short", day: "numeric" })`), or use `relTime` ("since 4h ago").
2. **The two trend checks are 2 seconds apart.** They ran at 02:41:02 and 02:41:04. "No change over 2 checks" is true, but a flat line across 2 seconds says nothing about change over time, and in the README image it reads as "flat for a while". **Fix (either works):** only plot checks at least about an hour apart (keep the last one per hour), or put the span in the caption ("No change between 2 checks, 2 s apart"). For the README, it's worth re-shooting after the sandbox's next scheduled or manual check on a later day. Until then the current frame is honest, just not informative.

---

## What stands between this and 9.5+

It is at 9.5. What remains is the trend line's dating (items 1 and 2), which will fix itself in practice once real checks accumulate hours or days apart, apart from the time zone.

Summary of the three rounds:
- **Round 1: 7.8.** The fold led with a flat, windowed spline and zero rows.
- **Round 2: 9.3.** The fold leads with Freshness by server and Waiting updates. The history is a 52-week stacked bar chart with both real events labelled. Every denominator is explicit.
- **Round 3: 9.5.** Copy and consistency are clean app-wide, the empty and indexing states are tight, and there is a real trend line.

The page is honest, dense and consistent with the rest of PlugWarden, and the README image shows real data from a real network.
