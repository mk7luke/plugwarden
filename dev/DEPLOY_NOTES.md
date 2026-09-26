# PlugWarden 2.0: deployment notes

How PlugWarden (formerly LGT AMP Sync) is deployed (Docker Compose), configured and secured. `INSTALL.md` has the short version.

## 1. Running it: Docker Compose

PlugWarden runs as the compose service `lgt-amp-sync` (name kept for deployment continuity), like the owner's other apps. The files are in the repo root:

| File | Purpose |
|---|---|
| `Dockerfile` | `python:3.10-slim` + `rsync`, runs as uid/gid 1001 (`amp`), `HEALTHCHECK` on `GET /healthz`, uvicorn on `0.0.0.0:8078` inside the container |
| `docker-compose.yml` | production: `container_name: lgt-amp-sync`, `restart: unless-stopped`, `user: "1001:1001"`, port `127.0.0.1:8078:8078` only, volumes `/mnt/storage_ssd/ssd-live` (same path inside) and `./data:/var/lib/lgt-amp-sync`, `env_file: .env` |
| `.env.example` | the real Cloudflare Access values, the hostname and the datastore path. Copy it to `.env` |
| `docker-compose.dev.yml` | override that mounts the **sandbox** instead, publishes `127.0.0.1:8095`, runs as the sandbox owner, auth off (`LGT_AUTH=none` + `LGT_AUTH_ALLOW_INSECURE=1`) |

Why this is safe with `0.0.0.0` inside the container:
- The port is published on the host's loopback only (`127.0.0.1:8078`). `cloudflared` on the host reaches it; the LAN cannot.
- Production uses `LGT_AUTH=cf-access`, so every request needs a valid Access JWT anyway.
- `LGT_AUTH=none` on a non-loopback bind is refused at startup unless `LGT_AUTH_ALLOW_INSECURE=1`. Only the dev override sets that, and it is refused whenever `LGT_HOSTNAME` is set.
- File ownership: the container runs as `1001:1001` (`amp:amp` on this host), so files it writes into the datastore stay `amp:amp`. Verified: a container file write shows `1001:1001` on the host.

Real values (in `.env.example`, not in code):
- `LGT_CF_TEAM_DOMAIN=tech-guy.cloudflareaccess.com`
- `LGT_CF_AUD=26625e9b76430b682d8c278b1c8ad95087a23b5f5ac2d3b58ac2a527891bded4`
- `LGT_HOSTNAME=bulkupdate.obliv.us`. The tunnel ingress `bulkupdate.obliv.us` → `http://localhost:8078`, unchanged from the systemd setup.

### Testing the container against the sandbox

```bash
export LGT_SANDBOX=/path/to/scratchpad/sandbox          # contains base/ and state/
docker compose -f docker-compose.yml -f docker-compose.dev.yml -p lgt-amp-sync-dev up -d --build
curl -s http://127.0.0.1:8095/healthz
docker compose -f docker-compose.yml -f docker-compose.dev.yml -p lgt-amp-sync-dev down
```

Stop `dev/serve.sh` first, because both use port 8095. `LGT_UID`/`LGT_GID` pick the container user; the default 1000 matches the sandbox owner.

## 1b. Migrating from the systemd install

The old install: systemd unit `lgt-amp-sync`, code in `/opt/lgt-amp-sync`, state in `/var/lib/lgt-amp-sync`, listening on `127.0.0.1:8078`. The repo no longer ships the unit, `run.sh` or `run.sh.deployed`. Their settings live in `.env` now.

1. **Prepare the checkout** on the host, e.g. `/home/luke/lgt_amp_sync` or a deploy directory of your choice:
   ```bash
   cp .env.example .env && chmod 600 .env        # check the values
   docker compose build
   ```
2. **Stop the old service. Keep it installed for rollback.**
   ```bash
   sudo systemctl stop lgt-amp-sync
   sudo systemctl disable lgt-amp-sync
   ```
3. **Migrate state.** v1 state (`jobs/*.log`, `uploads/`) isn't used by v2, but copying keeps history. `settings.json`, `backups/` etc. appear on first v2 start.
   ```bash
   sudo mkdir -p data
   sudo rsync -a /var/lib/lgt-amp-sync/ ./data/
   sudo chown -R 1001:1001 data && sudo chmod 700 data
   ```
4. **Start:**
   ```bash
   docker compose up -d
   ```
5. **Verify:**
   ```bash
   docker ps --filter name=lgt-amp-sync        # (healthy)
   curl -s http://127.0.0.1:8078/healthz       # {"ok":true}
   curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8078/api/v2/overview   # 401: no Access token, as intended
   docker logs lgt-amp-sync | tail
   ```
   Then open `https://bulkupdate.obliv.us`. Cloudflare Access should log you in, and the top bar should show your email. Run **Check updates** and one **Deploy → preview (plan)** before any real change.
6. **Rollback:**
   ```bash
   docker compose down
   sudo systemctl enable --now lgt-amp-sync
   ```
   The v1 service ignores v2's state files. Nothing in `/opt` or `/var/lib/lgt-amp-sync` was modified by the migration (step 3 only copies).

## 2. Authentication: Cloudflare Access JWT

The app verifies the `Cf-Access-Jwt-Assertion` header, or the `CF_Authorization` cookie, on every request:
- the RS256 signature, checked against `https://<team>/cdn-cgi/access/certs` (cached; refreshed when a new key id appears);
- `aud` must equal the application's AUD tag;
- `iss` must be `https://<team>`;
- `exp` and `nbf` must be valid.

