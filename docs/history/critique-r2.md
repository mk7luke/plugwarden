# LGT AMP Sync 2.0: Critique, round 2

Evidence paths:
- `r2/` = `<scratchpad>/shots/r2/` (designer)
- `rc/` = `<scratchpad>/shots/r2-critic/` (mine, captured live on :8095)

Everything I did was read-only. I opened the sheets and plans, ran `POST /deploy/plan` (a dry run), and applied nothing.

---

## Score: 7.5 / 10 (up from 6.5)

Round 2 fixes almost every round-1 finding.
- **Updates:** every "Update" now goes through one Review-changeset sheet. The sheet shows exact rows, compat chips, hash-verified marks and per-row exclusion, applies by `plan_id`, and ends with a restart checklist. This is the best screen in the app.
- **Dashboard:** it has one clear headline, and all 11 servers fit above the fold at 1440.
- **Matrix:** the headers are angled and cells can be selected.
- **Deploy:** search is recursive and the plan shows real diffs.
- **Contrast:** it now passes AA in both themes (measured: dark `--fg-3` 5.3–6.0:1, light accent 5.8:1).

The two new P0s are safety problems in the domain, not visual ones, and the new features exposed them. The config diff shows that pushing `LuckPerms/config.yml` rewrites every server's `server:` identity to `survival`, and nothing warns about it. Separately, the Replace-jar action accepts a folder, and a folder is deployed as an `rsync --delete` mirror. Fix those two and the small P1s, and this is a 9.

### Benchmark tasks (clicks, measured live)
| Job | r1 | r2 path | r2 clicks |
|---|---|---|---|
| Update everything | 3 + wait (preview was a log) | Dashboard "Review & update all" → sheet auto-plans → "Apply 64 changes" | **2** ✓ |
| Push `Essentials/config.yml` to M1–M8 | 6 | Deploy → type `Essentials/config.yml`, tick the single result → M1–M8 → Execute → confirm | **5** ✓ (4 via Ctrl+K "Push Essentials/config.yml…") |
| Replace CoreProtect everywhere | 6 | Ctrl+K "core" → "Update CoreProtect on 6 servers…" → Apply | **3** ✓ (the Deploy path is 6 and has a trap: see P0-B) |
| See what's outdated on M4 | 2 | M4 tile is above the fold at 1440 (3 rows + "+5 more"), or Ctrl+K "m4" → "Review 8 updates" | **0–2** ✓ |

---

## Round-1 items: status

