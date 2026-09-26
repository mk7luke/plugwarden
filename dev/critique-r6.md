# PlugWarden: Critique, round 6 (AMP integration)

> **Superseded (2026-09-26):** the owner removed the AMP integration. P1-1 (command guard bypass), P1-2 (proxy restart warnings) and AMP P2s 2–6 no longer apply. Two late AMP findings are also moot: the protected-command confirm dialog rendered underneath the console sheet (invisible and unclickable), and rolling restarts ran the full warning countdown on empty servers. **Still open for round 7:** P1-3 (Known-issues noise) and P2-1 (duplicated "(on every start)"). Round 7 must also verify the AMP removal leaves nothing behind.

Evidence paths:
- `r6/` = `/tmp/claude-1000/-home-luke-lgt-amp-sync/0cf2f751-d945-45e5-89cc-a0b56688015d/scratchpad/shots/r6/` (designer)
- `rc/` = `/tmp/claude-1000/-home-luke-lgt-amp-sync/0cf2f751-d945-45e5-89cc-a0b56688015d/scratchpad/shots/r6-critic/` (mine, captured live on :18095 against the mock AMP on :18100)

**Environment note.** Partway through my session the sandbox switched to `readonly: true`, marked M8-lifesteal01 and M9-homestead01 `unresponsive`, and the mock briefly refused connections. I assume someone else was testing states. I used that window to review the read-only, unresponsive and offline states live.

For the write flows (rolling restart, console, power), I intercepted `/api/v2/amp/status` in the browser to present `readonly:false` with players online (3 on elChapo01, 37 on M0-proxy01), and intercepted every `/power`, `/restarts/rolling`, `/console`, `/deploy`, `/updates/apply` and `/undo` request. So I saw exactly what the UI sends, and nothing reached the server. I checked the command guard directly, in-process (`amp.check_command`), with no side effects. I did not see a live rolling run end-to-end: the progress UI assessment comes from `r6/amp-rolling-*.png` and `tests/test_amp.py`.

---

## Score: 9.0 / 10

## NITPICK-ONLY: **no**

The round-5 P1 is fixed well. The AMP layer is thoughtfully built: mutating jobs are serialized, restarts wait for `Done (`, there is a health gate, stop-on-failure, read-only mode, backoff for hung instances, and an audited console. But two gaps in its safety net undercut what the UI promises. A third item is a regression in the known-issues signal.

---

## Round-5 items: status

| # | Item | Status | Evidence |
|---|---|---|---|
| P1-1 | Plugin that disables itself after `Done (` reported healthy | **Fixed** | elChapo01 now shows the red card "1 plugin not running after the last start · voicechat — disabled itself after startup · Not running". The Dashboard headline says "2 plugins are not running: voicechat on elChapo01, Voting on M1-hub01", and tiles show a red `1 failed` chip (`rc/ro-dash-1440.png`). There are tests (`test_rolling_restart_fails_when_a_plugin_disables_itself_after_start`, `test_canary_gate_fails_on_self_disable`) |
| P2-1 | Failed undo invisible on the original job | **Fixed** | "Undo failed" tag plus "View failed undo" |
| P2-2 | Startup failures not on the Dashboard | **Fixed** | Headline and tile chip |
| P2-3 | Limit row misaligned | **Fixed** | `r6/policy-limit-aligned-1440.png` |
| P2-4 | Fixture shot with the old name | **Fixed** | |
| P2-5 | Settings tab strip at 390 | **Fixed** | |

---

