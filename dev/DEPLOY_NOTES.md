# LGT AMP Sync 2.0: deployment notes

This covers what changed for running the service after the security round. `INSTALL.md` still describes the basic install.

## 1. Network: bind to loopback

- The app listens on `127.0.0.1:8078` by default (`LGT_BIND`, used by `run.sh` / `run.sh.deployed`).
- `cloudflared` on the same host reaches it. Nothing on the LAN can.
- If `cloudflared` runs on another machine:
  - Set `LGT_BIND` to the interface that machine connects to.
  - Firewall 8078 so that only the tunnel host can reach it.
  - Keep `LGT_AUTH=cf-access`. The app refuses to start unauthenticated on a non-loopback bind.

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
| `LGT_CF_TEAM_DOMAIN` | cf-access | e.g. `yourteam.cloudflareaccess.com` (no `https://`) |
| `LGT_CF_AUD` | cf-access | the Access application's Audience (AUD) tag |
| `LGT_HOSTNAME` | yes (prod) | the public hostname, e.g. `amp-sync.example.com`; added to the Host allowlist |
| `LGT_ALLOWED_HOSTS` | no | extra comma-separated hostnames that may appear in `Host:` |
| `LGT_BIND` | no | bind address (default `127.0.0.1`) |
| `LGT_PORT` | no | port for `run.sh` (default `8078`) |
| `LGT_STATE_DIR` | no | state directory (default `/var/lib/lgt-amp-sync`); make it `chmod 700`, owned by `amp` |
| `LGT_BASE_OVERRIDE` | yes (prod) | datastore base, e.g. `/mnt/storage_ssd/ssd-live` |
| `LGT_MIN_FREE_GB` | no | refuse new jobs below this much free space on the state dir or datastore (default `2`) |
| `LGT_DOCS` | no | `1` enables `/api/v2/docs` (off by default) |

### Finding the team domain and AUD tag

1. Cloudflare dashboard → **Zero Trust** → **Settings** → **Custom Pages** (or **General**). The team domain is shown as `<team>.cloudflareaccess.com`.
2. **Zero Trust** → **Access** → **Applications** → the AMP Sync application → **Overview** (or **Basic information**). Copy the **Application Audience (AUD) Tag**.
3. Check it on the host:
   ```
   curl -s https://<team>.cloudflareaccess.com/cdn-cgi/access/certs | head -c 200
   ```
   The response must list `keys`.
4. Recommended Access settings:
   - Cookie **SameSite = Lax** (or Strict).
   - **HTTP Only**.
   - Session duration as short as is practical.

### Example systemd environment

See `lgt-amp-sync.service`.

```
Environment=LGT_BIND=127.0.0.1
Environment=LGT_AUTH=cf-access
Environment=LGT_CF_TEAM_DOMAIN=yourteam.cloudflareaccess.com
Environment=LGT_CF_AUD=<aud tag>
Environment=LGT_HOSTNAME=amp-sync.example.com
```

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
- Install `PyJWT[crypto]` (in `requirements.txt`).