| # | Item | Status | Note / evidence |
|---|---|---|---|
| P0-1 | One-click real updates from 4 entry points | **Fixed** | Tile, row, matrix and Updates-row buttons all read "Review" and open the sheet (`rc/sheet-elchapo.png`, `rc/matrix-sel-review.png`) |
| P0-2 | Preview wasn't a preview; apply-all could drift from what was previewed | **Fixed** | Plan rows are shown in the sheet, and apply is by `plan_id` (`rc/sheet-all-excluded.png`, `r2/changeset-result-1440.png`) |
| P0-3 | Unlabeled or unhoverable badges; wrong drift semantics | **Fixed** (new layout issue: P1-A) | "14 updates / 2 drift" are labeled. Hit-testing shows the badge is on top of the overlay. M1 no longer shows drift |
| P0-4 | Pin/Ignore scope | **Partial** | The menu now separates "on M1-hub01" from network-wide, but it says "all 10 servers" for a plugin that is on 7 (P1-B) |
| P1-1 | Dashboard hierarchy | **Fixed** | Single headline, all 11 tiles above the fold, and 1024 no longer has an orphan grid cell (`r2/dashboard-1024.png`) |
| P1-2 | Feed noise | **Partial** | Dry runs and checks are hidden. Three identical "Apply updates · 5 changed" rows aren't collapsed, and reverted rows show a raw job id (P2-3) |
| P1-3 | Matrix defects | **Mostly fixed** | Angled headers aren't clipped, the hover overlap is gone, and the legend has N/A. Versions are still truncated (P1-E) |
| P1-4 | Non-recursive deploy search | **Fixed** | "config.yml" → 12 results as paths (`rc/deploy-search-config.png`) |
| P1-5 | Replace rows and skip reasons | **Fixed** | Strikethrough old → new, and "Skipped: not installed (install is off) · Also install where missing" (`r2/deploy-replace-1440.png`) |
| P1-6 | AxiomPaper compat trust | **Fixed** | "supports MC 1.21.6" / "works on MC 1.21.6" chips, with the declared range in the tooltip |
| P1-7 | Contrast | **Fixed** | All text tokens are ≥4.8:1. `--fg-4` is left only on icons, separators and the chevron |
| P1-8 | Accent overuse | **Fixed** | Changelog links are neutral and underlined. "No source · Map…" is a muted chip |
| P1-9 | Palette | **Partial** | Verbs, files and grouping are in, and the input no longer jumps. Multi-word queries return nothing, and destructive verbs rank above "Open" (P1-D) |
| P1-10 | Mobile | **Mostly fixed** | Targets are 44px high and nothing overflows horizontally. The plan bar text collides with its button (P1-F) |
| P1-11 | Numbers disagree | **Fixed** | Headline, Updates and Activity all say "24 plugins · 64 installs · 46 of 78 jars identified" |
| P1-12 | Undone jobs read as done | **Fixed** | "Reverted" tag and strikethrough title (`rc/activity.png`) |
| P1-13 | Stale error / layout shift | **Fixed** (error) | After a 500 then navigating away and back, the page revalidates. I didn't re-measure the skeleton shift |
| P1-14 | Restart marker | **Fixed** | Headline shows "2 servers need a restart", and the tile and list show a Restart chip (`rc/dash-list.png`) |

---

## P0: broken, misleading or unsafe

**P0-A · Config push silently overwrites per-server identity (LuckPerms `server:`)**
- View: Deploy → `LuckPerms/config.yml` → M1–M8 → Show diff. Evidence: `rc/deploy-lp-diff.png`, `r2/deploy-diff-1440.png`.
- What's wrong: the diff shows `-server: hub / +server: survival` for M1. The sandbox files confirm every server has its own value (`hub`, `hungergames`, `skyblock`, `survival`). Executing "7 changes" renames every server's LuckPerms context to `survival`, which breaks per-server permissions network-wide. Nothing is highlighted except a normal red/green line inside a collapsed diff, and only M1's diff is open by default. The same trap exists for DB names, ports, `server-name`, and DiscordSRV channel ids.
- Fix:
  - **Backend:** add a small `SERVER_SPECIFIC_KEYS` registry, per file glob, to the plan: `LuckPerms/config.yml: [server, storage-method, data.database]`, `*/config.yml: [server-name, port, database, table-prefix]`, `DiscordSRV/config.yml: [Channels, DiscordConsoleChannelId]`. Emit `warnings:[{server, file, key, from, to}]`.
  - **UI:** show a warn block at the top of the plan: `⚠ 7 servers: this changes server-specific keys — server: hub → survival (M1) …`. Offer two buttons: `Keep each server's value` (merge: push everything except those keys, a YAML-aware 3-way merge) and `Overwrite anyway`. Make Execute require that choice.
  - **Minimum for next round:** auto-expand diffs that touch a flagged key, and put a warn chip on the server row.

