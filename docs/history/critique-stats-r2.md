# PlugWarden Stats page: Critique, round 2

Evidence paths:
- `sr2/` = `<scratchpad>/shots/stats-r2/` (designer)
- `sc2/` = `<scratchpad>/shots/stats-critic-r2/` (mine, captured live on :18096, the demo sandbox with real data from an 11-server network)
- README candidate: `docs/screenshots/stats.png`
- Scripts: `sc2/cap.py` (animation frames, themes, widths, reduced motion), `sc2/cap3.py` (edge states made by rewriting the `/api/v2/stats` response in the browser), `sc2/cap4.py` (390 scroll positions), `sc2/cap5.py` (hover, keyboard, Tab order, per-card crops)

All testing was read-only. The edge states were produced by rewriting the response in the browser, so nothing reached the server.

---

## Score: 9.3 / 10

## NITPICK-ONLY: **yes**

There are no P0s and no P1s. Both round-1 P1s are fixed properly, and 17 of the 17 P2s are fixed. What's left is small copy and consistency work, mostly outside the Stats page.

---

## Round-1 items: status

| # | Item | Status | Evidence |
|---|---|---|---|
| P1-1 | Weekly chart: wrong window, spline, hidden series | **Fixed** | 52 weeks of stacked bars (PlugWarden jobs teal, other changes orange). The December event is now visible and labelled "98 · Dec 8" next to "89 · Aug 17", with dates only. I recomputed Dec 8 from the daily data: 67+4+18+2+7 = 98. The last week shows a solid teal bar, with a tooltip of "15 jars changed · 15 by PlugWarden jobs · 0 other" (`sc2/hover-weekly-last.png`). The chart is 226 px tall. The KPI sparkline is gone |
| P1-2 | 1440×900 frame doesn't work as a README hero | **Fixed** | Row 2 is Freshness by server (span 8, worst-first, with the M1-hub01 "1 not running" chip) plus Waiting updates with its "Waiting longest" list. The frame ends on whole cards (bottom at about 888 px). `docs/screenshots/stats.png` matches the live page pixel for pixel apart from the relative times (`sc2/seq-4000.png`) |
| 1 | Gauge blue weight | **Fixed** | The outdated arc is at reduced opacity and the legend swatch stays solid. It now reads as green score plus remaining work, in both themes (`sc2/light-1440.png`) |
| 2 | Scoreboard % and bar denominators | **Fixed** | The bar is current plus outdated only, so green length equals the %. Untracked is text ("13/26 · +18 not tracked"). M9-aerons-server01: green is about 36% of the bar and the label says 36% |
| 3 | Unlabelled "untracked" units | **Fixed** | "98 installs not tracked" and "Where new versions come from · 68 plugins (256 installs)" |
| 4 | 9/10 denominators | **Fixed** | "9 of 9", with the subtitle "out of the servers on its platform" |
| 5 | Precision mismatches | **Fixed** | Donut 60% and legend 60%. "361 days" in the KPI and "361 d" in the list. 47 days, while values under 10 keep a decimal (8.5) |
| 6 | "30 days" KPI with a 26-week spark | **Fixed** | Replaced by a 60/40 split bar (15 of 25 by PlugWarden, which checks out) |
| 7 | "p75" jargon | **Fixed** | "3 in 4 landed within 78 days" and a "3 in 4" tick label |
| 8 | Em dash in the indexing banner | **Fixed** | "(40/256). Figures may change until this finishes." (`sc2/edge-nocheck.png`) |
| 9 | Count-up and arc out of sync | **Fixed** | At 400 ms the number reads 26% and the arc is at about 26% (`sc2/seq-400.png`) |
| 10 | Count-up digits indented | **Fixed** | Digits are left-aligned. The unit stays at its final position during the count, which is the correct no-shift choice |
| 11 | Unequal bins | **Fixed** | "Bins widen to the right: from under a day to over 91 days." under both histograms |
| 12 | Empty space in Most shared | **Fixed** | The card now sits in the lag/spread/shared trio at matching height |
| 13 | 11 vs 10 servers | **Fixed** | "256 jars across 10 servers" |
| 14 | Heatmap legend cuts | **Fixed** | Fixed steps: 0 / 1 / 2–5 / 6–20 / 21+ |
| 15 | 390 heatmap hides December | **Fixed** | A "Scroll left for earlier months" hint appears under the grid (`sc2/m390-0.png`) |
| 16 | No-check state layout | **Fixed** | The hero fills with real KPIs (Network, Update sources, Shared plugins). The cards run 3-up with no full-width stretching (`sc2/edge-nocheck.png`) |
| 17 | Donut legend screen-reader text | **Fixed** | "Modrinth: 41 plugins, 60%" and "No source found: 27 plugins, 40%" |

