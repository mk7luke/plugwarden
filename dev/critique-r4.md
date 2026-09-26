# LGT AMP Sync 2.0: Critique, round 4

Evidence paths:
- `r4/` = `/tmp/claude-1000/-home-luke-lgt-amp-sync/0cf2f751-d945-45e5-89cc-a0b56688015d/scratchpad/shots/r4/` (designer)
- `rc/` = `/tmp/claude-1000/-home-luke-lgt-amp-sync/0cf2f751-d945-45e5-89cc-a0b56688015d/scratchpad/shots/r4-critic/` (mine, captured live on :8095)
- `rc/sweep/` = every view × {dark 1440, light 1440, dark 390, light 390}: 14 views × 4 = 56 full-page shots. Contact sheets are `rc/sheet-{d1440,l1440,d390,l390}.png`

All testing was read-only. Every Execute, Apply and Undo request was **intercepted in the browser** and answered with a fake 409, so nothing reached the server. The server-side checks I triggered (a 400 settings validation, a 403 missing-CSRF, a 422 replace-folder and a 400 bad Host) are all rejected before any write.

---

## Score: 9.0 / 10 (6.5 → 7.5 → 8.5 → 9.0)

## NITPICK-ONLY: **no**

There are two P1s. Both are small (a validation mismatch and one sentence of copy), and neither is a safety bug in the deploy or update engines. After those two, what's left is cosmetic.

**What I checked and found solid:**

- **Security.**
  - A write without `X-Requested-With` gets 403 `missing x-requested-with: lgt-amp-sync header`.
  - A foreign `Host` gets 400.
  - A spoofed `Cf-Access-Authenticated-User-Email` is ignored (the user stays `local`).
  - `GET /` sends a strict CSP (`default-src 'self'`, hashed inline scripts, `form-action 'self'`).
  - `auth.check_startup()` refuses `LGT_AUTH=none` on a non-loopback bind.
  - The sidebar identity reads "local (dev)", and Settings says "Development session (no Cloudflare Access identity)".
- **Partial-run gating (r3 P1-1).** The plan banner says "1 item would fail — they'll be skipped if you execute" and offers "Remove failing items". The button reads "Execute 1 change · skip 1 that would fail". The confirm lists the refused file and reason (`rc/fail-keep.png`, `rc/fail-confirm.png`).
- **Error paths.**
  - A 409 on Execute gives the toast "Targets changed since this preview … The plan was refreshed" and re-plans automatically (`rc/exec-409.png`).
  - A 409 on Apply gives the inline card "This plan is out of date · Re-plan" (`rc/apply-409.png`).
  - A 422 on settings save gives the toast "Couldn't save policy" with the server's message.
  - A 500 on /overview gives an error card with Retry (checked in r2 and still true).
- **Enabling Check & apply** needs a confirm dialog ("Install updates automatically?") and the API requires `confirm_apply`.
- **`?` sheet**: complete and accurate, and focus goes to Close (`rc/help.png`).
- **Keyboard**: `j`/`k` works on tiles, and arrows, Space and `u` work in the matrix.
- **Matrix at 1440**: 10 servers fit with no horizontal scroll (scrollWidth 1166 = clientWidth), in both light and dark.
- **Layout**: no horizontal page overflow in any of the 56 sweep shots. The only element past the edge is the Settings tab strip at 390, which is inside its own scroller.
- **Light theme**: consistent across all 14 views. I found nothing broken in light that works in dark.
- **Undo robustness**: I found a real failed undo in history (`20260926-062418-03914b`: `OSError [Errno 18] Invalid cross-device link` while backing up the replaced jar). That would matter in production, where the datastore is on `/mnt/storage_ssd` and state is on `/var/lib`. It is **already fixed**: `engine.py:287-294` falls back to copy+remove on EXDEV, it has a test (`tests/test_security.py:532`), and the fix landed 24 seconds after that job. M1-hub01 is clean now (only `CoreProtect-CE-23.1.jar`).