**P0-B · Replace jar accepts a folder, and a folder deploys as an `rsync --delete` mirror**
- View: Deploy → search "CoreProtect" → tick the first result → Replace jar → All eligible. Evidence: `rc/deploy-replace.png`. API dry run: `POST /deploy/plan {action:"replace", folders:["CoreProtect"]}` returns `action:"mirror"`. `engine.py:280` shows `delete=True`.
- What's wrong: search results sort the **folder** `CoreProtect/` above `CoreProtect-23.4b.jar`, so the natural first tick picks the folder. With "Replace jar" selected, the plan says "CoreProtect/ · 1 change(s)" on 6 servers and Execute is enabled. In production that folder holds `database.db`, the block-logging DB. A mirror deletes or overwrites target files that differ from elChapo's. The plan row shows only a count, with no deletion list and no size.
- Fix:
  - With Replace jar selected, disable non-jar items. The copy should read `Replace jar only works on .jar files — pick CoreProtect-23.4b.jar`, and the backend should reject it with 422.
  - For any folder mirror (Sync/Install), list deletions in red in the plan (`− database.db (1.4 GB)`) and add a checkbox to exclude them.
  - Default-exclude `*.db`, `*.sqlite`, `*.mv.db`, `data/`, `userdata/` and `logs/` from folder mirrors, with an explicit opt-in.
  - In search results, rank jars above folders whenever the Replace action is selected.

---

## P1: significant problems

**P1-A · Tile badges wrap to a second line, so tile heights are inconsistent** (lead-noted, confirmed)
- Evidence: `rc/dash.png`, `r2/dashboard-light-1440.png`. Measured: the elChapo01 and M9-aerons flags sit 37px from the tile top versus 11px elsewhere. Row 1 tiles are 180px, row 2 are 154px, and row 3 are 180px.
- Fix: move `source` out of the flag cluster into the footer platform line (`Pu Purpur 1.21.6 · source`). Shorten drift to an icon plus number (`⇄ 2`, with a tooltip that says "2 plugins differ from the network majority"), and allow one line only (`flex-wrap:nowrap`). Put the badges on the name row, and truncate the name with an ellipsis before wrapping. Pin all tiles to a fixed height (`grid-auto-rows: 172px`) and always render 3 watch rows or placeholders, so M0 and M9-homestead line up.

