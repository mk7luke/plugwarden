# LGT AMP Sync 2.0 — Rebuild Brief

The owner wants a **ground-up redesign and rebuild**. Nothing of the current UI may survive:
no 4-card "What would you like to do?" wizard, no Tailwind CDN, no emoji-icon cards,
no blurred gradient blobs, no "Start Over" flow. New information architecture, new visual language.

## What the tool is

A private web panel (FastAPI, port 8078, behind Cloudflare Access) for one admin team running
a Minecraft network on CubeCoders AMP. Every AMP instance is a directory under a datastore base
(`/mnt/storage_ssd/ssd-live/<Instance>/Minecraft/plugins`). The panel manages plugin jars and
plugin config folders across those instances without SSH.

Real network (sandbox mirrors it with real jars):
- `elChapo01` — dev/staging Purpur server; the usual "source of truth" for pushes.
- `M0-proxy01` — Velocity proxy. Different plugin ecosystem. Never push Bukkit plugins to it.
- `M1-hub01`, `M3-hunger01`, `M4-skyblock01`, `M5-kitpvp01`, `M6-creative01`, `M7-bending01`,
  `M8-lifesteal01`, `M9-aerons-server01` — Purpur/Paper game servers (MC 1.21.x, see
  `Minecraft/version_history.json` → `"currentVersion": "... (MC: 1.21.6)"`).
- `M9-homestead01` — Fabric modded server, empty plugins dir (show it, but as "no plugins / Fabric").
- Platform detection: `Minecraft/velocity.toml` or `velocity-*.jar` → velocity; `purpur.jar` → purpur;
  `paperclip.jar` → paper; `fabric.jar` → fabric; else unknown.

## Jobs to be done (ranked by frequency)

1. **"Is anything out of date?"** — at a glance, per server, which plugins have updates.
2. **Update plugins** — one plugin on all servers that have it, all plugins on one server, or
   everything everywhere ("Update all"). Manual, or automatic on a schedule.
3. **Push config** — copy specific config files/folders (e.g. `Essentials/config.yml`,
   `LuckPerms/config.yml`) from a source server to chosen targets.
4. **Roll out a jar** — upload a new jar (or pick one from the source) and install/replace it
   on chosen servers, removing the old versioned jar (`CoreProtect-23.1.jar` → `CoreProtect-24.1.jar`).
5. **Remove** a plugin (jar + optionally its folder) from chosen servers.
6. **Spot drift** — same plugin at different versions across servers.
7. **Audit / undo** — what changed, when, by whom (Cloudflare Access header
   `Cf-Access-Authenticated-User-Email`), and roll it back.

## Information architecture (new)

Persistent left sidebar (collapses to a bottom bar / drawer on mobile) + top bar with global
search / command palette (Ctrl/Cmd+K) and "Check for updates" status.

- **Dashboard** — KPI strip (servers, plugins, updates available, drifted plugins, last check).
  Grid of server tiles: name, platform + MC version, plugin count, updates-available badge,
  drift badge, "pending restart" marker if we changed files since. Prominent **Update all**
  (with dry-run preview) and auto-update status/mode. Recent activity feed.
- **Servers → server detail** — plugin table (name, jar, installed version, latest compatible
  version, source, status), per-row Update / Pin / Ignore, "Update all on this server".
- **Plugins matrix** — rows = plugins, columns = servers, cells = version, color-coded
  (current / outdated / drift / missing). The signature view of the app. Row actions:
  update everywhere, align all to source version, remove.
- **Updates** — queue of all available updates grouped by plugin, with changelog link,
  select + apply; the auto-update schedule and policy live here too.
- **Deploy** — one-screen composer replacing the wizard: pick items from a source file browser
  (jars / folders / individual config files, with search), pick targets (chips + presets:
  All game servers, M1–M8, custom saved groups), choose action (Sync existing / Install /
  Replace versioned jar / Delete), live **plan preview** (automatic dry run showing per-server
  what would change), then Execute. Upload jar via drag-and-drop here.
- **Activity** — job history with structured per-server results, full log, who ran it, and **Undo**.
- **Settings** — datastore base (read-only display if overridden), source default, server
  groups, update sources mapping (manual Modrinth/Hangar/Spiget/GitHub id for plugins not
  auto-detected), auto-update policy.