### Benchmark tasks (clicks)
| Job | r1 | r2 | r3 | r4 |
|---|---|---|---|---|
| Update everything | 3 + wait | 2 | 2 | **2** |
| Push `Essentials/config.yml` to M1–M8 | 6 | 5 | 5 | **5** (4 via Ctrl+K) |
| Replace CoreProtect everywhere | 6 | 3 | 3 | **3** |
| See what's outdated on M4 | 2 | 0–2 | 0–2 | **0–2** |

---

## Round-3 items: status

| # | Item | Status | Evidence |
|---|---|---|---|
| P1-1 | Partial deploys executable without warning | **Fixed** | Warn-style "Execute 1 change · skip 1 that would fail", a confirm that lists the refused items, "Remove failing items", and backend 409 `has_failing` (per the lead; my Execute was intercepted) |
| P1-2 | Matrix doesn't fit 10 servers at 1440 | **Fixed** | Measured scrollWidth 1166 = clientWidth 1166 (`rc/matrix-1440-light.png`) |
| P1-3 | Refused merge shows the overwrite diff | **Fixed** | The refused row no longer claims "those lines won't change". The kept file shows a "Merged preview — keeps this server's Server.ServerName" diff |
| P2-1 | Kept values invisible | **Fixed** | "Keeping Server.ServerName (hub); last-created-npc-id (7); npc.0.name (Survival) +6 more keys" |
| P2-2 | Disabled Execute style | **Fixed** | Neutral grey everywhere I saw |
| P2-3 | No-op deploys on the Dashboard | **Fixed** | Not in the feed any more |
| P2-4 | Activity pane starts empty | **Fixed** | Newest job auto-selected at ≥1280 |
| P2-5 | Changeset footer spacing | **Fixed** | Summary on its own line |
| P2-6 | Palette theme entries | **Partial** | See P2-7 below: typing "dark" returns "Go to M5-kitpvp01…" and no theme command |
| P2-7 | `u` with nothing updatable; `?` sheet | **Fixed** | "Review 0 updates" is disabled with a toast (`r4/matrix-u-nothing-1440.png`). `?` opens the sheet |
| P2-8 | UUID cells wrap | **Fixed** | Truncated with a copy button (`r4/deploy-uuid-copy-1440.png`) |
| P2-9 | Data-file awareness | **Fixed** | "data file" chip on `Citizens/saves.yml` in search (`rc/fail-keep.png`) |

---

## P0: none

## P1

**P1-1 · "Max changes per run" promises "No limit", but an empty value can't be saved**
- View: Updates → Auto-update policy, and Settings → Auto-update (`updates.js:156`).
- What's wrong: the input's placeholder is **"No limit"** and it has `min="0"`. Clearing the field (or entering 0) sends `max_changes_per_run: null` (`updates.js:124`: `p.max_changes_per_run || null`). The backend requires a number between 1 and 500 (`settings.py:128-136`). I confirmed the real response: `PUT /settings {"auto_update":{"max_changes_per_run":null}}` → **400 `auto_update.max_changes_per_run must be a number`** (rejected before any write). So a user who takes the placeholder at its word can't save the policy at all, and the error doesn't say how to fix it. Negative numbers (`-5`) are also sent unchecked (`rc/settings-invalid.png`).
- Fix: either support "no limit" in the backend (accept `null`, and have `select_auto_rows` skip the cap), or remove the "No limit" placeholder. Set the input to `min="1" max="500" required`, validate on the client, and show an inline message under the field (`Enter 1–500`). Keep Save disabled while the field is invalid.

