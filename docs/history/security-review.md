# LGT AMP Sync 2.0: security and data-safety review

Scope: `app/*.py` (plus a pass over `app/static/js` for HTML sinks), `run.sh`, `lgt-amp-sync.service`.
Method: code read, the test suite (`88 passed`), scratch reproductions using the app's own functions
against temp directories, and probes of the sandbox on 127.0.0.1:8095 (read-only or dry-run requests only).
Port 8078, /opt, /var/lib/lgt-amp-sync and /mnt/storage_ssd were not touched. Where I cite line numbers they
match the files as of this review. `inventory.py` was being edited while I worked.

| Severity | Count |
|---|---|
| CRITICAL | 1 |
| HIGH | 2 |
| MEDIUM | 6 |
| LOW | 8 |

What already holds up (no action needed):
- **rsync argument injection.** `engine.rsync` refuses non-absolute paths (`engine.py:87`), so names starting with `-` or containing `:` can't become options or `host:path`. `cp` gets `--`.
- **Path handling in the API.** `check_rel` and `resolve_in` reject `..`, absolute paths, NUL and backslash, and resolve symlinks against the plugins root (`inventory.py:119-148`). Tree, search, diff, deploy, delete and undo all go through them. Search does not descend into symlinked directories.
- **rsync symlink handling.** rsync runs with `--safe-links`, and without `--keep-dirlinks` it replaces a symlinked destination directory instead of following it.
- **YAML and zip parsing.** YAML uses `safe_load`, so objects can't be constructed. zip reads are capped at the declared 2 MiB per member, and CPython bounds decompression to the declared size.
- **Downloads.** The https-only hook applies to every redirect hop. Size is capped while streaming. The hash is checked before the jar is staged. `expect_key` stops an update from installing a different plugin.
- **Plans.** Execute re-runs the dry run under the mutate lock and compares fingerprints and source hashes. Update rows re-hash the from-jars right before swapping. Plan consumption is under a lock.
- **Frontend.** htm/Preact text escaping is used everywhere. There is no `innerHTML` or `dangerouslySetInnerHTML`, and SSE lines are split per line, so the log can't inject events.

---

## CRITICAL

### C1. Anyone who can reach port 8078 gets full, unauthenticated admin: the service binds 0.0.0.0 and the app has no auth of its own
- **Where:** `run.sh:5` (`--host 0.0.0.0 --port 8078`, also `run.sh.deployed:8`); `main.py:91-93` (`user_of` trusts `Cf-Access-Authenticated-User-Email` as sent); `main.py:79-88` (no Host check). `ss -ltn` confirms production is listening on `0.0.0.0:8078`.
- **Why:** Cloudflare Access protects only the tunnel hostname. Any request that reaches the socket some other way skips it: the LAN, a VPN peer, another container or VM on the host, or a port-forward. The app then trusts everything in the request.
- **Exploit:**
  1. From the LAN, `curl -F file=@evil.jar http://<host>:8078/api/v2/upload` uploads a jar whose `plugin.yml` names an installed plugin, for example `CoreProtect`.
  2. `POST /api/v2/deploy/plan` with `{"action":"install","targets":[...all game servers...],"items":{"uploads":["<id>"]}}`, then `POST /api/v2/deploy {"plan_id":...}`.
  3. Every Minecraft server now runs attacker code as `amp` on its next restart.
  4. The same access can delete every plugin (`action:"delete"`), push arbitrary configs, point `source_map` at an attacker's GitHub repo, or turn on `auto_update.mode="apply"`.
  5. Sending `Cf-Access-Authenticated-User-Email: someone@else` writes someone else's name into the audit log. Verified on the sandbox: `/api/v2/overview` echoed the spoofed `ceo@victim.com`.
