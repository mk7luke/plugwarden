# LGT AMP Sync 2.0: Critique, round 3

Evidence paths:
- `r3/` = `<scratchpad>/shots/r3/` (designer)
- `rc/` = `<scratchpad>/shots/r3-critic/` (mine, captured live on :8095 and :8096)

Everything I did was read-only: plans, sheets, one dry-run `POST /deploy` (rejected with 409 as designed), and API probes. I executed nothing.

---

## Score: 8.5 / 10 (r1 6.5 → r2 7.5 → r3 8.5)

Both round-2 P0s are fixed, and the backend enforces the fixes, not just the UI.

**Server-specific values.** I probed this directly:
- A plan for `LuckPerms/config.yml` to M1–M8 returns `needs_decision: true`, with a per-server `server:` table (hub, hungergames, skyblock, kitpvp, creativeplots, bending, lifesteal).
- A `POST /deploy` against that plan without a choice returns **409 `needs_decision`**.
- Re-planning with `preserve_keys:"server_specific"` gives all 7 servers `merge · unchanged · kept this server's server`.

I ran detection across 80 config files on 7 servers. It correctly caught `bStats serverUuid`, `DiscordSRV Channels/ConsoleChannelId`, `CoreProtect table-prefix`, and `Plan Server.ID/UUID/ServerName`, which is better than I asked for. The merge substitutes values line by line, so comments survive. It **refuses** rather than guesses when the structures differ (`Citizens/saves.yml`: "npc.4.name exists on the target but not in the source").

**Replace jar and folder mirrors.**
- Replace jar is jar-only in the UI (non-jar results disabled) and in the API: `folders:["CoreProtect"]` → 422, `paths:["CoreProtect/config.yml"]` → 422.
- Folder mirrors protect data by default ("Player data, logs and databases are protected"), and when the toggle is on they list deletions in red (`− database.db`).

Everything else from round 2 is fixed or close. What remains is one real trust gap (a plan with failing items can still be executed as a partial deploy), one layout regression (the matrix no longer fits 1440), and polish.

### Ship-ready?

**Yes, for a small admin team, once P1-1 is addressed.** P1-1 is a small change: gate Execute when the plan has refused or failed items. Without it, the sandbox already contains a real partially-applied deploy (`20260926-053504-04f1e8`: 21 changed, 7 error, status Failed). Undo exists and works as a safety net, so this does not block a careful team, but it should land before anyone without context uses the tool. Nothing else blocks shipping. The other P1s are quality issues, not safety issues.

### Benchmark tasks (clicks, measured live)
| Job | r1 | r2 | r3 |
|---|---|---|---|
| Update everything | 3 + wait | 2 | **2** (sheet now opens By plugin, 24 rows, footer "All compatible · all hashes verified") |
| Push `Essentials/config.yml` to M1–M8 | 6 | 5 | **5** (4 via Ctrl+K "essentials config" → "Push Essentials/config.yml…"). For LuckPerms, +1 for the Keep/Overwrite choice, which is correct |
| Replace CoreProtect everywhere | 6 | 3 | **3** (Ctrl+K "update core" → "Update CoreProtect on 6 servers…" → Apply) |
| See what's outdated on M4 | 2 | 0–2 | **0–2** (tile above the fold; Ctrl+K "m4" → "Review 8 updates"; `j`/`k` moves between tiles) |

---

## Round-2 items: status

| # | Item | Status | Evidence |
|---|---|---|---|
| P0-A | Config push overwrites per-server identity | **Fixed** | `rc/lp-decision.png` shows Execute disabled until a choice is made. `rc/lp-keep.png` shows 7 × no change after Keep. `rc/lp-overwrite.png` shows "Execute 7 changes" enabled only after Overwrite. The API returns 409 without a choice |
| P0-B | Replace accepts folders / mirror deletes data | **Fixed** | `rc/replace-search.png`: the folder and files are disabled under Replace. The API returns 422. `rc/plan-folder.png` shows protected-data banner ON by default, and `r3/deploy-mirror-deletes-1440.png` shows red deletions |
| P1-A | Tile badges wrap / uneven heights | **Fixed** | All 11 tiles measure 154px and the flags sit 11px from the top. `source` moved to the footer (`rc/dash.png`) |
| P1-B | Pin count wrong | **Fixed** | "Pin at 23.1 on all 7 servers with CoreProtect", plus the note "elChapo01 is on 23.4 — pinning blocks their updates but doesn't downgrade them" |
| P1-C | Update-all sheet: 63 rows, repeated chips | **Fixed** | By plugin is the default, multi-server rows collapse (CoreProtect "6 of 6 servers"), and chips are summarized in the footer (`rc/cs-all.png`) |
| P1-D | Palette ranking / multi-word | **Fixed** | "core" → Open first and no Remove verbs. "update core" → exactly one verb. "remove core" → Remove only when asked. "essentials config" → the file |
| P1-E | Truncated versions | **Partial** | Tiles use compactVer (`6.0.1`, `2.11.3·1247`). The matrix still truncates (`2.0.40-S·39…`, `11.6.1.0.13…`), and the cells got wider, which caused P1-2 |
| P1-F | Mobile plan bar | **Fixed** | Two-line bar, "7 changes", full-width button (`rc/m-planbar.png`) |
| r2 P2s | Feed row names, reverted copy, restart+platform, list alignment, search tokens | **Fixed** (mostly) | "Apply updates · M5-kitpvp01". Tokenized search works. The Activity pane still starts empty (P2-4) |

