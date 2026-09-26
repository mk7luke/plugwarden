<div align="center">

<img src="app/static/img/mark.svg" width="72" alt="" />

# PlugWarden

**The plugin control tower for Minecraft networks running on CubeCoders AMP.**

See every plugin on every server at a glance. Update the whole network in two clicks,<br>
push configs without clobbering each server's identity, and undo anything.

<img src="docs/screenshots/dashboard.png" alt="PlugWarden dashboard: 24 plugins have updates across 10 servers, with per-server update tiles" width="100%" />

</div>

---

## Why this exists

If you run a network on AMP — a Velocity proxy, a hub, a handful of game-mode servers — you know the drill.
Ten servers, thirty plugins each, and every update means clicking through each instance's file manager,
uploading the same jar again and again, deleting the old versioned jar, and hoping you didn't miss one.
Configs are worse: copy `LuckPerms/config.yml` everywhere and you've just told every server it's `survival`.

AMP's built-in Store is great for installing content on **one** instance. PlugWarden is for the **network**:

| | AMP Store | PlugWarden |
|---|:---:|:---:|
| Install plugins from Modrinth / Hangar | ✅ per instance | — (use AMP or Deploy) |
| Detect updates for jars you installed by hand | — | ✅ by file hash |
| One view of every plugin × every server | — | ✅ |
| Spot version drift between servers | — | ✅ |
| Update the whole network in one reviewed changeset | — | ✅ |
| Push configs while keeping per-server values | — | ✅ |
| Check the plugin actually started after an update | — | ✅ from server logs |
| Canary rollouts, auto-update with guardrails | — | ✅ |
| Undo any change, full audit log | — | ✅ |

<sub>AMP Store column based on the AMP 2.7–2.8 release notes. PlugWarden works alongside the Store; it doesn't replace it.</sub>

PlugWarden reads your AMP datastore directly. **No AMP API credentials, no server-side plugin, no changes to your instances** until you ask for one.

---

## What it does

### 🧭 A dashboard that answers "is anything out of date?"

Every server gets a tile: what's outdated (`current → latest`), what's drifted, what needs a restart, and whether any plugin failed to start. One headline tells you the state of the whole network — and what changed *since you last looked*.

### 🧮 The plugin matrix

Every plugin as a row, every server as a column. Green is current, blue has an update, purple has drifted from the rest of the network. Select cells and act on them.

<img src="docs/screenshots/matrix.png" alt="Plugins × servers matrix, colour-coded by status" width="100%" />

### ✅ Reviewed, exact updates — never a surprise

Every **Update** button opens the same review sheet: server × plugin, old jar → new jar, a Minecraft-compatibility check, verified download hashes and changelog excerpts (with risky words like *breaking*, *migration* or *Java 25* flagged). **Apply** runs exactly that plan — if anything changed on disk since you reviewed it, PlugWarden refuses and re-plans instead of guessing. Old versioned jars are removed, so no server ends up with two copies of a plugin.

<img src="docs/screenshots/review-sheet.png" alt="Review sheet grouped by plugin, with version changes, changelog excerpts and verified hashes" width="100%" />

Update identity comes from the jar itself: PlugWarden hashes every `.jar` and looks it up on **Modrinth** (with your server's loader and Minecraft version), and you can map anything else to **Hangar**, **Spiget** or **GitHub Releases**. Hand-installed jars are recognised just like Store-installed ones.

### 🩺 "Did it actually start?"

After a restart, PlugWarden reads the server's own logs and compares the new start with previous ones. Plugins that failed to load or disabled themselves are flagged **Not running**, with a plain-English cause and a next step — *missing dependency: install TheCore (it's on elChapo01)* comes with a one-click, pre-filled Deploy. Long-standing noise is kept separately as **Known issues**, so real regressions stand out.

<img src="docs/screenshots/server-health.png" alt="Server page showing a plugin that is not running because of a missing dependency, with a one-click fix" width="100%" />

### 🚚 Deploy configs without clobbering server identity

