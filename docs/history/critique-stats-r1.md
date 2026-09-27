# PlugWarden Stats page: Critique, round 1

Evidence paths:
- `sr1/` = `<scratchpad>/shots/stats-r1/` (designer)
- `sc/` = `<scratchpad>/shots/stats-critic-r1/` (mine, captured live on :18096, the demo sandbox with real data from an 11-server network)
- Capture scripts: `sc/cap.py` (animation frames, full pages, themes, widths, reduced motion), `sc/cap2.py` (hover, Tab order, keyboard stepping), `sc/cap3.py` (edge states made by rewriting the `/api/v2/stats` response in the browser), `sc/cap4.py` (390 scroll positions)

All testing was read-only. Nothing that writes was clicked. The edge states were produced by rewriting the response in the browser, so nothing reached the server.

---

## Score: 7.8 / 10

## NITPICK-ONLY: **no**

There are 2 P1s, both about the above-the-fold frame the README hero depends on. The engineering underneath is strong: every number I checked adds up, the empty states are honest, keyboard and screen-reader support is better than most commercial dashboards, and reduced motion works. The page doesn't yet lead with its best story.

---

## What I verified is correct

| Check | Result |
|---|---|
| Gauge | 109 / (109 + 49) = 69.0%. 109 + 49 + 98 = 256 installs, which matches Network |
| Donut | 41 / 68 = 60.3%. 41 + 27 = 68 distinct plugins, which matches Network |
| Update lag bins | 3+19+4+9+5+86+32 = 158, which matches "158 updates measured" |
| Waiting bins | 2+3+17+13+1+3+10 = 49, which matches "49 installs" and the gauge's 49 outdated |
| Plugin spread | Plugins on 5 or more servers: 1+7+11+7 = 26, which matches the subtitle. 35 on one server matches |
| 30-day KPI | 25 changes, 15 by jobs. This matches the daily data (Sep 6: 9, Sep 12: 1, Sep 26: 15) |
| Heatmap | Dec 10 2025 (67) lands on a Wednesday. Busiest day Aug 17 (89) matches the weekly peak |
| Honesty | The trend is hidden until there are 2 or more checks and the page says so ("Not shown yet: freshness trend…"). With a faked 3-check trend (`sc/edge-trend.png`) it renders "+8 pts over 3 checks since Sep 20", which is good |
| Keyboard | One Tab stop per chart. Arrows, Home, End and Escape work. The heatmap moves by 7 across and 1 down. The live region announces "1–3 days, 19 updates · 12%". Every chart has a Data table. Tab order is sensible (`sc/kb-heatmap.png`, `sc/kb-lag.png`) |
| Reduced motion | Everything is final at 300 ms (`sc/reduced-300ms.png`) |
| Overflow and errors | scrollWidth equals the viewport at 1024 and 390. There were 0 console errors |
| Light theme | Consistent. The heatmap ramp and the status colors hold (`sc/light-1440-full.png`) |

---

## P0: none

## P1

### P1-1. The widest chart above the fold shows the wrong window, with the wrong mark, for the data it holds

Evidence: `sc/seq-4000.png`, `sc/hover-timeline.png`, `sc/hover-timeline-end.png`, `sc/dark-1440-full.png` (heatmap row)

- **Wrong window.** "Plugin changes per week" shows the last 26 weeks. The daily data behind the heatmap directly below it covers 12 months, and that history has a second, larger event: **124 jar changes on Dec 10–27 2025** (67 on Dec 10 alone), plus activity in January. The hero chart cuts all of that off. What's left is 26 weeks with one spike (89 on Aug 17), three small bumps and ~560 × 240 px of flat zero line. The flat line is the lead's observation, and it is real. It is also partly self-inflicted, because the 52-week view has two events and reads as a history rather than an accident.
- **Wrong mark.** Weekly counts are discrete, but a monotone spline turns them into a continuous signal. The 89 becomes a needle about 2 weeks wide with a filled gradient that reads as area. The teal "Made by PlugWarden jobs" line sits on the zero baseline for 25 of 26 weeks, so it looks like the axis. In the last week both series are 15, and the teal is drawn over the orange, so the "All jar changes" line appears to end at 0 (`sc/hover-timeline-end.png`).
- **Redundant.** The same series appears three times: the KPI sparkbars (26 weeks), this chart (26 weeks) and the heatmap (365 days). Only the heatmap tells the full story. It is the third thing you see.