- **DNS rebinding makes this remote:** a web page visited by anyone on the LAN can rebind its hostname to the server's LAN IP. The middleware only compares Origin with Host, and in a rebinding attack both are the attacker's hostname. Sandbox probe: `POST /api/v2/deploy/plan` with `Host: evil.example:8095` and `Origin: http://evil.example:8095` got past the middleware (400 from body validation). A real cross-origin request got 403.
- **Fix, in order:**
  1. Bind `--host 127.0.0.1` if `cloudflared` runs on this host. If it runs elsewhere, bind the specific interface and firewall 8078 so only the tunnel host can reach it. Also update `run.sh.deployed` and `INSTALL.md`.
  2. Validate `Cf-Access-Jwt-Assertion` on every request: RS256 signature against `https://<team>.cloudflareaccess.com/cdn-cgi/access/certs`, plus `aud` equal to the Access application AUD tag, plus `exp`. Take the user's email from the verified JWT, not from the plain header. Fail closed with 401 when it is missing. Allow a `LGT_DEV_NO_AUTH=1` escape only for `dev/serve.sh`.
  3. Add `TrustedHostMiddleware(allowed_hosts=["<tunnel hostname>", "127.0.0.1", "localhost"])` to shut out DNS rebinding.

---

## HIGH

### H1. Undo of a folder push rolls back live player data and databases, which the push never touched
- **Where:**
  - `engine.py:356-364` (`_sync_dir` excludes `DATA_EXCLUDES` from the rsync);
  - `engine.py:409-410` (`ctx.backup.save(tgt, rel)` backs up the whole folder);
  - `engine.py:143-177` (`Backup.save` `cp -a`s the entire tree, data included);
  - `engine.py:943-968` (`_restore_entry` moves the current folder aside and copies the old one back in full).
- **Scenario:** An admin pushes `Essentials/` from elChapo01 to M1–M8. Userdata, databases, logs and `*-storage` are excluded from the push, but the backup copies the whole folder. The next day someone clicks Undo to revert a bad `config.yml`. Every `Essentials/userdata/*.yml` goes back to its state before the push: balances, homes and kits on all 8 servers. Players who joined since then are removed. The same applies to any plugin folder with a `.db`, `.sqlite` or `.h2` file (LuckPerms H2, CoreProtect SQLite, and others), and those files are replaced while the server has them open.
- **Reproduced** with `engine.sync_folder` and then `engine.run_undo` in a temp dir (scratchpad `sec_undo.py`):
  ```
  deploy: [('changed', '1 change(s)')]
  undo:   [('changed', 'restored previous version')]
  player.yml after undo: money: 100            # was 999999 after the push
  newplayer.yml exists after undo: False       # player created after the push is gone
  ```
  The overwritten live state goes into the undo job's backup, so it can be recovered by hand. Nothing tells the operator that this happened.
- **Fix:**
  1. Back up only what the rsync will actually change. For mirror and sync, run the real rsync with `--backup --backup-dir=<job backup>/files/<server>/<rel>` (plus `--suffix=`), which saves exactly the overwritten and deleted files. Record the created files from the itemized output so undo can delete them. Or use the dry-run change list to copy just those paths.
  2. Undo then restores and removes only those files, never the whole folder, and never anything matching `DATA_EXCLUDES` unless the original job ran with `include_data`.
  3. Before restoring any file, check that it still has the hash or mtime the job left behind. If it changed, report a conflict and skip it rather than overwrite.
  4. Undo's dry run should list every file it will overwrite.

### H2. A 529-byte `plugin.yml` exhausts memory (alias bomb) or crashes every page (deep nesting); the service and possibly a live game server go down
- **Where:**
  - `inventory.py:194-200` (`yaml.safe_load`, catching only `yaml.YAMLError`);
  - `inventory.py:210-228` (`_desc` calls `str(name)`, `str(description)` and `str(x)` on values that may be expanded aliases);
  - `inventory.py:295-299` (`jar_meta` catches only `BadZipFile`, `OSError` and `KeyError`, and caches only on success).