**P1-B · The "Pin … on all 10 servers" count is wrong**
- Evidence: `rc/server-rowmenu.png`. The CoreProtect menu offers "Pin at 23.1 on all 10 servers", but CoreProtect is installed on 7. `servers.js:51` computes `total` as every server with plugins, including the Velocity proxy.
- Fix: use the number of servers that have this key (`matrix.plugins[key].cells`), e.g. `Pin at 23.1 on all 7 servers with CoreProtect`. Also warn when those servers aren't on the same version: `elChapo01 is on 23.4 — pinning at 23.1 blocks its updates but doesn't downgrade it`.

**P1-C · The changeset sheet for "update everything" is 63 rows grouped by server, with the same chip repeated 63 times**
- Evidence: `rc/sheet-all-excluded.png`, `rc/sheet-all-full.png`.
- The "works on MC 1.21.6" and "hash verified" chips repeat on every row, which is noise that hides the one row that needs attention. The strikethrough old jar in 11px mono at `--fg-3` is hard to read. You can't collapse a server group, and you can't view by plugin (24 rows).
- Fix:
  - Add a `By plugin | By server` toggle, defaulting to By plugin when the scope is "all".
  - Show compat and hash chips only on exceptions, and summarize them in the footer: `All 63 compatible · all hashes verified` (green) or `2 not listed for their MC version` (warn, clickable to filter).
  - Make server groups collapsible.
  - Show the old jar as `from CoreProtect-CE-23.1.jar` in plain `--fg-3`, without strikethrough.
  - Size the sheet to its content when there are ≤5 rows (the 2-cell review in `rc/matrix-sel-review.png` leaves 600px empty).

**P1-D · Palette ranking and matching**
- Evidence: `rc/pal-core.png`. For "core" the list is: Update CoreProtect, Replace CoreProtect, **Remove CoreProtect**, Open CoreProtect, Replace TheCore, **Remove TheCore**… Destructive verbs appear for fuzzy matches and rank above navigation. "update core" and "dark" return nothing.
- Fix:
  - Order results as Open (entity) → safe verbs → destructive verbs.
  - Show "Remove X…" only when the query matches the plugin name exactly or at a word prefix and contains "rem" or "del".
  - Parse `<verb> <entity>` queries: "update core" should find "Update CoreProtect on 6 servers".
  - Keep the theme toggle searchable as "theme", "dark" or "light".

**P1-E · Versions are truncated exactly where comparison matters**
- Evidence: `rc/matrix-sel.png` shows `2.0.40-S …` (with a stray space before the ellipsis), `11.6.1.0.…` and `2.14.3-S-…`. Dashboard tiles show `5.0.1+1.2… → 6.0.1+1.2…` for AxiomPaper, with both sides truncated.
- Fix:
  - Matrix: widen cells to 104px and compact the version, e.g. `2.14.3-SNAPSHOT-1231+8090431` → `2.14.3·1231`. The full string is already in the tooltip.
  - Tiles: truncate the plugin *name* first, never the version. Give the version column `flex:none` and trim `+mc` build suffixes (`6.0.1`, with the MC range in the tooltip).

**P1-F · The mobile deploy plan bar collides with its button, and its label is wrong when nothing changes**
- Evidence: `rc/m-deploy-planbar.png`. "1 item · 7 servers · 0 changes" runs into the "Review plan" button, and the bar also covers the last row of target chips.
- Fix: stack the bar as two lines (summary 12px, then button), or shorten it to `1 · 7 srv · 0 Δ`. When there are 0 changes, render `Nothing to change` as a disabled ghost button. Add `padding-bottom: 88px` to the composer so the bar never covers content.

---

## P2: polish

1. **Selection bar label truncated**: "Remove from 2 servers …" (`rc/matrix-sel.png`). Drop the ellipsis char, or write `Remove…` with the count in the summary.
2. **Mobile matrix after tapping a cell**: the elChapo01 angled label is clipped to a sliver under the sticky first column (`rc/m-matrix-sel.png`). Add `padding-left` on the header row equal to the label's diagonal overhang (~40px).
3. **Reverted feed row** shows `Undone by 20260926-044341-01a951 · was: 1 changed`. Use `Reverted 25m ago · was 1 change` and put the id only in the detail view.
4. **Feed collapse**: three consecutive "Apply updates · 5 changed · 1 server" rows. Show the server name in the row (`Apply updates · M5-kitpvp01 · 5 changed`) so they're distinguishable, or collapse them.
5. **Activity empty right pane** says "Select a job". Auto-select the newest job at ≥1280px.
6. **"No source · Map…" chip** reads as truncated. Use `No source · Map` without the ellipsis, or keep "Map source…" as a ghost button in the actions column.
7. **Tile restart chip replaces the platform line** in the tile footer (M3 loses "Purpur 1.21.6"). Show both: `Pu 1.21.6 · ⟲ Restart`.
8. **List view Status column**: the badges are right-aligned under a left-aligned header (`rc/dash-list.png`). Left-align them.
9. **Replace with an older build than the latest**: Deploy lets you replace CE-23.1 with a custom 23.4b while 24.1 is available and compatible. Add a hint on the plan row: `24.1 is available via Updates`.
10. **Deploy search** needs contiguous text ("essentials config" → nothing). Tokenize on spaces and `/`.

---

## Bold ideas (updated)

1. **Server-aware config templating.** Once P0-A exists, go further: store per-server overrides (`server: hub`) in Settings and render pushes as *source + overrides*. Pushing Essentials/LuckPerms configs then becomes safe by construction, and the diff shows only the intended changes.
2. **Canary rollout.** This is still the biggest win for "update all". Apply to elChapo01 first, tail `logs/latest.log` for `Done (` or plugin `ERROR`, then continue to M1–M8 or halt and auto-undo. The restart checklist already provides half the UI.
3. **Changelog excerpts in the sheet.** Inline the first 5 lines of the Modrinth changelog under each plugin (collapsed), and flag "breaking", "config migration" and "requires Java 21" keywords in amber.
4. **Drift "why"**: the plugin drawer should show *when* each server diverged (from Activity), plus one-click "Align to majority" and "Align to source" with the same changeset sheet.
5. **Keyboard grid.** In the matrix, arrow keys should move between cells, Space selects, and `u` reviews. My arrow-key test was inconclusive (the row I tested had only one cell), so re-check this.
