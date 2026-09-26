# PlugWarden: Critique, round 8

Evidence paths:
- `r8/` = `<scratchpad>/shots/r8/` (designer)
- `rc/` = `<scratchpad>/shots/r8-critic/` (mine, captured live on :18095)
- Full sweep: `rc/sweep/`, 17 views × dark/light × 1440/390. Contact sheets: `rc/sheet-{d390,l390}.png`

All testing was read-only. Execute, Apply and Undo were intercepted in the browser, and none were sent. The only requests that reached the server were deploy plans (dry runs).

---

## Score: 9.5 / 10

## NITPICK-ONLY: **yes**

There are no P0s and no P1s. Everything left is cosmetic or content polish.

---

## Round-7 items: status

| # | Item | Status | Evidence |
|---|---|---|---|
| P1-1 | Remove from the drawer leaves focus behind the modal | **Fixed** | Focus lands on the confirm input. 20 Tabs gave 0 escapes. Escape returns focus to "CoreProtect details". `dev/a11y_focus.py` passes **9/9** (palette, shortcuts, review sheet, map-source, drawer, drawer→Remove, palette→Remove, confirm, 390 nav drawer) |
| P2-1 | Audit without paging | **Fixed** | 48 rows per page with "Load older", day headers (Today, Yesterday), and a "Changes only" filter (7 rows) |
| P2-2 | "12:10 AM–12:10 AM" ranges | **Fixed** | Equal-time ranges collapse to a single time |
| P2-3 | Segmented control clipped at 390 | **Fixed** | Full width (`r8/review-sheet-seg-390.png`) |
| P2-4 | voicechat listed twice | **Fixed** | Supporting lines now sit under the Not-running card as "Also logged: …". Known issues on elChapo01 dropped to "2 errors, 5 warnings" |

## 9.5+ items from round 7

| # | Item | Status | Notes |
|---|---|---|---|
| 2 | Since you last looked | **Done** | Server-side per-user marker, advanced on leaving the Dashboard or hiding the tab. Example: "Since you last looked (13h ago): 24 new updates (AxGraves 1.32.1, AxiomPaper 6.0.1+1.21.8, …) · 2 changes by tester@example.com". When nothing is new: "Nothing new since you last looked (3m ago)" |
| 3 | Changelog excerpts | **Done** | "What's new in 24.1" disclosure on the Updates page and in the Review sheet (5 of 5 rows), with keyword flags ("Java 27", "Breaking") and a "Full changelog" link |
| 4 | Actionable Not-running causes | **Done**, and very good | elChapo01: "**Port in use:** UDP port 24454 is already in use: another process or a second server holds it. Change the plugin's port or stop whatever uses it, then restart." M1-hub01: "**Missing dependency:** Install TheCore (TheCore-3.6.5.jar is on elChapo01), then restart. **Deploy TheCore from elChapo01**". The link pre-fills Deploy, and the plan shows "M1-hub01 · 1 change · new install TheCore-3.6.5.jar (3.6.5)" (`rc/deploy-prefill.png`). The Dashboard headline carries the causes too: "voicechat on elChapo01 (UDP port 24454 in use), Voting on M1-hub01 (needs TheCore)" |
| 5 | Audit as a ledger | **Done** | See P2-1 above |
| 6 | Cached health | **Done** | Cold load to the health section rendered: elChapo01 256 ms, M1-hub01 211 ms, M4-skyblock01 206 ms (was 816 ms). The table renders in about 200 ms |
| 7 | Undo toast | **Done** | "Deploy finished · 1 changed · View details · **Undo · 8s**" (`r8/undo-toast-1440.png`) |

**Sweep:** 68 shots with no page overflow and no console errors. Light and dark are consistent, and the 390 layouts hold, including the new cause text and the "Also logged" blocks.

---

## P0: none
## P1: none

## P2: nitpicks

1. **Markdown escapes leak into changelog excerpts.** CoreProtect shows "Added support for Minecraft 26\\.2\\." and "…container interactions\\." Unescape `\.`, `\-`, `\(` and similar when converting Modrinth markdown to plain lines.
2. **Excerpts start with boilerplate.** CoreProtect's first three lines are the Patreon/Discord plea plus a bare "Changelog" heading, so the actual changes are pushed below the fold (`rc/` Updates, CoreProtect expanded). Skip leading lines that match `patreon|discord|support the project|sponsor` and lone heading words (`Changelog`, `Changes`, `What's new`) before taking the first 6 lines.
3. **The Undo toast and the job dock stack in the same corner.** In `r8/undo-toast-1440.png` the toast sits directly above the dock and covers the M7-bending01 tile's Review button. When a dock is open for the same job, put the Undo button in the dock header instead of a separate toast.
4. **The "Since you last looked" count lacks context on the first long gap.** "24 new updates" after 13 h equals the whole backlog, so it reads as noise. When new equals total, say "all 24 updates are new since you last looked", or list the 3 most recent and "+21".

---

## What stands between this and 9.5+

**Nothing material.** At 9.5 this meets the bar. What remains is small-scale finishing, in order of value:

1. **Changelog excerpt cleanup** (P2-1, P2-2): about 20 lines in the excerpt extractor. This is the most visible remaining roughness, because it sits in the trust-critical review step.
2. **Merge the Undo toast into the job dock** (P2-3).
3. **Smarter "new since" wording** (P2-4).
4. **Beyond 9.5, optional:** a one-line **"Restart checklist"** on the Dashboard after any apply, listing servers with pending restarts and their Mark-restarted buttons in one place. It closes the loop between "updated" and "actually running", and I think it's what would take this to 9.7.