---

## P0: none

---

## P1

**P1-1 · A plan with refused or failed items can still be executed, which creates partial deploys**
- Evidence: Activity job `20260926-053504-04f1e8` (`rc/activity-error-job.png`) is a real deploy with status **Failed**: "21 changed, 7 error". On M1, `DiscordSRV/` changed and `Plan/config.yml` changed, but `DiscordSRV/config.yml` was *refused* ("'Channels' does not map to one scalar"). Servers are now half-migrated across several plugins. `rc/mixed-keep.png` shows the plan banner "1 item would fail — see details below".
- What's wrong: the plan knew about the failures in advance, but the Execute label only counts the changes. The operator can't tell they're about to commit a partial rollout.
- Fix: when `summary.error > 0`, relabel the button `Execute 21 changes · skip 7 that would fail` in warn style and require a confirm that lists the refused items. Offer a one-click `Remove failing items` that re-plans without them. For `complex` keys like DiscordSRV `Channels`, add a third decision next to Keep/Overwrite: `Skip this file on servers where it can't be merged`. Record the skipped items as `skipped (by choice)` rather than `error`, so the job ends `Done` instead of `Failed`.

**P1-2 · Regression: the matrix no longer fits 10 servers at 1440**
- Evidence: `rc/matrix-kbd.png`. The M9-aerons-server01 column is cut off at the right edge and needs a horizontal scroll. In r2 all 10 columns fit.
- Fix: return server columns to 88–92px and take the space from the plugin-name column (240 → 200px; the longest name, FastAsyncWorldEdit, is ~150px). Keep compactVer, but cap it at 9 characters with a middle ellipsis: `2.0.40·3990` → `2.0.40…90`, or show the build only (`b3990`) when the base version is shared across the row. Target: 10 servers plus the name column fit in 1168px with no horizontal scroll.

**P1-3 · A refused merge still shows the overwrite diff, under copy that says those lines won't change**
- Evidence: `rc/mixed-keep.png`. For `Citizens/saves.yml` on M1, the row says "refused: cannot keep this server's values safely", then "This server keeps its own … npc.0.name … — those lines below won't change". Below that is a full diff where `name: Survival → name: Gonzron23` *does* change. Meanwhile the footer says "Targets already match. Nothing to do.", which contradicts "1 item would fail".
- Fix: for `reason_code: merge_unsafe`, hide the diff behind `Show what Overwrite would do` and replace the "keeps its own" line with `Nothing will be written to this file on M1-hub01`. When there are errors and no changes, the footer should say `Nothing can be pushed with "Keep" — 1 file can't be merged safely. Overwrite or remove it.`

---

## P2: polish

1. **Kept values are invisible after choosing Keep** (`rc/lp-keep.png`). The summary says "Keeping each server's own value for 1 server-specific key", but the collapsed rows show only "no change". Name the key and values: `Keeping server (hub, hungergames, skyblock, +4)`.
2. **Disabled Execute in the Keep/nothing-to-do state** is sometimes a faded orange (`r3/deploy-identity-keep-1440.png`) and sometimes grey (`rc/lp-keep.png`). Always use the neutral disabled style.
3. **No-op deploys in Recent changes**: "Deploy · 7 unchanged" and "Deploy · 1 skipped, 61 unchanged" appear on the Dashboard. Hide jobs with `changed == 0` (they stay in Activity), or render them muted as `No-op deploy`.
4. **The Activity right pane starts empty** ("Select a job"). Auto-select the newest job at ≥1280px.
5. **Changeset footer spacing**: "230.0 MB download✓ All compatible" runs together (`rc/cs-all.png`). Add a `·` separator and a 12px gap.
6. **Palette "dark" → "Switch to light theme"** reads backwards. Add both `Theme: dark` and `Theme: light` entries and mark the current one.
7. **Matrix `u` with no updatable cells selected** does nothing silently (`rc/matrix-kbd.png`, Citizens). Toast `None of the 2 selected cells has an update`. The `?` shortcut sheet also didn't open for me on the Dashboard; check it's bound globally.
8. **Plan UUID cells** wrap to 5 lines (`rc/plan-folder.png`). Truncate to `5b51d20b…3340` with copy-on-click.
9. **Data-file awareness**: `Essentials/spawn.yml`, `Jarvis/*-data.yml` and `Citizens/saves.yml` are data, not config. Detection handles them safely (refuse, or keep coordinates), but a `data file` chip in search results and the plan would stop people from pushing them in the first place.

---

## Bold ideas (still open)

1. **Canary rollout**: apply to elChapo01, watch `logs/latest.log` for `Done (` and plugin errors, then continue or auto-undo. With plan_id apply, restart tracking and undo in place, this is now a small step, and it's what makes "Check & apply" auto-update trustworthy.
2. **Per-server overrides as first-class settings**: persist the detected server-specific keys (`LuckPerms server`, `CoreProtect table-prefix`, `Plan ServerName`) in Settings. Pushes then become "source + overrides" by construction, and the Keep/Overwrite prompt only appears for *new* differences.
3. **Changelog excerpts** in the sheet, with amber flags on "breaking", "migration" and "Java 21".