- **Reproduced** (scratchpad `sec_bomb.py`, `deep.jar`):
  - Anchors `a0..a8`, each a list of nine references to the one before, with `name: *a8`: `read_descriptors` hit **MemoryError after 6.7 s under a 1 GiB cap**. Without the cap it keeps allocating. On a host running about 11 JVMs, the kernel OOM killer usually picks the biggest process, which is a Minecraft server.
  - `depend: [[[[…5000…]]]]` raises **RecursionError**. `jar_meta` doesn't catch it, so `snapshot()` fails and `/overview`, `/servers`, `/matrix` and `/updates` all return 500 while that jar exists in any plugins folder. The `warm-cache` thread hits it again on every restart.
- **How such a jar gets in:**
  - `POST /upload`, which parses before anything else is checked;
  - an update downloaded from Modrinth, Hangar or GitHub (it is parsed in `replace_jar` → `describe_jar`, possibly unattended by the scheduler);
  - any jar that a plugin, AMP's file manager or a person drops into `plugins/`.
- **Fix:**
  - Parse descriptors with a loader that refuses aliases (subclass `SafeLoader` and override `compose_node` to raise on `AliasEvent`), or only accept scalar `name`, `version` and `description` and never `str()` a container.
  - Catch `Exception` (including `RecursionError` and `MemoryError`) in `_yaml_fields`, `read_descriptors` and `jar_meta`.
  - Cache the failure (`valid_zip: False`, empty descriptors) so the jar isn't parsed again on every request.
  - Put a cap on each descriptor field's length before storing it.
  - Optionally run upload parsing in a subprocess with `RLIMIT_AS` set.

---

## MEDIUM

### M1. Config-diff redaction misses common secret shapes
- **Where:** `inventory.py:478-511` (`_SECRET_KEY`, `_SECRET_LINE`, `_not_secret`, `redact`).
- **Reproduced** (scratchpad `sec_redact.py`). Each of these leaked in cleartext through `GET /api/v2/diff`:
  - Block scalars: `password: |` and `bot-token: >-`. The opener line gets redacted, or is skipped as a "block opener", but the indented lines holding the secret come through.
  - Numeric secrets: `password: 12345678`. `_not_secret` treats every number as a toggle.
  - Inline JSON: `{"password": "hunter2", ...}`. The regex only matches keys at the start of a line.
  - Lists: `passwords:` followed by `- hunter2`.
  - Common key names that aren't covered: `pass`, `pw`, `pwd`, `auth`, `key`, `credentials`, `access`. Also bare tokens such as `ghp_…`, `sk_live_…`, and Discord bot tokens under a neutral key.
- **Fix:**
  - When a secret-named key has a block or collection opener (`|`, `>`, `|-`, `>-`, `[`, `{`, empty), redact every following line that is indented more deeply.
  - Treat numbers under secret-named keys as secrets.
  - For `.json` files, redact through a real parse (`json.loads`, walk the tree, re-dump) rather than line regexes.
  - Add `pass|pwd|pw|auth|credential|key$` to the key list, plus value patterns for well-known token formats (`ghp_`, `github_pat_`, `sk_live_`, `xox[bap]-`, Discord `[\w-]{24}\.[\w-]{6}\.[\w-]{27,}`, JWT `eyJ…\.…\.…`).
  - Consider redacting by default and letting only an allowlist of known-safe keys show.

### M2. Deploy-plan "server-specific" warnings return config values without redaction and without an access-log entry
- **Where:** `configmerge.py:271-303` (`_shown` redacts only when the key path matches `SECRET_NAME`), returned by `engine.plan` → `/api/v2/deploy/plan`, stored in `STATE_DIR/plans/<id>.json` (`engine.py:824`), and echoed in the 409 `needs_decision` body (`actions.py:462-467`).
- **Scenario:** A plan that pushes `LuckPerms/` returns `{"key":"storage.mongodb-connection-uri","target_value":"mongodb://admin:hunter2@db"}` for each target, and `data.pass` or `auth` values likewise. The diff endpoint would redact that URL through `_SECRET_VALUE`, but this path doesn't, and nothing is written to `access.log`, so it gets around the read-audit the diff feature was built around.
- **Fix:**
  - Put one shared redaction function in `configmerge` and use it for both `redact()` and `_shown()`, covering both key and value patterns (see M1).
  - Don't store raw values in plan files.
  - Record plan reads that include values in the access log, or return only `changed: true/false` for values.