## What's good in the AMP layer
- **Server panel**: state dot, players `0 / 20`, CPU, memory `3.0 GB (50%)`, uptime, and Console / Restart… / Stop controls (`rc/w-chapo.png`). Read-only mode reads "AMP is read-only here". An unresponsive instance shows "AMP reported: AMP instance is not responding (ADS reports it unavailable)". An offline instance says "The AMP instance itself is stopped — start it in AMP" (`rc/ro-m8-1440.png`).
- **Tiles**: live dot and player count on each tile. Offline, Unknown and Fabric are distinguished (`rc/ro-dash-1440.png`).
- **Rolling dialog**: shows per-server player counts ("elChapo01 · 3 online", "M9-aerons-server01 · AMP instance offline", disabled), the footer "2 servers · 40 players online", warnings at 60/30/10 s, optional wait-for-empty (5–60 min), and "Check plugin startup after each restart, and stop on a failure" (`rc/w-rolling-dialog.png`).
- **Stop**: confirms with the player count ("37 players online will be disconnected. The server stays offline until someone starts it.").
- **Power and rolling jobs** default to `mutating=True`, so they queue behind a running deploy or update instead of restarting a server mid-copy. Good.
- **Console**: live SSE output with secrets redacted, audited commands, and a confirm dialog for protected commands.

---

## P0: none

## P1

**P1-1 · The protected-command guard is bypassed by namespaced and platform-specific forms**
- Evidence: the default `amp_command_denylist` is `["stop","restart","reload*","op *","deop *","lp user * permission set *","luckperms user * permission set *","whitelist off","ban-ip *","pardon *"]`. I ran `amp.check_command(cmd, denylist, confirm=False)` in-process:

  | Command | Result | Effect on a live server |
  |---|---|---|
  | `stop`, `/stop`, ` STOP ` | confirm required ✓ | |
  | **`minecraft:stop`**, **`bukkit:stop`** | **allowed** | stops the server |
  | **`end`**, **`shutdown`** | **allowed** | Velocity's stop commands: M0-proxy01 goes down and **the whole network disconnects** |
  | **`minecraft:reload`**, **`bukkit:reload`**, `rl` | **allowed** | full plugin reload |
  | **`plugman unload LuckPerms`**, `plugman reload Essentials` | **allowed** | PlugManX is installed on these servers |
  | **`minecraft:op Foo`**, `minecraft:deop Foo` | **allowed** | |
  | **`lp user Foo parent set admin`**, `lp group default permission set * true`, `lpv user Foo permission set *` | **allowed** | grants admin or `*` to everyone; `lpv` is LuckPerms on Velocity |

  Through the UI, typing `minecraft:stop` in the elChapo01 console sent `{"command":"minecraft:stop"}` with no confirm (`rc/w-console-minecraft_stop.png`). The request was intercepted by me.
- Why P1: the confirm dialog tells the operator that protected commands (stop, op, permission changes) are guarded. That guard is only as good as the pattern match, and the common Paper and Velocity aliases slip through. It's audited, but after the fact.
- Fix: normalise before matching instead of listing variants.
  1. Strip a leading `/` and any `namespace:` prefix from the first token (`minecraft:stop` → `stop`).
  2. Match on the first token plus a small verb table: `{stop, end, shutdown, restart, reload, rl, op, deop, whitelist, ban-ip, pardon, pardon-ip}`, plus `plugman|pm (unload|reload|disable)`, plus `(lp|luckperms|lpv|lpb) … (permission set|parent set|parent add)`.
  3. Choose the table by platform: Velocity adds `end`, `shutdown`, `velocity reload`, `lpv …`.
  4. Keep the user-editable list as an *addition*.
  5. Add tests for each row above.