## Visual direction

Calm, dense, professional ops console (think Linear / Vercel / Grafana-grade polish), not a
marketing page. Dark theme first, plus a light theme. System font stack or one self-hosted
variable font, monospace for versions/filenames. One accent color; semantic colors only for
status (up-to-date, update available, drift, error). Real SVG icon set (vendored, e.g. Lucide
SVG sprites) — no emoji. Keyboard-first, fast, no layout jank, skeleton loading states,
toasts for results, confirm dialogs only for destructive actions (type-to-confirm for delete
across many servers). Must work at 390px phone width.

## Technical constraints

- No build step in production. Serve from FastAPI: `app/templates/index.html` shell +
  `app/static/**`. Vendor any JS lib into `app/static/vendor/` (download now; do not load from a
  CDN at runtime). Preact + htm (ESM, vendored) or vanilla ES modules are both fine.
- Must run on Python 3.10, deps in `requirements.txt` (add `httpx` if needed).
- **Never touch `/mnt/storage_ssd`**. Develop only with `dev/serve.sh` (sandbox copy on
  http://127.0.0.1:18095). Port 8078 is production — do not stop, restart, or modify it or
  `/opt/lgt-amp-sync`. Do not edit `/usr/local/bin/amp-plugin-sync`.
- Screenshots: `.venv/bin/python dev/shoot.py <path> <out.png> [--w 390 --h 844] [--full] [--click sel]`.
  Screenshots go in the scratchpad `shots/rN/` folder given in your task.

## API contract (v2, JSON, prefix `/api/v2`)

All mutating operations run as background **jobs**; the endpoint returns `{job_id}` at once.

- `GET  /api/v2/overview` → `{servers:[Server], totals:{servers,plugins,updates,drift}, last_check, auto_update:{mode,next_run}, user}`
- `GET  /api/v2/servers` → `[Server]`; `Server = {id, platform, mc_version, plugin_count, updates, drift, pending_restart, eligible_target}`
- `GET  /api/v2/servers/{id}/plugins` → `[Plugin]`;
  `Plugin = {key, name, jar, version, sha1, folder, size, mtime, source:{kind,id,url}|null, latest:{version,url,download_url,published,changelog_url}|null, status: "current"|"outdated"|"unknown"|"pinned"|"ignored"}`
- `GET  /api/v2/matrix` → `{servers:[id], plugins:[{key,name, cells:{server_id:{version,jar,status}}}]}`
- `GET  /api/v2/servers/{id}/tree?path=` → directory listing inside plugins/ for the file browser.
- `POST /api/v2/updates/check` → job (checks Modrinth by sha1 with loader + MC version filter,
  then Hangar/Spiget/GitHub for manually mapped plugins). Results cached in state dir.
- `GET  /api/v2/updates` → `[{key,name,from_versions,to_version,servers:[id],changelog_url,download_url}]`
- `POST /api/v2/updates/apply` body `{items:[{key, servers:[id]}] | "all", dry_run}` → job.
  Downloads to staging, verifies hash, backs up the old jar to `STATE_DIR/backups/<job>/`,
  replaces jar on each server, removes the old versioned jar.
- `POST /api/v2/deploy/plan` body `{source, targets, action, items:{jars,folders,paths}, options:{install,backup}}` → per-server plan (dry run, `rsync --dry-run --itemize-changes`).
- `POST /api/v2/deploy` same body → job.
- `POST /api/v2/upload` multipart jar → `{upload_id, name, plugin_name, version}` (reads plugin.yml).
- `GET  /api/v2/jobs` / `GET /api/v2/jobs/{id}` → `{id, kind, status, user, started, finished, summary, results:[{server, item, action, outcome, detail}], log}`; `GET /api/v2/jobs/{id}/stream` (SSE log lines).
- `POST /api/v2/jobs/{id}/undo` → job restoring from that job's backups.
- `GET/PUT /api/v2/settings` → `{groups:{name:[ids]}, default_source, auto_update:{mode:"off"|"notify"|"apply", interval_hours, window, dry_run_first}, pins:{key:version}, ignores:[key], source_map:{key:{kind,id}}}`