**Fix (no faking involved):**
1. Match the window to the heatmap: 52 weeks. The backend already sends 365 daily counts, so weekly bins over 52 weeks are a client-side sum.
2. Draw **bars**, stacked as "by PlugWarden jobs" (teal, bottom) plus "other changes" (accent, top), so the two series can't hide each other and always add up to the total. Annotate the two largest weeks directly on the chart ("67 · Dec 8", "89 · Aug 17") with dates only. Don't invent reasons.
3. Cut the height from 280 to about 170 px. The data has two events, and 280 px of mostly empty grid makes it look emptier.
4. Or drop it completely and let the heatmap carry the history (see P1-2). The KPI sparkbar is already the "shape of recent weeks".

### P1-2. The 1440 × 900 frame doesn't work as a README hero

Evidence: `sc/seq-4000.png` (the exact README frame; `dev/readme_shots.py` shoots `/stats` at 4000 ms, dark, 1440 × 900), `sc/hover-donut-zero.png`

In the frame, the top ~290 px is strong. The gauge, the four KPIs and the green-to-blue arc look good. The bottom ~450 px is:
- a mostly flat line chart (P1-1)
- a donut whose legend is **3 of 5 rows at "0 · 0%"** (Spiget, GitHub, Hangar). On this network the donut is really a two-slice chart. The zero rows take 90 px and suggest the feature is broken. Hovering a zero row turns the center into a large "0 / Spiget / 0%" (`sc/hover-donut-zero.png`)
- the 900 px edge cuts through the "Data table" footers of both cards (y≈883–900), so the frame ends on half a UI row.

The most visually rich and useful views are all below the fold. **Servers** has 10 rows of green/blue/gray stacked bars with health and disk. **Waiting updates** is the actionable histogram with the three oldest named. **Daily activity** is the full-width heatmap with two clusters.