**Rechecked this round:**
- **Every number reconciles.** 109/(109+49) = 69%. 41/68 = 60%. Both histograms add up to their totals (158 and 49). Per-server x/y figures sum to the gauge's counts.
- **Tests.** No console errors. scrollWidth equals the viewport at 1024 and 390. Reduced motion is final at 300 ms.
- **Keyboard.** Weekly stepping announces "Week of Dec 8, 98 jars changed, 0 by PlugWarden jobs, 98 other" (`sc2/kb-weekly.png`). The Freshness-by-server rows are links with a full sentence for screen readers.

---

## P0: none
## P1: none

## P2: nitpicks

1. **The weekly axis skips October.** The labels read "Sep, Nov, Dec…" (`sc2/hover-weekly-last.png`, `sc2/dark-1440-full.png`). The first bar is a late-September week, it takes the first label, and the 30 px spacing rule then drops Oct. **Fix:** don't label a first month that has fewer than 2 weeks visible, or prefer to drop the first label instead of the second.
2. **User-visible em dashes remain in backend strings.** `app/engine.py:656` "shared with … — pass force:true" and `app/engine.py:733` "ROLLBACK FAILED (…) — use Undo on this job" both surface in job details and toasts. The first also leaks API jargon ("pass force:true") into the UI. **Fix:** "Shared with X. Tick 'Delete them anyway' to remove it." and "Rollback failed (…). Use Undo on this job."
3. **The em-dash sweep made one double colon.** In deploy.js the failing-items list is now `${server}: ${item}: ${detail}`, which reads as "M1-hub01: TheCore: missing dependency". **Fix:** `${server} · ${item}: ${detail}`, or "on" ("TheCore on M1-hub01: …"). I spot-checked the rest of the sweep's diff and screens (Dashboard, Activity, a Velocity server page, a Fabric server page, Deploy, Updates). The colon and period rewrites read naturally. Changing the empty-value "—" placeholders to "–" is harmless.
4. **Different pages count identification three ways.** Dashboard and Activity say "46 of 78 jars identified". Stats says "41 of 68 plugins have a source" and "98 installs not tracked". All three are correct for their own unit, but a reader who opens Dashboard and Stats back to back sees 46/78 and 41/68 for what feels like the same thing. **Fix:** a short glossary tooltip on the Stats donut ("Counted per plugin. The Dashboard counts distinct jar files: 46 of 78"), or pick one unit for the headline everywhere.
5. **The no-check state repeats two figures** (`sc2/edge-nocheck.png`). The hero KPIs "Update sources 60%" and "Shared plugins 33 · 35 on just one" repeat the donut and the Plugin spread subtitle directly below. The KPI also renders as "60 %" with a gap before the unit. **Fix:** acceptable as a fill, but tighten the unit spacing for "%". If a KPI is shown in the hero, drop the duplicate subtitle figure from its card.
6. **Footprint at 1440 leaves the left half empty** under "Jobs by kind", beside a 10-row disk list (`sc2/dark-1440-full.png`). **Fix:** put Jobs by kind to the right of the three figures, and run the disk list full width in two columns, or leave it as is. This is below every fold that matters.
7. **At 390 the heatmap figures drop "Busiest day"** without comment (`sc2/m390-0.png`). This is fine if intended. Otherwise, wrap it to a second row.

---

## What stands between this and 9.5+

Not much, and none of it is on the Stats page's structure:

1. **Items 2 and 3 above.** The app-wide rule is no em-dash asides and no jargon. The two backend strings are the last user-visible exceptions, and the double colon is a small regression from the sweep.
2. **Item 1 (the missing October).** Axis labels are where a careful reader checks whether a chart is trustworthy.
3. **The time dimension above the fold arrives with the second update check.** The hero's trend line (already built and hidden honestly) is the one thing that would make the README frame show change over time as well as current state. Once the demo sandbox has run a second check, re-shoot `docs/screenshots/stats.png` so the gauge carries its "+N pts over 2 checks" line. Don't fake it before then.
4. **Item 4.** Pick one headline unit for "identified" across Dashboard and Stats, or cross-reference them.

With 1–3 done this is a 9.5. The page is honest, dense, well sequenced, and it now leads with its most useful view.
