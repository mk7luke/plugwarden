# PlugWarden: Critique, round 7

Evidence paths:
- `r7/` = `<scratchpad>/shots/r7/` (designer)
- `rc/` = `<scratchpad>/shots/r7-critic/` (mine, captured live on :18095)
- Contact sheets: `rc/sheet-{d1440,l1440,d390,l390}.png`, built from 17 views × 4 modes (68 full-page shots in `rc/sweep/`)

All testing was read-only. Every Execute, Apply and Undo request was intercepted in the browser. Error states were produced by intercepting GETs in the browser.

---

## Score: 9.3 / 10

## NITPICK-ONLY: **no**

One P1: a keyboard-focus bug on the destructive Remove flow. Everything else is fixed or cosmetic.

---

## Scope checks

### AMP removal: clean ✓
- **API**: `GET /api/v2/amp/status`, `GET …/console/stream`, `POST …/power`, `POST /restarts/rolling` and `POST …/console` all return **404**.
- **Code**: `app/amp.py`, `dev/mock_amp.py`, `components/amp.js` and `tests/test_amp.py` are gone. There are no `ampCache`, `openRolling`, `openConsole`, `AmpPanel` or `LGT_AMP_*` references in `app/`, the docs or compose.
- **Settings**: no `amp_*` keys. `auto_restart_*` is retired and stripped from stored settings on load (`settings.py:95`, with a test in `test_round5.py:573`).
- **Docs and compose**: the remaining "amp" strings are the host `amp` user (uid 1001), which is correct, and the historical "formerly LGT AMP Sync" name.
- **Legacy jobs**: the five old rolling-restart jobs render under **Activity → Other 5**, readable with results and log (`rc/activity-other.png`).

### r6 P1-3: Known issues de-noised ✓ **Fixed**
| Server | r6 | r7 |
|---|---|---|
| elChapo01 | "6 errors, 18 warnings" | **4 errors** shown, "Show 6 warnings", "3 plugins announce updates in their logs (FastAsyncWorldEdit, NBTAPI, ViaVersion) — see Updates" |
| M1-hub01 | "2 errors, 10 warnings" | **2 errors**, "Show 3 warnings", 2 update notices |
| M4-skyblock01 | – | **3 errors** (including DiscordSRV IDENTIFY Rate Limit), "Show 4 warnings" |
| M8-lifesteal01 | 11 rows incl. tilde banner and "join my discord" | **2 errors**, "Show 3 warnings", 1 update notice |

- No decoration lines, and no discord or GitHub banner lines.
- Voicechat's three consecutive errors are merged into one issue.
- The PhoenixCratesLite INFO-continuation title the lead flagged is **no longer present**: it doesn't appear in any expanded list.
- **r6 P2-1** (doubled "(on every start)"): **fixed**. It now reads "voicechat — disabled itself after startup (on every start) · Not running".
- **Tile names**: **fixed**. All 11 are measured untruncated. Flags are now icon plus number (`↑14 ⇄2 ✕1`) (`rc/dash.png`).

### Designer's 9.5 polish pass
- **Error states with Retry**: I injected a 500 into the primary GET of all seven views: Dashboard, Deploy, Server, Updates, Matrix, Activity and Settings. **7 of 7** show "Couldn't load this · <message> · GET … → 500 · Retry", and **7 of 7 recover** after clicking Retry (`rc/err-*.png`).
- **Focus traps**: I tabbed 40 times and Shift-tabbed 10 times inside each:
  - review sheet: 0 escapes, 40 distinct stops
  - plugin drawer: 0 escapes
  - palette: 0 escapes
  - shortcuts: 0 escapes
  - Remove dialog opened from the drawer: **fails** (see P1-1)
- **Review sheet 390**: stacked rows, and the footer reads "14 changes · 14 plugins · 1 server · 34.3 MB download · ✓ All compatible · all hashes verified" (`r7/review-sheet-390.png`). ✓
- **Feed rows**: the time is right-aligned, the server is in the title, and "Undo failed" is a tag on the origin row. ✓
- **Speed**: cold loads measured Dashboard 299 ms, Matrix 189 ms, Updates 63 ms, Server 816 ms (the log health parse). Fine.
- **Sweep**: no page overflow in 68 shots (the Settings tab strip at 390 scrolls inside its own container) and no console errors.

---

## P0: none

## P1