**P1-2 · Rolling restart promises in-game warnings on the Velocity proxy, but sends `say`, which Velocity doesn't have**
- Evidence: `actions.py:337` sends `c.call("Core/SendConsoleMessage", {"message": f"say {text}"}, inst)` for every server. Velocity has no built-in `say` command (it answers "Unknown command"), so proxy players get no warning. The dialog still lets you pick M0-proxy01 with "37 online", keeps "Warn players in-game at 60 s, 30 s and 10 s" checked, and submits without a further confirm (`rc/w-rolling-dialog.png`; the intercepted body was `{"servers":["elChapo01","M0-proxy01"],"warn_seconds":[60,30,10],…}`).
- Restarting the proxy disconnects every player on every backend server. The Auto-update policy also offers *"Roll-restart the other updated servers in the window, one at a time with in-game warnings"*. That would include M0-proxy01 whenever a Velocity plugin (Geyser-Velocity, Maintenance) updates, so the network would drop unattended with no warning at all.
- Fix:
  1. Use a per-platform broadcast command: Paper/Purpur `say …`; Velocity `broadcast …` if a broadcast plugin exists, otherwise disable warnings for it. Make it a setting (`amp_broadcast_command.velocity`), defaulting to *none*.
  2. When the proxy is selected, render the row in danger style: `M0-proxy01 · 37 online · restarting the proxy disconnects everyone on the network`. When no broadcast command is configured, add `· no in-game warning on Velocity`.
  3. Require a type-to-confirm (`restart proxy`) when the proxy is in the set.
  4. Exclude the proxy from `auto_restart_rest` unless explicitly opted in, with a separate checkbox "Include the proxy (disconnects everyone)".

**P1-3 · Regression: Known issues now floods with banner and nag WARN lines**
- Evidence: M8-lifesteal01 (`rc/ro-m8-1440.png`) lists 11 "issues", including:
  - `PlugManX — ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~` (a row of tildes)
  - `PlugManX — Also, if you encounter any issues, please join my discord: https://discord.gg/…`
  - `PlugManX — Or create an issue on GitHub: …`
  - `FastAsyncWorldEdit — A new release for FastAsyncWorldEdit is available: 2.15.4` and a second FAWE update line

  The header reads "2 errors, 9 warnings". M1-hub01 has "2 errors, 10 warnings", and elChapo01 "6 errors, 18 warnings". In round 5 the same panel had 2–6 rows, all real. Every line of a multi-line WARN banner is now a separate issue, so the two real errors are buried.
- Fix:
  1. Group consecutive lines from the same plugin, logger thread and second into one issue, titled by its first non-decorative line. Treat lines matching `^[\W_]{8,}$` as decoration.
  2. Classify "new version available / update available / out of date" nags into a separate collapsed counter: `4 plugins nag about updates — see Updates`.
  3. Show ERROR/SEVERE/exception issues first. Collapse WARN-only issues behind `Show 5 warnings`.

---

## P2: cosmetic or minor

1. **Duplicated suffix**: "voicechat — disabled itself after startup (on every start) (on every start)" in the elChapo01 red card. The reason string already contains the suffix and the UI appends it again.
2. **Tile name truncation**: `elChap…` because the flags (14 updates, ⇄2, 1 failed) take the space, and the footer truncates to `Purpur 1.21.6…` (`rc/ro-dash-1440.png`). Truncate the flag labels first (`14`, `⇄2`, `✕1` with tooltips) and never the server name.
3. **State wording**: an unresponsive instance shows "Unknown" in the panel and on the tile, while the reason says "not responding". Label it `Not responding`.
4. **Wait-for-empty timeout is ambiguous**: "Wait until the server is empty, up to 10 min" doesn't say what happens at 10 min. The backend restarts anyway and notes "N player(s) still online". Add ", then restart anyway (with warnings)".
5. **Palette in read-only mode**: "restart" returns nothing. Show the verbs disabled, with "AMP is read-only here", so people learn the feature exists.
6. **Servers list page** has no live-state column, although tiles and the server page do. Add a dot, state and players column.

---

## To reach NITPICK-ONLY: yes
1. Normalise and verb-match the command guard, with platform tables and tests (P1-1).
2. Add a Velocity-aware broadcast, a proxy danger confirm, and exclude the proxy from auto-restart by default (P1-2).
3. Group and de-noise Known issues (P1-3).

After a writable live rolling run confirms the progress and failure UI, the rest is cosmetic.
