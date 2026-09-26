# LGT AMP Sync 2.0: Critique, round 1

Evidence paths are abbreviated as follows:
- `r1/` = `/tmp/claude-1000/-home-luke-lgt-amp-sync/0cf2f751-d945-45e5-89cc-a0b56688015d/scratchpad/shots/r1/` (designer)
- `rc/` = `/tmp/claude-1000/-home-luke-lgt-amp-sync/0cf2f751-d945-45e5-89cc-a0b56688015d/scratchpad/shots/r1-critic/` (mine, captured live on :8095)

I ran one real dry run ("Update all → Preview changes"). I executed nothing else.

---

## Score: 6.5 / 10

Nothing from the old UI survives: no emoji cards, no wizard, no Tailwind, no blobs. The shell (sidebar, top bar, Ctrl+K, g-shortcuts), the token system, and the skeleton and error states are all at a professional level. The Deploy composer is the best screen, taking 6 clicks to push `Essentials/config.yml` to M1–M8 with a live plan. The weak point is **trust**, which matters most in an ops tool. The dry-run-first promise is broken in four places where one click applies real updates. The "preview" is a streaming log and a toast, not a reviewable plan. Two numbers on every server tile are unlabeled, and one of them (drift) means something different from what an operator would assume. Pin and Ignore look per-server but act network-wide. The Dashboard also repeats "24 updates" four times while only 4 of 11 servers are visible at 1440×900. Fix the P0s and the P1 hierarchy items and this is an 8.5.

### Task efficiency (measured live)
| Job | Path | Clicks | Verdict |
|---|---|---|---|
| Update everything | Dashboard → Update all → Preview changes → wait → toast "Apply for real" | 3 + wait | OK count, but the preview is a raw log (see P0-2) |
| Push `Essentials/config.yml` to M1–M8 | Deploy → open Essentials → tick config.yml → M1–M8 → Execute → confirm | 6 | Good. Filtering for `config.yml` from the root finds nothing (P1-4) |
| Replace CoreProtect everywhere | Deploy → filter+tick jar → Replace jar → All eligible → Execute → confirm | 6 | Works. The plan row that matters is truncated (P1-5) |
| See what's outdated on M4 | Ctrl+K "m4" ↵, or Servers → M4 | 2 | OK. On the Dashboard the M4 tile is below the fold and shows only 4 of 8 |

---

## P0: broken, misleading or unsafe

**P0-1 · Updates apply for real on one click from 4 entry points, with no preview and no confirm**
- Views: Dashboard tile "Update 14" (`dashboard.js:101`), matrix row ↑ icon "Update everywhere" (`matrix.js:52`), server header "Update all 5" and row "Update" (`servers.js:61`), and Updates row "Update" (`updates.js`). Evidence: `r1/dashboard-1440.png`, `r1/server-1440.png`, `r1/updates-1440.png`.
- What's wrong: the banner right above the tiles says *"Preview as a dry run, then apply."* The "Update all" dialog defaults dry-run ON, while the Updates bulk bar defaults it OFF (`rc/updates-selected.png`). The tile's "Update 14" button sits inside a fully clickable card (`.tile-link` overlay), so a missed click on the elChapo card starts a real 14-plugin swap on the source server. The model is inconsistent, and the dangerous default is the one closest to the pointer.
- Fix: route every update entry point through one **Review sheet** (the same component as Update all), prefilled with the scope (server, plugin or selection). The sheet auto-runs the dry run on open and shows the per-server table (P0-2). Its primary button reads `Apply 14 updates on elChapo01`. Keep the per-row "Update" on the server page as the only instant path, and add a 5-second undo toast (`Updated CoreProtect on M1-hub01 · Undo`). Remove the Dry-run switch from the Updates bulk bar, since the sheet makes it redundant.

**P0-2 · "Preview changes" doesn't show a preview, and "Apply for real" may apply something other than what was previewed**
- View: Update all dialog. Evidence: `rc/updateall-dry-running.png`, `rc/updateall-dry-done.png`.
- What's wrong: clicking Preview closes the dialog. A job dock then streams rsync-style log lines over the tiles, and a toast says *"64 changed"* (it should say *would change*). The toast stacks on top of the dock, with both in the bottom-right, covering content. "Apply for real" is styled as a secondary button, identical to "View details". It re-posts `items:"all"` (`updateall.js:33`), so if a check lands between preview and apply, the applied set differs from what was reviewed.
- Fix: keep the dialog open and turn it into a 2-step sheet. Step 1 is the list (as now). Step 2 is "Plan": a table grouped by server with `plugin · old jar → new jar · size · sha verified ✓`, a totals line `58 jar swaps on 10 servers · 0 errors · 142 MB download`, and a primary button `Apply these 58 changes`. That button posts the exact `items` list from the dry-run job (or `from_job: <id>`). In dry-run results, render "changed" as "would change" everywhere (toast, dock, feed).