The user shown in the audit and activity logs is the token's `email` (or `common_name` for service tokens). The plain `Cf-Access-Authenticated-User-Email` header is ignored.

### Environment variables

| Variable | Required | Meaning |
|---|---|---|
| `LGT_AUTH` | no | `cf-access` (default when `LGT_CF_AUD` is set) or `none` (only on a loopback bind, never together with `LGT_HOSTNAME`, and it refuses requests that arrive through a proxy or tunnel) |
| `LGT_CF_TEAM_DOMAIN` | cf-access | `tech-guy.cloudflareaccess.com` (no `https://`) |
| `LGT_CF_AUD` | cf-access | the Access application's Audience (AUD) tag |
| `LGT_HOSTNAME` | yes (prod) | the public hostname (`bulkupdate.obliv.us`); added to the Host allowlist |
| `LGT_ALLOWED_HOSTS` | no | extra comma-separated hostnames that may appear in `Host:` |
| `LGT_BIND` | no | bind address the app assumes (the image sets `0.0.0.0`; outside Docker the default is `127.0.0.1`) |
| `LGT_AUTH_ALLOW_INSECURE` | no | `1` allows `LGT_AUTH=none` on a non-loopback bind (dev container only) |
| `LGT_STATE_DIR` | no | state directory (image default `/var/lib/lgt-amp-sync`, i.e. `./data`); `chmod 700`, owned by 1001 |
| `LGT_BASE_OVERRIDE` | yes (prod) | datastore base, e.g. `/mnt/storage_ssd/ssd-live` |
| `LGT_MIN_FREE_GB` | no | refuse new jobs below this much free space on the state dir or datastore (default `2`) |
| `LGT_DOCS` | no | `1` enables `/api/v2/docs` (off by default) |

### Finding the team domain and AUD tag

1. Cloudflare dashboard → **Zero Trust** → **Settings** → **Custom Pages** (or **General**). The team domain is shown as `<team>.cloudflareaccess.com`.
2. **Zero Trust** → **Access** → **Applications** → the PlugWarden (AMP Sync) application → **Overview** (or **Basic information**). Copy the **Application Audience (AUD) Tag**.
3. Check it on the host:
   ```
   curl -s https://<team>.cloudflareaccess.com/cdn-cgi/access/certs | head -c 200
   ```
   The response must list `keys`.
4. Recommended Access settings:
   - Cookie **SameSite = Lax** (or Strict).
   - **HTTP Only**.
   - Session duration as short as is practical.

## 3. Host allowlist, CSRF and headers

- Requests whose `Host` is not `localhost`, `127.0.0.1`, `LGT_HOSTNAME` or listed in `LGT_ALLOWED_HOSTS` get `400`. This blocks DNS rebinding.
- State-changing `/api/` requests must meet all of these:
  - send `X-Requested-With: lgt-amp-sync` (the frontend does);
  - have no cross-site `Sec-Fetch-Site`;
  - have an `Origin` (if present) that is an allowed host and not `null`.
- Responses carry `X-Content-Type-Options: nosniff` and `Referrer-Policy: same-origin`.
- The app page gets a strict CSP: `default-src 'self'`, and `script-src 'self'` plus the SHA-256 of each inline script, computed per response.
- API responses carry `frame-ancestors 'none'; object-src 'none'`.
- The JWKS cache keeps serving cached keys during a Cloudflare outage and retries once a minute.
- Uploads over 200 MB are refused from `Content-Length` before the body is parsed.

## 4. Data safety and retention

- **Folder pushes:** a push backs up and undoes only the files rsync actually changed, created or deleted. Live data (databases, userdata, logs, `*-storage`, UUID-named player files) is never copied, deleted, backed up or restored unless `include_data` is set.
- **Undo:** undo restores a path only if it still has the state the job left. Anything changed since is reported as `changed_since_job` and left alone.
- **Retention** (Settings):
  - Backups are kept up to `backup_keep_jobs` (100), `backup_max_age_days` (30) and `backup_max_gb` (5). The newest backup is always kept.
  - Uploads and download staging are removed after 24 h.
  - The newest 500 job records are kept.
  - Housekeeping runs hourly and after each job.
- **Free space:** new jobs are refused with HTTP 507 when free space on the state dir or the datastore drops below `LGT_MIN_FREE_GB`.

## 5. Auto-update policy (Settings → auto_update)

- **Mode:** `off` / `notify` / `apply`. Switching to `apply` needs `confirm_apply: true` and is written to the audit log.
- **What `apply` installs:** only verified release builds that are at least `min_release_age_hours` old (default 48), from a Modrinth hash match or from a manual source marked `auto_apply`.
- **Canary:** updates go to `canary_server` (default: the default source) first. Other servers get the same version once the canary has run it for `canary_soak_hours` (24) and has restarted since.
- **Limits:**
  - At most `max_changes_per_run` (20) changes per run.
  - Nothing is applied outside the maintenance window.
- **Visibility:** `/api/v2/overview` → `auto_update` shows the policy, the canary soak state and the last selection with the reason each update is waiting.

## 6. Audit log

`GET /api/v2/access-log` (Activity → Access) lists:
- config value reads: `diff`, `plan-values`;
- changes: `settings`, `pin`, `ignore`, `upload`, with before/after values.

It is stored in `STATE_DIR/access.log` (JSON lines, rotated at 5 MB).

## 7. Housekeeping before deploying

- `app.stale-2025-12-14/` in the repo root is an old copy. Do not deploy it alongside `app/`.
- `docker compose build` installs everything from `requirements.txt`, including `PyJWT[crypto]`.
- `/healthz` is the only unauthenticated endpoint. It returns `{"ok":true}` and nothing else.