**Fix:**
1. Put these in row 2 under the hero: **Freshness by server** (span 8, the Scoreboard's stacked bars promoted to a proper horizontal bar chart, sorted worst-first, with M9-aerons-server01 at 36% at the top) and **Waiting updates** (span 4, with its "InvSeePlusPlus 361 d" list). Both are real, colorful and dense, and they answer "where is the work?"
2. Move Daily activity to row 3 and move the weekly chart (if kept, per P1-1) next to Update sources.
3. Donut: show only sources with a count. List the zero sources in one muted line under the legend: "Also checked: Spiget, GitHub, Hangar (no matches on this network)". That keeps the honesty and removes three rows.
4. Size row 2 so a card boundary lands at about 870–880 px at 1440 × 900, or have `readme_shots.py` clip to the bottom of the last whole card in view.

---

## P2: polish

1. **Outdated in blue on the gauge (lead's question 3).** Semantically it is consistent: blue is the app's "update available" color everywhere (Updates badge, scoreboard, Waiting updates bars), and "outdated" means an available update is waiting, which is actionable rather than alarming. Red or amber would be wrong because those mean broken or warning elsewhere in the app. The problem is visual weight. The blue arc is as saturated as the green, so the gauge reads as a two-category ring ("green vs blue team") rather than "69% of the way to full". There's no visible empty track. **Fix:** keep the hue, draw the outdated arc at about 55% opacity (or as `--update-line`) at its current width, and keep the legend swatch solid. Then the eye reads green = score and blue = remaining work (`sc/seq-4000.png`).
2. **Scoreboard % and bar use different denominators.** elChapo01 shows "50%", but its green segment is about 30% of the bar, because the bar includes the gray "not tracked" installs and the % excludes them (`sc/dark-1440-full.png`, Servers). **Fix:** draw the bar as current plus outdated only (so green length equals the %). Show the untracked count as muted text after it ("+18 not tracked"), or as a short separate gray stub with a gap.
3. **Two "untracked" figures with different units sit side by side.** The gauge says "98 not tracked" (installs) and the donut says "No source 27 · 40%" (distinct plugins). A reader tries to reconcile 98 with 27 and 38% with 40%. **Fix:** put the unit in the label: "98 installs not tracked" and "27 plugins with no source". In the donut subtitle, "68 distinct plugins (256 installs)".
4. **"Most shared" uses 10 as the denominator, but Bukkit plugins can't run on the Velocity proxy.** "Essentials 9/10" reads as missing on one server. It is actually on all 9 Bukkit servers. **Fix:** use compatible servers per family as the denominator ("9/9"), or drop the denominator and give the bar max = compatible servers. The same applies to the Plugin spread subtitle ("on half the servers or more").
5. **Precision mismatches.** The donut center is "60.3%", the legend is "60%". The KPI says "oldest waiting 361.2 days", the list says "361 d". "47.1 days" is fine, but tenths of a day at 361 is noise. **Fix:** whole days from 10 up. Pick one precision for the donut: center 60%, or legend with one decimal.
6. **The "30 days" KPI has a 26-week sparkline.** The label says "Jar changes · 30 days" and the bars underneath are 26 weekly bars. **Fix:** 30 daily bars, or caption the spark "last 26 weeks".
7. **Jargon: "p75".** It appears in the KPI sub, the LagRange labels and the Update lag subtitle. This app's copy bar is plain language. **Fix:** "3 in 4 landed within 78 days" in the sub, and "most" instead of "p75" on the range label.
8. **Em-dash aside in the indexing banner.** "(40/256) — figures are provisional until indexing finishes." (`sc/edge-nocheck.png`). **Fix:** "Reading plugin jars after a restart (40/256). Figures may change until this finishes."
9. **Count-up and gauge arc are out of sync.** At 400 ms the number shows **40%** while the green arc has drawn about 5% of the sweep (`sc/seq-400.png`). At 1000 ms the number is final but the blue arc is still drawing (`sc/seq-1000.png`). The number runs 1400 ms ease-out-cubic, the arc runs linear. **Fix:** drive both from one clock (same duration and easing), or let the number follow the arc's progress.
10. **Count-up digits shift inside the reserved width.** Mid-animation, "11" and "0" are right-aligned in the ghost box, so they sit visibly indented from the label ("  11", "   0 plugin installs", `sc/seq-400.png`). **Fix:** left-align `.cu-live` over the ghost. With `tabular-nums` the settle is invisible.
11. **Unequal histogram bins drawn as equal widths.** In Update lag, "1–3mo" spans 63 days and "<1d" spans 1. The tall 86-bar is partly just a wide bin. The bins are labelled so this is acceptable, but a one-line note ("bins widen to the right") or a per-day density tooltip would make it honest at a glance.
12. **"Most shared plugins" has about 170 px of empty panel** under its 10 rows because it stretches to the Servers table's height (`sc/dark-1440-full.png`). Show the top 12 to 14 (the data has 26 on 5 or more servers), or align the card to its content (`align-self: start`).
13. **Footprint says "256 jars across 11 servers" while the hero says "on 10 servers".** M9-homestead01 (Fabric, 0 jars) is counted in one place and not in the other. Say "across 10 servers" (servers with plugins) everywhere.
14. **Heatmap legend cuts.** "1 / 2–8 / 9–66 / 67+" are quantiles of 20 active days, so "9–66" is a meaningless range. Use fixed steps that scale by max (for example 1, 2–5, 6–20, 21+). At 390 the legend wraps and leaves an orphan "· 1 / 2–8…" line (`sc/m390-1.png`).
15. **At 390 the heatmap opens scrolled to the end**, which hides the December event off-screen with no cue. Add a left-edge fade or "← Dec" hint, or at narrow widths render a 26-week heatmap with a toggle (`sc/m390-1.png`).
16. **No-check state layout.** With freshness, lag and history all null (`sc/edge-nocheck.png`), the hero shows one KPI across two thirds of the width. Update sources goes to span 12 with the donut and legend floating in the middle of 1170 px, and Plugin spread becomes a 1170 px chart of 10 fat bars. **Fix:** without freshness, show Network, Update sources and Plugin spread as a 3-up row in the hero area, and keep cards at span 4 or 6 so no single card goes full width.
17. **Tabbable donut legend rows have no separators for screen readers.** Their accessible text is "Modrinth4160%". Add an `aria-label` ("Modrinth: 41 plugins, 60%").

---

## What would make this a stunning README hero

The top band is already good. What's missing is one visual that is both **big and full**, and the data has two:

1. **Lead with "Freshness by server" under the gauge.** Ten horizontal stacked bars (green current, soft blue outdated) sorted worst-first, with the server name, the % and a tiny "not running" chip on M1-hub01. It fills its space edge to edge, it is colorful without any faked data, it animates well (bars growing left to right, staggered 40 ms), and it answers the question an ops panel should answer first. Put Waiting updates next to it for the "how long has it been" angle.
2. **Promote the 12-month heatmap to row 3, full width, as the history visual.** The two clusters (December build-out, the August update) and the recent PlugWarden-driven week tell a real story. Give it about 16 px cells so it has presence at 1440.
3. **Retire the smoothed weekly line**, or rebuild it as 52 stacked bars at about 170 px per P1-1.
4. **Frame the crop.** Row 2 should end cleanly just above 900 px, with no card footers cut in half.
5. **One accent of motion that survives in a still.** The README image is a static PNG. Consider a 4-second GIF or MP4 of the reveal (gauge sweep, count-ups, bars growing) as a linked "see it animate" beneath the hero, and keep the PNG as the always-loads fallback.

With the two P1s fixed and items 1, 2, 5, 8 and 9 above done, this page should score 9+.