**P0-3 · Server-tile badges are unlabeled, unhoverable, and "drift" is semantically wrong**
- Views: Dashboard tiles and Servers list. Evidence: `r1/dashboard-1440.png` (the "14" and "⇄5"), `r1/servers-1440.png` (every game server shows "5 drift").
- What's wrong: (a) the blue "14" has no visible label and no `title`, only an sr-only span. (b) The drift badge has a `title`, but the `.tile-link` overlay intercepts the pointer, so the tooltip never shows. Playwright confirmed this: *"a.tile-link intercepts pointer events"*. (c) `drift` per server = number of plugins on this server whose version differs *anywhere* on the network (`main.py:110,124`). M1-hub01 shows "5 drift" although M1 is on the majority version of all 5. The odd one out is elChapo01 (CoreProtect 23.4 vs 23.1 everywhere else). An operator reads "M1 has 5 problems".
- Fix: label inline, as `14 updates` / `2 drifted`, matching the Servers list. Put the flags above the overlay with `position:relative; z-index:2`. Redefine server drift as "plugins where this server's version ≠ the network majority (or ≠ source)", so M1 shows 0 and elChapo shows its real count. Backend: add `drift_self` to `Server`.

**P0-4 · Pin / Ignore look per-server but apply network-wide**
- View: server detail row icons. Evidence: `r1/server-1440.png`.
- What's wrong: the icons live on M1-hub01's row with the tooltip "Pin at this version". The call is `POST /plugins/{key}/pin`, and settings store `pins:{key:version}`, which is global. Pinning CoreProtect at 23.1 on M1 silently pins it on 7 servers, including elChapo01, which is on 23.4. The tooltip also gives no text label, and the unlabeled 16px pin/eye-off glyphs repeat on every row.
- Fix: either make it per-server (backend `pins:{key:{server:version}}`) or say what it does. The tooltip and toast should read `Pin CoreProtect at 23.1 on all 7 servers`, and a pinned row should show `Pinned network-wide · 23.1`. Move the two actions into a `⋯` row menu with text labels.

---

## P1: significant UX or visual problems

**P1-1 · Dashboard hierarchy: "24 updates" appears 4 times, and servers start at y=380**
- Evidence: `r1/dashboard-1440.png`, `rc/t-dashboard.png` (1024: the 5 KPIs wrap 3+2 with an orphan cell and a broken border).
- The KPI "Updates 24", the banner "24 updates ready", the sidebar badge 24, and the tile badges all say the same thing. The "Servers 11" and "Plugins 68" KPIs are static trivia. At 1440×900 only 4 of 11 tiles are visible, and each tile is 236px tall.
- Fix: drop the KPI strip and fold its useful bits into the banner: `24 updates · 64 installs on 10 servers · 1 drifted plugin · checked 4m ago [Check] [Auto-update: Off] [Review & update all]`. Make tiles 3-up at ≥1280 with 3 watch rows (not 4) and a clickable `+N more`. That gets all 11 servers above the fold at 1440×900. At 1024, use a 2-col grid for tiles and never a 3+2 KPI wrap.

**P1-2 · Recent activity is dominated by noise**
- Evidence: `r1/dashboard-1440.png` shows 4 of the first 7 items as dry runs or checks. `/api/v2/jobs` returns 23 jobs: 8 dry runs, 5 checks, 20 of them by "local".
- Fix: the Dashboard feed shows only real mutations (`!dry_run && kind!=="update-check"`), and collapses consecutive identical kinds (`3 update checks · last 4m ago`). On the Activity page, add a `Hide dry runs` switch (default ON) and counts on the tabs (`Updates 5 · Deploys 3 …`). Style undone jobs with a strikethrough title and a `Reverted` tag, not just the grey "undone" tag.