**P1-1 · Remove opened from the plugin drawer leaves keyboard focus behind the modal**
- Repro: Plugins → click CoreProtect (drawer) → "Remove from servers…".
- The "Remove CoreProtect?" dialog opens (`rc/remove-dialog.png`), but `document.activeElement` is the **"CoreProtect details" button in the matrix behind it**. The drawer's close handler restores focus to its opener *after* the Remove dialog has opened.
- Tab then walks the matrix cells under the scrim ("CoreProtect on elChapo01: 23.4", "…M1-hub01: 23.1", …), with 30 of 30 stops outside the dialog. Pressing Enter on one of those cells silently dismisses the Remove dialog.
- The type-to-confirm field and the Cancel/Remove buttons can't be reached from the keyboard without clicking first.
- Why P1: this is the destructive flow, the brief is keyboard-first, the polish pass claims "focus traps on all dialogs", and it violates the `aria-modal` contract (WCAG 2.4.3 focus order).
- Fix:
  1. In the drawer's close handler, skip focus restoration when another modal is opening. Easiest: have `openRemove()` close the drawer with `{restoreFocus:false}` and let the Remove dialog's own `data-autofocus` (the confirm input) take focus.
  2. Add a regression test: open drawer → Remove → assert `activeElement` is inside `[role=dialog][aria-labelledby=rm-t]` and that 20 Tabs never leave it.
  3. Audit other modal-from-modal hand-offs the same way (drawer → Align goes to Deploy; palette → Remove…).

---

## P2: cosmetic or minor

1. **The Audit tab has no paging**: 167 rows in one table (`#/activity?tab=audit`), with no "Load older" and no day grouping.
2. **Collapsed audit ranges repeat a single time**: "×2 · 12:10 AM–12:10 AM". When start equals end, show `×2 · 12:10 AM`.
3. **The review-sheet segmented control is clipped at 390**: "By plugin | By server" is cut at the right with a fade (`r7/review-sheet-390.png`). Make it full-width with two equal halves on mobile.
4. **voicechat shows twice** on elChapo01: in the red "not running" card and again as the known issue "voicechat — Disabling Simple Voice Chat". Drop the disable line from Known issues when the same plugin is already in the not-running card.

---

## What stands between this and 9.5+

In priority order. The first item is required for NITPICK-ONLY. The rest are what would move a demanding daily user from "very good" to "I'd show this off".

1. **Fix the modal-from-drawer focus hand-off (P1-1)** and add the regression test. ~30 min. *Unblocks the verdict.*
2. **"What changed since you last looked" on the Dashboard.** Everyday use is "open, glance, leave". Store `last_seen` per user (localStorage is fine) and add one quiet line under the headline: `Since yesterday 9:14 PM: 3 new updates (LuckPerms 5.5.23, …), CoreProtect updated on 6 servers by luke, 1 plugin stopped running on M1-hub01`. This is the single biggest delight and speed win for the #1 job.
3. **Changelog excerpts in the Review sheet.** Expand a row to show the first 6 lines of the Modrinth changelog (already fetched by id), with "breaking", "migration", "Java 21" and "config" words highlighted in amber. It saves opening 24 tabs before "Apply 64 changes" and is the biggest trust improvement left.
4. **Make "Not running" actionable.** On the red card, add the concrete next step when it's recognisable. BindException: `UDP 24454 is already in use — another process or a second server holds the port`. Missing dependency: `needs PlayerStats (not installed)` with an Install link into Deploy. Voting load failure: show the jar and `Could not load` reason inline. Turns diagnosis into a 10-second read.
5. **Audit tab usability** (P2-1/P2-2): day headers ("Today", "Yesterday", "Sep 24"), "Load older" after 50 rows, and a filter chip for "Changes only" that hides "viewed diff". Audit is where trust is proven. It should read like a ledger, not a log dump.
6. **Offline-friendly speed on the server page** (816 ms cold). Cache the health report per (server, latest-log mtime) and serve it instantly. Show the known-issues section as a skeleton only on the first-ever parse. Target under 250 ms for every view.
7. **Undo confidence.** After an Apply or Deploy, the result sheet already has Undo. Add a small toast with a 10-second "Undo" countdown that becomes the Activity link. Operators undo mostly in the first minute, and this makes it one keystroke (`z`).
8. **Micro-polish sweep**: P2-3 (segmented control at 390), P2-4 (voicechat duplicated), P2-2 (equal-time ranges), and a subtle 120 ms fade when a tile's badge count changes after a check, so updates feel live rather than swapped.

With 1 done, the verdict flips to NITPICK-ONLY: yes and I'd score about 9.4. Items 2–4 are what I'd expect to lift it to 9.6–9.7.