**P1-2 · The canary copy claims a health check that doesn't exist**
- View: Auto-update policy → Safety (`rc/settings-invalid.png`, `r4/updates-policy-safety-1440.png`). The copy reads: *"Automatic updates go to elChapo01 first; the rest follow after 24 h **if it stays healthy**."*
- What's wrong: `scheduler.select_auto_rows` (`scheduler.py:91-135`) only checks that the soak time has passed and that the canary has **restarted** since the update. It never reads the server log, plugin enable errors, or a crash state. For an unattended feature that installs jars on production servers, the UI overstates the protection. An operator will trust "healthy" and stop watching elChapo01's console.
- Fix (either):
  - (a) Copy only: *"…the rest follow once elChapo01 has restarted and run the update for 24 h. There is no automatic health check — review elChapo01's console after it restarts."*
  - (b) Real signal: after the canary restarts, scan the newest `logs/latest.log` (or the `.log.gz` created at startup) for `Done (` and for `[ERROR]`/`Could not load 'plugins/<jar>'`/`Error occurred while enabling <Plugin>` that mentions the updated plugin. If any appear, hold the rollout and surface it on the Dashboard as `Canary check failed: CoreProtect 24.1 errored on elChapo01 [View log] [Roll back]`.

  (a) is a five-minute change and is enough to clear this P1. (b) is the bold-idea version.

---

## P2: cosmetic or minor

1. **The audit log floods with "viewed diff" rows.** Every plan recompute (each target click or option toggle) logs a new "viewed diff" and "saw server-specific values" row. My session alone produced about 14 near-identical rows per minute (`rc/audit.png`). Collapse identical (user, action, paths, servers) within 15 minutes into one row with a count (`viewed diff ×6 · 23:24–23:31`).
2. **The Audit tab isn't in the URL.** Clicking Audit keeps `#/activity/<job-id>`, so it can't be linked or bookmarked, and Back leaves the tab. `#/activity?tab=access` works but nothing produces it. `#/activity/access` shows "Couldn't load this · unknown job · GET /api/v2/jobs/access → 404" (`rc/sweep/l1440-audit.png`). Push `?tab=access` on click and alias `/activity/access`.
3. **Apply stays enabled on an out-of-date plan.** In the changeset sheet, "This plan is out of date · Re-plan" sits next to an enabled orange "Apply 5 changes" (`rc/apply-409.png`). Disable Apply until the re-plan finishes, or make Re-plan the primary button.
4. **Kept values use the warning style.** "⚠ Keeps Server.ServerName: hub" is amber with a warning icon, but keeping the value is the safe outcome. Use a neutral or ok shield icon (as the summary banner already does).
5. **A redaction label parses a comment as a key.** "5 secret values hidden (Key_pass, Password, Secret, Store_pass, **This can be changed to your own MaxMind URL if you have license https**)" comes from the Plan config comment line `# This can be changed … license https://…`. The redactor should skip `#` comment lines when collecting key names. The actual redaction is only over-cautious, so nothing leaks.
6. **Deploy to the Fabric server.** The M9-homestead01 page shows "Deploy to M9-homestead01", and `#/deploy?targets=M9-homestead01` pre-checks the ineligible chip, so "Where ✓ 1 server" is shown although the plan will always be empty (`rc/deploy-to-fabric.png`). Hide the button for fabric and velocity-without-source, and never pre-check ineligible targets.
7. **Palette "dark"** returns "Go to M5-kitpvp01 / Review 5 updates on M5-kitpvp01 / Deploy to M5-kitpvp01" and no theme entry. Add "Theme: dark / light / system" commands, and stop the fuzzy matcher accepting sparse subsequences across unrelated words.
8. **Matrix middle-ellipsis on short strings**: `5.8 b… 3638`, `11.6.….1327`, `2.2.5…ae91)`. For strings of 14 characters or fewer, drop words instead (`5.8·3638`, `11.6.1.0·1327`). Keep the ellipsis only for genuinely long snapshot ids.
9. **A failed undo leaves no mark on the original job.** `20260926-062414-0e1f2e` (deploy) still shows "done" with no hint that its undo `…062418` failed. Add `Undo failed — retry` on the original job and a Retry-undo button.

---

## To reach NITPICK-ONLY: yes
Fix P1-1 (validation/"No limit") and P1-2 (canary copy), and optionally P2-1 through P2-3. P2-1 through P2-3 are the only P2s a daily user would notice. Everything else is cosmetic.