**P1-3 · Plugins matrix layout defects**
- Evidence: `rc/matrix-header.png`, `r1/matrix-1440.png`, `rc/matrix-rowhover.png`, `rc/m-matrix-tap.png`.
- (a) `M9-aerons-server01` is clipped at the top. The label is 117px, but `thead th` is `height:118px; padding-bottom:8px`, which leaves 110px. Set `height:136px`, or better, use 45° headers (`transform: rotate(-45deg)` on a 28px-high label) so long names never need vertical space.
- (b) The table doesn't fill the panel: a ~48px dead gutter sits after the last column at 1440. Set `width:100%` on the table and let the server columns flex (`min-width:88px`).
- (c) Versions are truncated to `2.0.40-SN…`, `11.6.1.0.…`, `2.14.3-S1…`, the snapshot builds most in need of comparison. Widen cells to 96px, drop `-SNAPSHOT` to `-S` (shortVer already tries this, but only for `-SNAPSHOT-`), and show the full version on hover or focus as a styled tooltip instead of `title`.
- (d) On row hover, the action cluster covers the `7/10` count (it renders as "7/1"). Reserve a 72px actions slot or hide the count on hover.
- (e) On mobile the actions are always visible, which truncates every name ("Armored…", "CorePr…") and puts a trash can on all 68 rows. Put the actions in a row tap sheet instead.
- (f) The M0-proxy "different ecosystem" cells are solid grey, but the legend has no swatch for them. Add `N/A · other platform`.

**P1-4 · Deploy source filter only searches the current folder**
- Evidence: `rc/deploy-filter-configyml.png` shows *Nothing matches "config.yml"* at the plugins root, although 40+ exist.
- Fix: search recursively (server-side `tree?q=`, depth ≤4) and render results as paths: `Essentials/config.yml`, `LuckPerms/config.yml`, each with a checkbox. This is the #3 job, and it should be "type, tick, pick M1–M8, execute".

**P1-5 · The Replace plan hides the one line that matters, and skipped rows hide the reason**
- Evidence: `rc/deploy-replace-cp-full.png`.
- The row reads `CoreProtect-23.4b.jar — CoreProtect-CE-23…`, which is truncated, and the em dash is ambiguous about direction. The M7-bending01 and M9-aerons rows say "1 skipped" and stay collapsed with no reason.
- Fix: use two lines per change: `CoreProtect-CE-23.1.jar` (strikethrough, `--fg-3`) `→ CoreProtect-23.4b.jar` (`--update`). Show the reason inline on skipped rows: `not installed · turn on "Also install where missing"`, with that toggle as an inline link. Also, "All eligible" and "Game servers" both render as active at once. Only the preset that exactly matches the selection should be pressed.

**P1-6 · AxiomPaper "6.0.1+1.21.8" on a 1.21.6 server is correct, but it looks wrong**
- Evidence: `r1/updates-1440.png`, `r1/dashboard-1440.png`. Backend check: `updates.py:289` sends `game_versions:["1.21.6"]` and `loaders`. Modrinth version `CelkNHJp` (6.0.1+1.21.8) declares `game_versions: ["1.21.6","1.21.7","1.21.8"]`. **The filter works, and this is not a backend bug.** In fact, the installed `AxiomPaper-5.0.1-for-MC1.21.5` is the jar that's wrong for 1.21.6.
- Fix (trust signal): add `game_versions` to `latest` in the cache and API. In the UI, render a compat chip next to the target version, e.g. `MC 1.21.6–1.21.8 ✓` (`--ok-soft`). When the *installed* jar doesn't list the server's MC version, show a warn chip: `built for 1.21.5`.

**P1-7 · Muted text fails WCAG AA in both themes**
- Measured contrast ratios: dark `--fg-4 #5b5f69` is 3.04:1 on `--bg` and 2.88:1 on `--surface`. Light `--fg-4 #9a9ea6` is 2.48:1 on `--bg` and 2.69:1 on `--surface`. `--fg-4` is used for *text* in nav section labels (10.5px), tree sizes, log timestamps, palette hints and footer, deploy "already up to date" lines (`.op-same`), and the "Not scheduled" sub-label.
- Light accent `#e0551a` is 3.83:1 as text on white ("Map source", "View all", "Changelog"), and white-on-accent is also 3.83:1 on the primary button at 12.5px. Light `--ok #13894a` is 4.46:1 and `--warn #a66b00` is 4.44:1, marginal at 11.5px.
- Fix: keep `--fg-4` for icons and borders only, and use `--fg-3` for all text. Dark `--fg-3` is fine at 4.9:1. Light: `--accent: #c2410c` (5.2:1 on white, and white-on-accent 5.2:1), `--ok: #0f7a41`, `--warn: #8f5c00`. Raise `--fs-2xs` from 10.5 to 11px.