Push jars, folders or individual config files from a source server to any set of targets. Every deploy starts as a **dry-run plan** showing exactly what would change on each server, with diffs (secrets redacted).

The part that makes it safe on a real network: PlugWarden detects **server-specific values** — LuckPerms `server:`, CoreProtect `table-prefix`, Plan's server name and UUID, DiscordSRV channel IDs — and makes you choose. *Keep each server's own values* merges the source file while preserving those lines (comments and formatting intact); anything it can't merge safely is refused, not guessed.

<img src="docs/screenshots/deploy-guard.png" alt="Deploy composer detecting that LuckPerms server: differs on every target, asking to keep or overwrite" width="100%" />

Folder pushes never touch live data: databases, `userdata/`, `playerdata/`, logs and caches are excluded unless you explicitly opt in, and every file a mirror would delete is listed in red first.

### ↩️ Undo everything, audit everything

Every real change is backed up first — only the files it actually touched — and can be undone from **Activity**, which also records who did what (from your Cloudflare Access identity), and who viewed which config.

<img src="docs/screenshots/activity.png" alt="Activity log showing an update job across 9 servers with an Undo button and per-server startup checks" width="100%" />

### 🤖 Auto-update with guardrails

Off by default. When you turn it on, it only installs **verified release builds** that are at least 48 hours old, tries them on a **canary server** first, waits for that server to restart and log a clean plugin enable, soaks for 24 hours, and only then rolls out — inside your maintenance window, capped per run. A plugin that fails on the canary is held back and never retried automatically.

### ⌨️ Fast to drive

`Ctrl/⌘ K` opens a command palette that understands verbs — *update core* → "Update CoreProtect on 6 servers…". `j`/`k` walk lists, arrow keys move around the matrix, `?` shows every shortcut. Dark and light themes, and it works on your phone.

<table>
<tr>
<td width="62%"><img src="docs/screenshots/palette.png" alt="Command palette with 'update core'" /></td>
<td width="38%"><img src="docs/screenshots/mobile-dashboard.png" alt="Dashboard on a phone" /></td>
</tr>
</table>

---

## How it works

AMP keeps every instance in a datastore folder. PlugWarden mounts that folder and works on the plugin directories directly:

```
<datastore>/                      e.g. /home/amp/.ampdata/instances
├── Proxy01/Minecraft/plugins/    ← Velocity proxy (detected, handled separately)
├── Hub01/Minecraft/plugins/
├── Survival01/Minecraft/plugins/
└── …
```

- **Discovery:** every instance with a `Minecraft/plugins` folder is a server. Platform (Paper, Purpur, Velocity, Fabric, or generic Bukkit) and Minecraft version are detected from the instance files.
- **Identity:** each jar's `plugin.yml` / `paper-plugin.yml` / `velocity-plugin.json` gives its name and version; its SHA-1 gives its exact build. Bukkit and Velocity builds of the same plugin (LuckPerms, Geyser…) are tracked separately.
- **Changes:** made with `rsync` per item, planned first, applied exactly, backed up for undo. The container runs as your `amp` user, so file ownership never changes.
- **Health:** read from `Minecraft/logs/latest.log` and the rotated `*.log.gz` files — no agent inside the game server.

PlugWarden never restarts your servers. It tells you which ones need a restart, and marks them done when you do.

---

## Quick start

**Requirements:** Linux host running AMP, Docker with Compose, and (recommended) a Cloudflare Tunnel with Cloudflare Access in front of it.

```bash
git clone https://github.com/mk7luke/plugwarden.git /opt/plugwarden && cd /opt/plugwarden

cp .env.example .env && chmod 600 .env      # then edit it (see below)
mkdir -p data && sudo chown "$(id -u amp):$(id -g amp)" data && sudo chmod 700 data
```

Edit `docker-compose.yml` so the container runs as your AMP user and mounts your datastore **at the same path inside the container**:

```yaml
    user: "1001:1001"                  # output of: id -u amp ; id -g amp
    volumes:
      - /home/amp/.ampdata/instances:/home/amp/.ampdata/instances
      - ./data:/var/lib/lgt-amp-sync
      - /etc/localtime:/etc/localtime:ro
```