### M3. Security-relevant settings changes are unaudited, and `source_map` silently replaces the trusted update source
- **Where:**
  - `main.py:547-549` (`PUT /settings` has no user and no record);
  - `main.py:334-356` (pin and ignore);
  - `main.py:432` (upload does not record a user);
  - `updates.py:334-367` (a mapping overrides Modrinth identification: `not mapping or mapping["kind"] == "modrinth"`).
- **Scenario:** Someone with panel access (see C1 for how easy that currently is) sets `source_map["bukkit:luckperms"] = {"kind":"github","id":"attacker/LuckPerms"}` and `auto_update.mode="apply"`.
  1. The next scheduled run downloads the attacker's release.
  2. GitHub's `digest` makes it count as "verified", although that only shows the file matches what the attacker published.
  3. The jar's `plugin.yml` says `name: LuckPerms`, so it passes `expect_key`.
  4. The jar is installed on every server that has LuckPerms.
  The only record is job `user: "scheduler"`. Nothing shows who changed the mapping or when.
- **Fix:**
  - Write every settings, pin, ignore and upload mutation to an append-only audit log with user, time and a before/after diff. Put it in `access.log` or a new `audit.log`, and show it in Activity.
  - Display the effective update source for each plugin in the Updates view, and flag it clearly when a manual mapping overrides a Modrinth hash match.
  - Require a separate confirmation when switching auto-update to `apply`.

### M4. Auto-update `apply` is an unattended supply-chain path with no soak time, canary, or blast-radius limit
- **Where:** `scheduler.py:318-337`, `actions.py:442-453`, `updates.py:669-695`.
- **Details:**
  - `require_verified` only proves the download matches the hash in the same API response, so it guards against corruption, not a compromised author account or project.
  - A release published minutes ago can be installed on all 11 servers, proxy included, in one run.
  - GitHub and Hangar sources carry no compatibility data for the Minecraft version, yet GitHub counts as verified.
  - The swap is made under running servers and takes effect at their next restart, possibly well after the job, with nobody watching.
- **Fix:**
  - Only auto-apply versions that have been published for at least N days (a setting, default 3–7) and are `version_type == "release"`.
  - Apply to a canary group first (for example elChapo01) and to the others only after a later cycle, if the canary has restarted cleanly.
  - Cap the number of rows per run.
  - Auto-apply only when the source is a Modrinth hash match, never through a manual mapping, unless the admin opts in per plugin.
  - Keep the default mode as `notify`.

### M5. Unbounded disk growth in the state dir (backups, uploads, upload spooling, job records)
- **Where:**
  - Backups: `engine.py:217-221`, keep-by-count 100 and up to 1000 (`settings.py:169`); every mirror copies the whole folder including data (H1). A `dynmap/`, `BlueMap/` or `CoreProtect/` push can mean GBs per server per job.
  - Free-space guard: `engine.py:185` requires only 256 MiB free, so backups can fill the filesystem almost completely. If `/var/lib` shares the root filesystem, that fills root.
  - Uploads: `main.py:432-475`. Starlette/python-multipart spools the whole request body to `/tmp` before the handler's 200 MB check runs, so there is no limit when requests don't come through Cloudflare (C1). Each upload is kept for 7 days with no count limit, and pruning only runs on the next upload.
  - Job records: `jobs.py:147-151` and `jobs/*.json` (up to 20,000 log lines each) are never pruned.