**P1-8 · Accent orange is overused as link colour, so it no longer means "primary action"**
- Evidence: `r1/server-1440.png` has "Map source" ×10 in orange, next to a grey "Unknown source" pill on the same row. `r1/updates-1440.png` has "Changelog ↗" in orange on all 24 rows.
- Fix: links use `--fg-2` with an underline on hover. Keep orange for the primary button, the active nav item, and focus. On the server page, merge Source and Status for unknown rows into one muted chip, `No source · Map…`, that opens an inline popover (Modrinth/Hangar/Spiget/GitHub + id) prefilled with the plugin name, instead of deep-linking to an unfiltered Settings tab.

**P1-9 · Command palette is a navigator, not a command surface**
- Evidence: `rc/palette-core.png`, `rc/palette-esscfg.png`, `rc/palette-empty.png` vs `rc/palette-core.png` (the input baseline moves 10px when typing starts).
- Group headers repeat ("Plugins, Actions, Plugins, Actions") because results are sorted by score across groups. The fuzzy matcher is too loose ("core" matches "Switch to dark theme"). It shows "1 servers". It can't find config files and offers no entity verbs.
- Fix: group first, then score within groups. Use prefix/word-boundary matching and a minimum score. Pluralize. Add verbs per entity: `CoreProtect → Update on 6 servers · Replace jar… · Remove…`, `M4-skyblock01 → Update 8 · Deploy to…`. Add file results (`Essentials/config.yml → Push to…`). Fix the input height so it doesn't jump.

**P1-10 · Mobile (390) usability**
- Evidence: `rc/m-server.png`, `rc/m-updates.png`, `rc/m-deploy.png`, `r1/dashboard-390.png`.
- (a) Row buttons are 32×26 and the pin/eye icons about 24×24. Make every tap target ≥40×40.
- (b) The seg filter "Pinned & ignored 0" is cut off at the right edge. Make it scrollable with a fade mask, or shorten it to "Held".
- (c) `#/updates` overflows horizontally by 4px (scrollWidth 394).
- (d) Deploy stacks What, Where, How and Plan, so the plan sits ~1500px down. Add a sticky bottom bar: `3 items · 7 servers · 1 change [Review]`.
- (e) Dashboard 390: KPIs take 300px and the first tile starts at y=662. Use the P1-1 banner instead.
- (f) The bottom nav omits Servers, the #1 job's entry point. Swap out Deploy (keep it in the drawer) or use 5 icons with Servers.

**P1-11 · Numbers disagree between views**
- Evidence: `r1/dashboard-1440.png` feed says *"46/78 jars have a known source, 27 outdated"*. The KPI says *24 updates*. The Updates policy card says *"49/78 … 24 outdated, 1 lookup error(s)"* (`r1/updates-1440.png`). The dialog later said *22 plugins · 58 installs*.
- Jars vs plugins vs installs are mixed without units, and "lookup error(s)" is programmer copy.
- Fix: use one summary format everywhere: `24 plugins outdated (64 installs) · 46 of 78 jars identified · 1 source failed [details]`.

**P1-12 · Undone jobs read as "Done · 2 changed"**
- Evidence: `r1/activity-1440.png`. The selected Deploy has an "undone" tag in the list, but the detail says Status `Done`, Outcome `2 changed`, and "Undone by" is a small orange id.
- Fix: add a banner at the top of the detail: `Reverted 5m ago by local · view undo job →`, set the status tag to `Reverted` (muted), and dim the results table to 60%.

**P1-13 · Loading and error: layout shift and a stale error**
- Evidence: `rc/skel-dashboard.png` vs `r1/dashboard-1440.png`. The skeleton has no placeholder for the update banner, so tiles jump about 86px when it arrives. `rc/err-dashboard.png`: after an `/overview` 500, navigating away and back shows the cached error again instead of revalidating on mount.
- Fix: add a 72px banner skeleton. `useQuery` should drop error entries on remount and refetch. The error card should keep the page skeleton behind it at 40% opacity, so the page doesn't collapse to one red box. Sidebar "Auto-update …" and avatar "?" also degrade oddly; show `—`.

