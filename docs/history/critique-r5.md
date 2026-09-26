# PlugWarden (LGT AMP Sync 2.0): Critique, round 5

Evidence paths:
- `r5/` = `<scratchpad>/shots/r5/` (designer)
- `rc/` = `<scratchpad>/shots/r5-critic/` (mine)
- Contact sheets: `rc/sheet-{d1440,l1440,d390,l390}.png`, built from 16 views × 4 modes (64 full-page shots in `rc/sweep/`)

**Note on the environment:** the sandbox moved from :8095 to **:18095** mid-review. It was restarted with the excerpt fix. The full sweep ran on the pre-restart build, and I re-verified every health-related finding on :18095.

All testing was read-only. Every Execute, Apply, Undo and "Mark restarted" request was intercepted in the browser and answered with a fake 409. Settings PUTs were intercepted and faked, so nothing reached the server.

---

## Score: 9.3 / 10 (6.5 → 7.5 → 8.5 → 9.0 → 9.3)

## NITPICK-ONLY: **no**

One P1 remains. It is narrow, but it sits in the new health feature: the thing the canary and "Known issues" promise to tell you.

**What I verified:**
- **Rename:** the title is "Dashboard · PlugWarden", the new shield mark is in place, and "AMP Sync" appears nowhere in the live views. (`r5/dashboard-canary-failed-1440.png` still shows "AMP Sync" because it's a fixtures shot. Just regenerate it.)
- **Policy validation (r4 P1-1): fixed.** Changes per run is now a Limit toggle plus a field.
  - `-5`, empty, `0`, `abc` and `501` each give `aria-invalid="true"`, the inline message "Enter a whole number from 1 to 500", and the footer "Fix the highlighted field to save", with Save disabled.
  - `20` enables Save. Turning the limit off is now an explicit state, not an empty field (`rc/policy-invalid.png`).
- **Canary copy (r4 P1-2): fixed in principle.** The copy now describes a real check: *"The rest follow only after elChapo01 restarts, its log shows each updated plugin enabling cleanly, and 24 h pass. A plugin that fails to enable there is held back."* `health.py` checks the plugin's enable line, hard load errors, disabling before `Done (`, and new error signatures compared with earlier runs. But see P1-1: "enabling cleanly" misses one common failure shape.
- **Known-issues excerpt off-by-one: fixed on :18095.** Titles now come from the matched line (`Plan — Failed to enable geolocation`, `voicechat — Voice chat server error`), with a `latest.log:635` locator. I also checked mechanically that every excerpt in the API contains its signature line: elChapo01, M1-hub01, M4-skyblock01, M8-lifesteal01 and M0-proxy01, 0 misses across 201 items. The pre-restart build (:8095) showed the preceding line as the title (for example "voicechat — Running in offline mode" for a BindException). If any r5 designer shots were taken before the fix, retake them.
- **Failed-at-start surfacing:** M1-hub01 shows the red card "1 plugin failed to start in the last run · Voting — failed to load or enable (on every start) · Log excerpt · 2026-09-24-2.log.gz:28", and it works at 390 light (`rc/srv-M1-hub01-390-light.png`).
- **Startup check on a job:** "Plugin startup after this job" gives the honest state "M1-hub01 hasn't restarted since the change — restart it, then check again" (`rc/job-startup-1440.png`).
- **r4 P2s:**
  - Audit collapses repeats ("viewed diff ×4 · 11:49 PM–11:51 PM") and its URL is `#/activity?tab=audit`.
  - Apply is disabled on a stale plan.
  - The Fabric server page has no Deploy button, and `?targets=M9-homestead01` yields "no targets".
  - The palette has "Theme: dark (current) / Theme: light".
  - The redaction label no longer picks up the MaxMind comment ("4 secret values hidden (Key_pass, Password, Secret, Store_pass)").
  - No middle-ellipsis cells remain in the matrix.
  - Kept values use a neutral chip.
- **Sweep:** no page overflow in any of the 64 shots. The only element past the edge is the Settings tab strip at 390, which is inside its own scroller. No console errors apart from the 409s I injected. Light and dark are consistent across all 16 views.

### Benchmark tasks (clicks): unchanged from r4 at 2 / 5 (4 via Ctrl+K) / 3 / 0–2.

---

## Round-4 items: status

| # | Item | Status |
|---|---|---|
| P1-1 | "No limit" / invalid max-changes can't be saved | **Fixed** (Limit toggle, inline errors, Save gated) |
| P1-2 | Canary claims a health check that doesn't exist | **Fixed** (real log check, copy describes it). The one gap is P1-1 below |
| P2-1 | Audit floods | **Fixed** (×N collapse with a time range) |
| P2-2 | Audit tab not in the URL | **Fixed** (`?tab=audit`; `#/activity/access` no longer 404s) |
| P2-3 | Apply enabled on a stale plan | **Fixed** |
| P2-4 | Kept values shown as warnings | **Fixed** |
| P2-5 | Redaction label parsed a comment | **Fixed** |
| P2-6 | Deploy to a Fabric server | **Fixed** |
| P2-7 | Palette theme | **Fixed** |
| P2-8 | Matrix middle-ellipsis | **Fixed** |
| P2-9 | Failed undo leaves no mark on the original job | **Not fixed** (P2-1 below) |

---

## P0: none

## P1

**P1-1 · A plugin that disables itself right after `Done (` is reported healthy, and Known issues calls its errors "informational, not failures"**
- Evidence: elChapo01 on :18095 (`rc/srv-elChapo01-1440-dark.png`) and `GET /api/v2/servers/elChapo01/health`.
- The log (`latest.log`):
  - line 626: `Done (90.041s)!`
  - lines 633–635: `[ERROR] [voicechat] Failed to bind … / Failed to run voice chat at UDP port 24454 … / Voice chat server error` followed by `java.net.BindException: Address already in use`
  - lines 656–657: `[ERROR] [voicechat] Disabling Simple Voice Chat`, then `Disabling voicechat v2.6.6`, all in the same second as Done.
- The API reports voicechat as `status: "healthy", reason: "enabled (with known issues)"`. The server page header reads **"6 warnings that also appeared in earlier starts — informational, not failures"**, although all six are `[ERROR]` lines and one of them says the plugin shut itself down. The voicechat row in the plugin table shows only "Update available". Voice chat is **not running** on elChapo01, and nothing in the UI says so.
- Root cause: `health.analyse_run` only treats `Disabling <name> v` as a failure when `i < done_i` (`health.py:275`). Plugins that start network listeners or database pools on the first tick after startup (voicechat, Geyser, DiscordSRV, Votifier, Plan's web server) fail *after* `Done (`. Also, "seen in earlier starts" is used as a synonym for "not a failure". That's right for the canary's question ("did this update cause it?") but wrong for the server page's question ("is this plugin working?").
- Why P1: this is the headline promise of the new feature, and the canary copy says "its log shows each updated plugin enabling cleanly". An update that makes a plugin disable itself post-Done *with* new ERROR lines is still caught by the new-signature path. But a silent post-Done disable (INFO `Disabling X v` only) passes the canary, and the server page actively labels a dead plugin as fine.
- Fix:
  1. In `analyse_run`, treat `Disabling <name> v` as `failed · "disabled itself after startup"` when it occurs within `GRACE_SECONDS` of start and is not part of a shutdown sequence. `_cut_shutdown` already finds "Stopping server", so any disable before that cut counts.
  2. Keep "preexisting" as a separate flag. Status stays `failed` with the tag `(on every start)`, exactly like the M1 Voting card.
  3. Server page: move voicechat into the red "failed to start" card: `voicechat — disabled itself after startup (on every start) · BindException: UDP 24454 already in use`. Put a red `Not running` chip on its table row.
  4. Rename the Known-issues header to count by level: `2 errors, 1 warning that also appeared in earlier starts — not caused by recent changes`. Drop "informational, not failures".
  5. Add a test fixture using this exact elChapo01 log.

---

## P2: cosmetic or minor

1. **Failed undo is still invisible on the original job** (r4 P2-9). Deploy `20260926-062414-0e1f2e` shows "Done", with an Undo button and no mention that `…062418` (its undo) failed with EXDEV (`rc/job-startup-1440.png`). Add `Undo failed 48m ago — retry` with a link to the failed job.
2. **Startup failures don't reach the Dashboard.** The M1-hub01 tile and the headline show nothing about Voting failing on every start. Only the server page does. Add a small red `1 failed` chip on the tile, and a headline line `1 plugin failing to start: Voting on M1-hub01`, consistent with how restarts are surfaced.
3. **The Limit row sits higher than its neighbour.** "Changes per run" starts about 20px above "Minimum release age" in the Safety grid (`rc/policy-invalid.png`). Align the two columns on their labels. The inline error text should also be linked with `aria-describedby`.
4. **Regenerate the fixture screenshot** `r5/dashboard-canary-failed-1440.png`. It still shows the old "AMP Sync" name in the sidebar (fixtures mode).
5. **Settings tab strip at 390** scrolls horizontally with no affordance: "Auto-update" is cut off at the right edge. Add a fade mask on the overflow edge, or wrap it to two rows.

---

## To reach NITPICK-ONLY: yes
Fix P1-1. The classifier change is about 5 lines in `health.py` plus a test with the real elChapo01 log. Relabel the Known-issues header, and optionally do P2-1 and P2-2. After that, everything left is cosmetic.