- **Fix:**
  - Retention by bytes as well as count (for example `backup_max_gb`), and refuse a job whose backup would push the dir over it, or leave less than about 10% of the disk free.
  - Fix H1 so backups hold only the changed files.
  - Reject uploads with `Content-Length` over the limit in middleware before parsing. Limit uploads to about 20 files or 1 GB in total, and prune on a timer.
  - Prune job JSON older than N days, or beyond N jobs, keeping those whose backups still exist.

### M6. The undo safety check runs only when undo is submitted, so a queued job can slip past it
- **Where:** `actions.py:517-545` (`overlapping_later_jobs` at submit time); `engine.py:971-990` (only looks at backup dirs, which a queued job has not created yet).
- **Scenario:**
  1. Deploy B, which touches `Essentials/`, is queued behind a long update job.
  2. The admin clicks Undo on an earlier job A that also touched `Essentials/`. No later backups exist yet, so the undo is accepted.
  3. With FIFO order, B runs and then undo-A restores A's pre-state over B's changes. B still shows as applied and undoable.
- **Fix:**
  - Repeat `overlapping_later_jobs` (and treat queued or active mutating jobs that touch the same servers as conflicts) inside the undo job body after the mutate lock is taken, and abort with a clear result.
  - Also refuse undo while any mutating job is queued.

---

## LOW

### L1. Download URLs are not restricted to known hosts (SSRF, unverified sources)
- **Where:** `updates.py:112` (Modrinth `files[].url` accepted as given), `updates.py:170` (Hangar `externalUrl`, which can be any https host), `updates.py:228` (GitHub), `updates.py:631-647`.
- **Risk:** A malicious or compromised API response makes the server GET any https URL, including internal hosts (for example `https://192.168.x.x/admin?...`), with redirects followed. The response is written to staging. Hangar external and Spiget downloads are unverified and can be applied manually.
- **Fix:**
  - Allowlist download hosts per source: `cdn.modrinth.com`; `hangarcdn.papermc.io`; `github.com` and `objects.githubusercontent.com`/`release-assets.githubusercontent.com`; `api.spiget.org`.
  - Refuse private or loopback IPs after DNS resolution.
  - Compare `latest.size` with the bytes received.

### L2. The staging directory name comes from an API-supplied "hash" string that isn't validated
- **Where:** `updates.py:636-640`: `config.state("staging", ident[:40])`, where `ident` is whatever `hashes.sha512/sha256` the API returned.
- **Risk:** A value such as `../../../../opt/lgt-amp-sync/app/x` makes the app create directories and write `<name>.jar.part` outside staging. `_verify` then fails and removes the `.part`, but the directories stay. The response has to come from the upstream API over https.
- **Fix:** Accept only `^[0-9a-f]{40,128}$` for each hash when resolving (`_modrinth_latest`, `_resolve_hangar`, `_resolve_github`), and derive the staging dir from `sha256(ident)`.

### L3. Paths may contain newlines or control characters, which lets them spoof plan, itemize and log output
- **Where:** `inventory.py:119-134` (`check_rel` rejects only NUL and backslash); rsync `--itemize-changes` output is split on newlines (`engine.py:92-93`) and fed into `_deletions` and fingerprints.
- **Risk:** A file named `a\n*deleting Essentials/` found on a source server shows up as fake rows in the plan, delete list or job log. That misleads the reviewer, though it doesn't change what rsync does.
- **Fix:** Reject `[\x00-\x1f\x7f]` in `check_rel` and in names from `iterdir` and scans, and pass `-8`/`--outbuf`, or use `--out-format='%i %n'` with `--from0`-style parsing.

### L4. Some paths are joined without re-resolving, so a symlinked subdirectory inside a pushed folder is followed out of `plugins/`
- **Where:**
  - `engine.py:369-372` (`merge_file` uses `tgt.plugins_dir / rel`);
  - `engine.py:693-707` (`_config_files` checks `(tgt.plugins_dir / fr).is_file()`);
  - `engine.py:719-723` (`analyze_server_specific` parses the target file).
  None of these call `resolve_in` on the file itself, only on the top-level item.