**P1-14 · The pending-restart marker was never visible**
- Evidence: every r1 and rc screenshot. Job `20260926-040831-08f828` (real update-apply, 6 changed on elChapo01, M4, M8 and M9-aerons, not undone) exists, but `pending_restart.json` is absent from `sandbox/state` and `pending_restart` is false on every server.
- The state may have been reset, so I can't prove a bug, but the feature is unverified. Backend: add a test that runs apply, then asserts `servers[].pending_restart`. UI: when it shows, it must be a first-class amber tile flag, `Restart needed`, plus a Dashboard banner line `3 servers need a restart`. This is the next thing an operator does after updating.

---

## P2: polish

1. **Version formatting mismatch**: `Plan` installed `5.8 build 3638` vs latest `5.8+build.3638` (`rc/server-proxy.png`) reads as different versions. Normalise both through `display_version`, and use grey for "equal".
2. **Duplicate "check" buttons**: top bar "Check updates" plus Updates page "Check now". Keep the top bar one and show `Checking… 43/78` progress in it.
3. **Disabled target chips** (M0-proxy01, M9-homestead01) are nearly invisible and give no reason unless hovered (`r1/deploy-1440.png`). Render them at `--fg-3` with a suffix: `M0-proxy01 · proxy`, `M9-homestead01 · Fabric`.
4. **"None" as a clear action** in the Where header (`rc/deploy-esscfg-m1m8.png`). Label it `Clear`.
5. **Step 3 badge** stays "3" when the plan has no changes, while 1 and 2 turn into checks. Use a neutral `–` state and the copy `Targets already match. Nothing to do.`
6. **Tense in the remove dialog**: *"The jar is removed from 9 servers"* should read *"will be removed"* (`r1/remove-shared-1440.png`). The shared-folder warning, type-to-confirm and counts in that dialog are excellent otherwise.
7. **Tile meter** (green/blue/grey bar) has no legend entry for grey (untracked) and no numbers. Add a `title` with the counts, or drop it; the watch list already carries the meaning.
8. **Update banner gradient** (`linear-gradient(90deg, var(--accent-soft), transparent 70%)`) is the one surface that recalls the old "glow" aesthetic. Use a flat `--surface` with a 3px `--accent` left border.
9. **Datastore path** wraps mid-hash in Settings (`r1/settings-1440.png`). Use `white-space:nowrap; overflow-x:auto` with a copy button.
10. **Activity crumb** shows a raw job id `20260926-042006-00c66e` as the page title (`r1/activity-1440.png`). Use `Deploy · 5m ago` and keep the id in the kv list with a copy icon.
11. **Toast and job dock** both anchor bottom-right and overlap (`rc/updateall-dry-done.png`). When a dock exists, merge the completion into the dock header rather than spawning a toast.

---

## Bold ideas that would make it genuinely great

1. **Update inbox as a changeset.** Treat pending updates like a PR: one reviewable changeset with per-plugin changelog excerpts inlined (Modrinth `changelog` markdown, first 6 lines), a compat chip, a download size and sha-verified tick. One button, `Merge 22 updates`. Progress runs per server as a stage → verify → swap stepper, and ends with a **restart checklist** (`6 servers need a restart`, each ticked off via "Mark restarted").
2. **Canary rollout.** For "Update all", offer `Canary: elChapo01 first`. Apply to the source, watch its `logs/latest.log` for `Done (` and for plugin `ERROR`/`Could not load` lines, then continue automatically to M1–M8, or halt and auto-undo. This is the auto-update mode people will actually trust.
3. **Matrix as a command surface.** Click or shift-drag to select cells, which opens a floating action bar: `Update 6 cells · Align to elChapo01 (23.4) · Remove from 2`. Clicking a row name opens a plugin drawer with a version timeline per server (from Activity) and the source mapping editable inline.
4. **Config diffs in the plan.** For `Essentials/config.yml`, show a unified diff per server in the plan preview, collapsed to changed hunks. Add a "config drift" lens to the matrix (hash of `config.yml` per server) so "which servers have a hand-edited Essentials config?" is one glance.
5. **Keyboard-first operations.** `j/k` moves through rows and tiles, `u` updates the focused item (opening the review sheet), `x` selects, `.` opens the row menu, and `?` shows a shortcut sheet. Add a status-bar strip at the bottom of the shell: `Last check 4m · Next — · 0 jobs running · 3 restarts pending`.