Then:

```bash
docker compose up -d --build
curl -s http://127.0.0.1:8078/healthz        # {"ok":true}
```

The port is published on `127.0.0.1` only. Point your Cloudflare Tunnel at `http://localhost:8078`, open your hostname, sign in through Access, and press **Check updates**.

### Configuration (`.env`)

| Variable | Example | What it does |
|---|---|---|
| `LGT_BASE_OVERRIDE` | `/home/amp/.ampdata/instances` | Your AMP datastore (same path as mounted). |
| `LGT_AUTH` | `cf-access` | Verify the Cloudflare Access JWT on every request. |
| `LGT_CF_TEAM_DOMAIN` | `yourteam.cloudflareaccess.com` | Your Zero Trust team domain. |
| `LGT_CF_AUD` | `4f1c…e9` | The Access application's Audience (AUD) tag. |
| `LGT_HOSTNAME` | `plugins.example.com` | Public hostname; added to the Host allowlist. |
| `LGT_ALLOWED_HOSTS` | | Extra allowed `Host` headers (comma-separated). |
| `LGT_MIN_FREE_GB` | `2` | Refuse new jobs when disk space runs low. |

Everything else — default source server, server groups, update-source mappings, pins and ignores, auto-update policy, backup retention — lives in **Settings** in the UI.

> **No Cloudflare?** PlugWarden refuses to run unauthenticated on anything but loopback. For a quick local look you can run it with `LGT_AUTH=none` (and no `LGT_HOSTNAME`) and reach it through an SSH tunnel: `ssh -L 8078:127.0.0.1:8078 your-host`.

Full deployment, security and migration notes: [`dev/DEPLOY_NOTES.md`](dev/DEPLOY_NOTES.md).

---

## Safety model

- **Read-only until you act.** Discovery, update checks and health checks never write to your servers.
- **Plan → apply exactly.** Updates and deploys apply the reviewed plan or nothing; stale plans are refused.
- **Backups and undo** for every real change, with retention limits so backups never fill your disk.
- **Live data protected** by default in folder pushes; **server-specific values** require an explicit choice.
- **Proxy-aware:** Bukkit plugins are never pushed to your Velocity proxy (and vice versa).
- **Locked down:** Cloudflare Access JWT verification, Host allowlist, CSRF checks, a strict Content-Security-Policy, secrets redacted in diffs, and an audit trail of who changed and who viewed what.

---

## FAQ

**Does it replace AMP?** No — it sits beside it. Keep using AMP for consoles, restarts, schedules and backups; use PlugWarden for plugins and configs across the network.

**Does it work with the AMP Store?** Yes. Store-installed and hand-installed jars look the same to PlugWarden: it identifies them by hash.

**Which servers are supported?** Paper and Purpur (plus other Bukkit-based servers such as Spigot) and Velocity proxies. Fabric/modded instances are shown, but mods aren't managed.

**What if a plugin isn't on Modrinth?** It's listed as untracked. Map it to Hangar, Spiget or a GitHub repository in **Settings → Update sources**, or keep deploying it from your source server as a jar.

**Does it need AMP's API or my AMP login?** No. It only needs read/write access to the datastore folder, as the `amp` user.

---

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt pytest playwright
.venv/bin/python -m pytest -q                 # backend test suite
dev/serve.sh                                  # dev server on 127.0.0.1:18095 against a sandbox copy
.venv/bin/python dev/a11y_focus.py            # keyboard-focus regression checks
.venv/bin/python dev/readme_shots.py          # regenerate these screenshots
```

Develop against a **copy** of your datastore (`LGT_SANDBOX=/path/to/sandbox dev/serve.sh`), never the live one. Stack: FastAPI + a no-build Preact/htm frontend, served from one container.

---

<div align="center">
<sub>Built for the <b>LGT Network</b>. Not affiliated with CubeCoders, Mojang or Modrinth.</sub>
</div>