- **Risk:** If a target has `plugins/Foo/sub -> /home/amp/…` (for example planted by a plugin), a plan that pushes `Foo/` reads `/home/amp/…/config.yml`. Its non-secret values come back in plan warnings (M2), and a `preserve_keys` merge writes through the link. The attacker needs write access as `amp` in `plugins/`, so this is defence in depth.
- **Fix:** Call `resolve_in(tgt, f)` for every file used in merge or analysis. In `read_lines`, open with `O_NOFOLLOW` and check with `os.path.realpath`.

### L5. Uploaded zips with huge central directories use a lot of memory
- **Where:** `inventory.py:250-256` (`zipfile.ZipFile` plus `namelist()` on a 200 MB upload).
- **Risk:** A few million empty entries means several GB of `ZipInfo` objects.
- **Fix:** Before `ZipFile`, read the end-of-central-directory record and refuse more than about 100k entries. Or run parsing in a memory-limited subprocess (see H2).

### L6. CSRF middleware accepts `Origin: null` and requests with neither Origin nor Sec-Fetch-Site
- **Where:** `main.py:79-88`.
- **Risk:**
  - Sandboxed iframes and some redirects send `Origin: null`, and browsers without Fetch Metadata send no `Sec-Fetch-Site`.
  - `/upload` is multipart, so a plain HTML form can post it cross-site.
  - Current browsers send `Sec-Fetch-Site: cross-site`, which the middleware blocks, so the practical risk is low.
- **Fix:**
  - For `/api/` writes, require `Sec-Fetch-Site ∈ {same-origin, none}` or an Origin equal to an allowlisted host, and reject `null`.
  - Also require a custom header (`X-Requested-With: amp-sync`), which cross-site forms can't send without a CORS preflight.
  - Set the Access application's cookie to `SameSite=Lax` or `Strict` in the Cloudflare dashboard.

### L7. Undo doesn't notice changes made outside the app, and can leave two versions of a plugin
- **Where:** `engine.py:943-968`.
- **Scenario:** An update replaced `X-1.jar` with `X-2.jar`. Later an admin installs `X-3.jar` through AMP's file manager and deletes `X-2.jar`. Undo "removes" `X-2` (already gone) and restores `X-1.jar`, so the server loads both X-1 and X-3.
- **Fix:** For jar entries, before restoring, check `list_plugins` for other jars with the same key that aren't in the manifest, and refuse or report a conflict. More generally, store the post-job hash of each touched path and skip restores where the current content differs (as in H1).

### L8. Missing hardening headers; API docs exposed; `href` values come from third-party data
- **Where:** `main.py:41-44` (`/api/v2/docs` loads Swagger UI from a CDN); `templates/index.html` (no CSP); `static/js/views/*.js` put `changelog_url` into `href`, and for GitHub that value is `html_url` straight from the API.
- **Fix:**
  - Add `Content-Security-Policy: default-src 'self'; script-src 'self'; object-src 'none'; frame-ancestors 'none'`. Move the inline theme script to a file, or allow it by hash.
  - Add `X-Content-Type-Options: nosniff` and `Referrer-Policy: same-origin`.
  - Turn off `docs_url` in production.
  - In the UI, render only `https:` URLs.

---

## Other notes (not rated)
- The job, plan and settings JSON files are written by the app only. `undo` validates `rel` through `resolve_in` before restoring, and store paths come from the manifest the app writes itself. No traversal was found through job records, provided `STATE_DIR` is writable only by `amp` (0700 recommended).
- `jobs.write` splits multi-line log text, so SSE `data:` lines can't inject fields.
- Jars are replaced with `os.replace` and unlinked while servers keep running. That is safe on Linux because the JVM keeps the old inode, but the change only takes effect at the next restart. Show this clearly in the UI, especially for scheduled runs (the pending-restart checklist does).
- `ProtectHome=true` together with `User=amp`: check that the `amp` home isn't where AMP keeps its instances. Here they are on `/mnt/storage_ssd`, so it's fine.
