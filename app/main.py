"""PlugWarden 2.0 — FastAPI app and /api/v2 routes."""
from __future__ import annotations

import asyncio
import functools
import json
import re
import secrets
import shutil
import threading
import time
import zipfile
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import (actions, audit, auth, config, configmerge, engine, health, inventory, jobs, plans, scheduler,
               settings, stats, updates, visits)
from .inventory import PathError, UnknownServer
from .settings import SettingsError

HERE = Path(__file__).parent
_bg_stop = threading.Event()


@asynccontextmanager
async def lifespan(_: FastAPI):
    auth.check_startup()
    config.ensure_dirs()
    jobs.recover_interrupted()
    jobs.backfill_undo_failures()
    scheduler.start()
    health.start_refresher(_bg_stop)
    # Index (hash + read) every jar in the background; read endpoints answer from the cache meanwhile.
    threading.Thread(target=inventory.index_all, name="indexer", daemon=True).start()
    yield
    _bg_stop.set()
    scheduler.stop()
    inventory.flush_cache()


app = FastAPI(lifespan=lifespan, title=config.APP_NAME, version=config.VERSION, redoc_url=None,
              docs_url="/api/v2/docs" if config.DOCS_ENABLED else None,
              openapi_url="/api/v2/openapi.json" if config.DOCS_ENABLED else None)
templates = Jinja2Templates(directory=str(HERE / "templates"))
app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")


# ---------------------------------------------------------------- errors / security

@app.exception_handler(UnknownServer)
async def _unknown_server(_: Request, exc: UnknownServer):
    return JSONResponse({"detail": str(exc)}, status_code=404)


@app.exception_handler(inventory.NotFound)
async def _not_found(_: Request, exc: inventory.NotFound):
    return JSONResponse({"detail": str(exc)}, status_code=404)


@app.exception_handler(jobs.InsufficientSpace)
async def _no_space(_: Request, exc: jobs.InsufficientSpace):
    return JSONResponse({"detail": str(exc), "code": "insufficient_space"}, status_code=507)


@app.exception_handler(PathError)
async def _path_error(_: Request, exc: PathError):
    return JSONResponse({"detail": str(exc)}, status_code=400)


@app.exception_handler(engine.DeployError)
async def _deploy_error(_: Request, exc: engine.DeployError):
    return JSONResponse({"detail": str(exc)}, status_code=exc.status)


@app.exception_handler(plans.PlanError)
async def _plan_error(_: Request, exc: plans.PlanError):
    return JSONResponse({"detail": exc.detail}, status_code=exc.status)


@app.exception_handler(SettingsError)
async def _settings_error(_: Request, exc: SettingsError):
    if exc.fields:  # validation: per-field messages for inline errors
        return JSONResponse({"detail": {"message": str(exc), "fields": exc.fields}}, status_code=422)
    return JSONResponse({"detail": str(exc)}, status_code=400)


@app.middleware("http")
async def _security(request: Request, call_next):
    """Host allowlist (DNS rebinding) → authentication → CSRF → upload size, then hardening headers."""
    if request.url.path == "/healthz" and request.method == "GET":
        return JSONResponse({"ok": True})  # container liveness probe: no auth, no data
    if not auth.host_ok(request.headers.get("host")):
        return JSONResponse({"detail": "host not allowed"}, status_code=400)
    try:
        client = request.client.host if request.client else None
        request.state.user = await run_in_threadpool(auth.authenticate, request.headers, request.cookies, client)
    except auth.AuthError as e:
        return JSONResponse({"detail": e.detail}, status_code=e.status)
    err = auth.csrf_ok(request.method, request.url.path, request.headers)
    if err:
        return JSONResponse({"detail": err}, status_code=403)
    if request.url.path == "/api/v2/upload" and request.method == "POST":
        try:
            length = int(request.headers.get("content-length", ""))
        except ValueError:
            return JSONResponse({"detail": "Content-Length required"}, status_code=411)
        if length > config.MAX_UPLOAD_BYTES + 1024 * 1024:
            return JSONResponse({"detail": "file too large"}, status_code=413)
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Content-Security-Policy", "object-src 'none'; frame-ancestors 'none'; base-uri 'self'")
    # Everything sits behind login: never let a shared cache (Cloudflare) keep a copy, and make browsers
    # revalidate the page and its ES modules on every load (304 via ETag) so a redeploy shows up at once.
    path = request.url.path
    response.headers.setdefault("Cache-Control", "no-store" if path.startswith("/api/") else "no-cache, private")
    return response


def user_of(request: Request) -> str:
    """The authenticated identity (verified Cloudflare Access email, or "local" in loopback dev mode)."""
    return getattr(request.state, "user", None) or "local"


def job_ref(job: jobs.Job) -> dict:
    return {"job_id": job.id, "status": job.status, "kind": job.kind}


# ---------------------------------------------------------------- snapshot

DRIFT_BASIS = "majority"


def snapshot() -> dict:
    """Everything the read views need: servers, their plugins with status, drift, update counts.

    Drift basis ("majority"): for each plugin key installed on 2+ servers, the expected version is the
    one most servers run (ties go to the default source's version, else the highest). A server
    drifts on a plugin when its version differs from that expected version."""
    st = settings.load_raw()
    cache = updates.load_cache()
    servers = inventory.discover()
    source = next((s for s in servers if s.id == st["default_source"]), None)
    plugins: dict[str, list[dict]] = {}
    # While the startup indexer hashes jars, answer from the cache instead of hashing in the request.
    cached_only = inventory.indexing_state() is not None
    per_key: dict[str, dict[str, str]] = {}  # key -> {server: version}
    pinned_at: dict[str, set[str]] = {}      # key -> servers where a pin applies
    for srv in servers:
        rows = []
        for p in inventory.list_plugins(srv, cached_only=cached_only):
            status, src, latest = updates.status_for(p, srv, st, cache)
            pin, ign = st["pins"].get(p["key"]), st["ignores"].get(p["key"])
            pub = updates.public_latest(latest)
            if pub:
                pub["compat"] = updates.compat_for(pub["compat"], srv)
            rows.append({**p, "status": status, "source": src, "latest": pub,
                         "current_compat": updates.current_compat(p, srv, cache),
                         "pinned_version": pin["version"] if settings.hold_applies(pin, srv.id) else None,
                         "pin_scope": pin["servers"] if pin else None,
                         "ignore_scope": ign["servers"] if ign else None})
            per_key.setdefault(p["key"], {}).setdefault(srv.id, p["version"])
            if settings.hold_applies(pin, srv.id):
                pinned_at.setdefault(p["key"], set()).add(srv.id)
        plugins[srv.id] = rows
    inventory.flush_cache()
    # Pinned installs are deliberate: they neither vote for the expected version nor count as drift.
    voters = {k: ({s: ver for s, ver in v.items() if s not in pinned_at.get(k, set())} or v)
              for k, v in per_key.items()}
    expected = {k: _expected_version(voters[k], source) for k, v in per_key.items() if len(v) > 1}
    for rows in plugins.values():
        for r in rows:
            exp = expected.get(r["key"])
            differs = exp is not None and r["version"] != exp
            pinned = r["pinned_version"] is not None
            r["expected_version"] = exp
            r["drift"] = differs and not pinned
            r["drift_pinned"] = differs and pinned
    drift_keys = {k for k in expected
                  if any(ver != expected[k] for s, ver in per_key[k].items() if s not in pinned_at.get(k, set()))}
    for rows in plugins.values():
        for r in rows:
            r["versions_differ"] = r["key"] in drift_keys
            r["installed_on"] = list(per_key[r["key"]])
    pending = updates.pending_updates(servers, plugins)
    return {"settings": st, "cache": cache, "servers": servers, "plugins": plugins, "drift": drift_keys,
            "expected": expected, "source": source, "pending": pending, "counts": updates.update_counts(pending)}


def _expected_version(by_server: dict[str, str], source: inventory.Server | None) -> str | None:
    counts: dict[str, int] = {}
    for v in by_server.values():
        counts[v] = counts.get(v, 0) + 1
    top = max(counts.values())
    tied = [v for v, n in counts.items() if n == top]
    if len(tied) == 1:
        return tied[0]
    if source and source.id in by_server and by_server[source.id] in tied:
        return by_server[source.id]
    return sorted(tied, key=functools.cmp_to_key(lambda a, b: updates.compare_versions(a, b) or 0))[-1]


def server_view(srv: inventory.Server, snap: dict) -> dict:
    rows = snap["plugins"][srv.id]
    src = snap["source"]
    src_family = src.family if src else "bukkit"
    seen: set[str] = set()
    drift_plugins, drift_pinned = [], []
    for r in rows:
        if (r["drift"] or r["drift_pinned"]) and r["key"] not in seen:
            seen.add(r["key"])
            (drift_plugins if r["drift"] else drift_pinned).append(
                {"key": r["key"], "name": r["name"], "version": r["version"], "expected": r["expected_version"]})
    return {
        "id": srv.id, "platform": srv.platform, "family": srv.family, "mc_version": srv.mc_version,
        "plugin_count": len(rows),
        "updates": sum(1 for u in snap["pending"] if srv.id in u["servers"]),
        "drift": len(drift_plugins), "drift_plugins": drift_plugins, "drift_basis": DRIFT_BASIS,
        "drift_pinned": drift_pinned,
        "unknown": sum(1 for r in rows if r["status"] == "unknown"),
        "pending_restart": actions.pending_restart(srv),
        "is_source": bool(src and src.id == srv.id),
        "eligible_target": srv.family == src_family and srv.family != "fabric" and not (src and src.id == srv.id),
        "note": "Fabric server (no plugins)" if srv.family == "fabric" else None,
        "startup": health.startup_summary(srv),  # cached plugin-start summary of the latest start
    }


# ---------------------------------------------------------------- pages

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    resp = templates.TemplateResponse(request, "index.html", {"title": config.APP_NAME, "version": config.VERSION})
    # Strict CSP for the app shell: only our own scripts plus the page's exact inline scripts (by hash).
    hashes = " ".join(f"'sha256-{h}'" for h in _inline_script_hashes(resp.body))
    resp.headers["Content-Security-Policy"] = (
        f"default-src 'self'; script-src 'self' {hashes}; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; font-src 'self'; connect-src 'self'; object-src 'none'; "
        "frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
    return resp


def _inline_script_hashes(html: bytes) -> list[str]:
    import base64
    import hashlib
    out = []
    for m in re.finditer(rb"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S | re.I):
        out.append(base64.b64encode(hashlib.sha256(m.group(1)).digest()).decode())
    return out


# ---------------------------------------------------------------- read API

@app.get("/api/v2/overview")
def overview(request: Request):
    snap = snapshot()
    servers = [server_view(s, snap) for s in snap["servers"]]
    keys = {r["key"] for rows in snap["plugins"].values() for r in rows}
    checklist = actions.restart_checklist(snap["servers"])
    return {
        "servers": servers,
        "totals": {
            "servers": len(servers),
            "plugins": len(keys),
            "updates": snap["counts"],
            "drift": len(snap["drift"]),
            "drifted_servers": sum(1 for s in servers if s["drift"]),
            "pending_restart": len(checklist),
        },
        "drift_basis": DRIFT_BASIS,
        "restart_checklist": checklist,
        "last_check": snap["cache"].get("checked_at"),
        "check_summary": _check_summary(snap),
        "auto_update": scheduler.status(),
        "active_jobs": [j.to_dict(with_log=False) for j in jobs.active_jobs()],
        "recent_jobs": [_job_summary(j) for j in jobs.list_jobs(8)],
        "user": user_of(request),
        "default_source": snap["settings"]["default_source"],
        "version": config.VERSION,
        "indexing": inventory.indexing_state(),
        "since_last_visit": visits.since_last_visit(user_of(request), snap["pending"]),
    }


@app.get("/api/v2/stats")
def stats_view():
    """Network statistics (inventory, freshness, history, lag, health, trend, footprint). Cached, no network."""
    return stats.get(snapshot)


@app.post("/api/v2/seen")
def seen(request: Request, body: dict = Body(default={})):
    """Advance the caller's "last looked" time (to body.at if given, else now; never backwards)."""
    at = (body or {}).get("at")
    if at is not None and not isinstance(at, str):
        raise HTTPException(400, "at must be an ISO timestamp")
    return {"at": visits.mark_seen(user_of(request), at)}


@app.get("/api/v2/servers")
def servers_list():
    snap = snapshot()
    return [server_view(s, snap) for s in snap["servers"]]


@app.get("/api/v2/servers/{server_id}")
def server_detail(server_id: str):
    srv = inventory.get_server(server_id)
    snap = snapshot()
    return {**server_view(srv, snap), "plugins": snap["plugins"][srv.id], "indexing": inventory.indexing_state()}


@app.get("/api/v2/servers/{server_id}/plugins")
def server_plugins(server_id: str):
    srv = inventory.get_server(server_id)
    return snapshot()["plugins"][srv.id]


@app.get("/api/v2/servers/{server_id}/tree")
def server_tree(server_id: str, path: str = ""):
    srv = inventory.get_server(server_id)
    return inventory.list_tree(srv, path)


@app.get("/api/v2/servers/{server_id}/health")
def server_health(server_id: str, since: str | None = None):
    """Plugin enable report from the server's logs (latest run, or every run since `since`:
    ISO time or epoch seconds)."""
    srv = inventory.get_server(server_id)
    ts = None
    if since:
        try:
            ts = float(since)
        except ValueError:
            try:
                ts = datetime.fromisoformat(since.replace("Z", "+00:00")).timestamp()
            except ValueError:
                raise HTTPException(400, "since must be an ISO timestamp or epoch seconds")
    return health.server_report(srv, ts)


@app.post("/api/v2/servers/{server_id}/restarted")
def server_restarted(server_id: str):
    srv = inventory.get_server(server_id)
    actions.clear_pending(srv.id)
    return {"ok": True}


@app.get("/api/v2/matrix")
def matrix():
    snap = snapshot()
    rows: dict[str, dict] = {}
    for srv in snap["servers"]:
        for r in snap["plugins"][srv.id]:
            row = rows.setdefault(r["key"], {"key": r["key"], "name": r["name"], "family": srv.family,
                                             "drift": r["versions_differ"], "expected_version": r["expected_version"],
                                             "latest_version": None, "cells": {}, "source": None})
            if r["latest"] and not row["latest_version"]:
                row["latest_version"] = r["latest"]["version"]
            if r["source"] and not row["source"]:
                row["source"] = r["source"]
            if srv.id in row["cells"]:  # duplicate jars of one plugin on a server
                cell = row["cells"][srv.id]
                cell["duplicates"].append(r["jar"])
                if r["status"] == "outdated":
                    cell["status"] = "outdated"  # consistent with update counts
                continue
            row["cells"][srv.id] = {"version": r["version"], "jar": r["jar"], "status": r["status"],
                                    "sha1": r["sha1"], "duplicates": [], "drift": r["drift"],
                                    "drift_pinned": r["drift_pinned"]}
    src = snap["source"]
    if src:
        for row in rows.values():
            cell = row["cells"].get(src.id)
            row["source_version"] = cell["version"] if cell else None
    return {"servers": [s.id for s in snap["servers"]], "counts": snap["counts"], "drift_basis": DRIFT_BASIS,
            "indexing": inventory.indexing_state(),
            "server_info": [server_view(s, snap) for s in snap["servers"]],
            "plugins": sorted(rows.values(), key=lambda r: (r["family"] != "bukkit", r["name"].lower()))}


@app.get("/api/v2/updates")
def updates_list():
    snap = snapshot()
    for u in snap["pending"]:
        u["canary_health"] = scheduler.canary_health_for(u["key"], u["to_version"])
    return {"updates": snap["pending"], "counts": snap["counts"], "last_check": snap["cache"].get("checked_at"),
            "indexing": inventory.indexing_state(),
            "check_summary": _check_summary(snap)}


def _check_summary(snap: dict) -> str | None:
    stats = snap["cache"].get("stats")
    if not stats:
        return None
    if "plugins" in stats:
        return updates.check_summary(stats["plugins"], stats["plugins_identified"], snap["counts"], stats["errors"])
    return updates.check_summary(stats["jars"], stats["identified"], snap["counts"], stats["errors"], unit="jars")


# ---------------------------------------------------------------- plugin actions

KEY_RE = settings.KEY_RE


def _check_key(key: str) -> str:
    if not KEY_RE.match(key):
        raise HTTPException(400, f"invalid plugin key: {key!r}")
    return key


class _AuditAfter:
    """Records the before/after of one plugin's pin or ignore once the settings call returned."""

    def __init__(self, user: str, action: str, before: dict, key: str):
        self.user, self.action, self.before, self.key = user, action, before, key

    def __call__(self, result: dict) -> dict:
        field = "pins" if self.action == "pin" else "ignores"
        after = settings.load_raw()
        audit.record(self.user, self.action, servers=[], path=self.key,
                     detail=f"{field}[{self.key}] changed", before=self.before[field].get(self.key),
                     after=after[field].get(self.key))
        return result


def _scope_arg(body: dict) -> Any:
    servers = body.get("servers", "*")
    if servers == "*":
        return "*"
    known = {s.id for s in inventory.discover()}
    if not isinstance(servers, list) or not servers or not all(isinstance(x, str) and x in known for x in servers):
        raise HTTPException(400, "servers must be '*' or a non-empty list of known server ids")
    return servers


@app.post("/api/v2/plugins/{key}/pin")
def plugin_pin(key: str, request: Request, body: dict = Body(default={})):
    """{version: str|null, servers: [ids]|"*"}. null unpins on those servers ('*' = everywhere)."""
    _check_key(key)
    body = body if isinstance(body, dict) else {}
    version = body.get("version")
    if version is not None and (not isinstance(version, str) or len(version) > 100):
        raise HTTPException(400, "version must be a string or null")
    scope = _scope_arg(body)
    all_ids = [s.id for s in inventory.discover()]
    before = settings.load_raw()
    audit_after = _AuditAfter(user_of(request), "pin", before, key)
    return audit_after(settings.mutate(lambda raw: settings.set_hold(raw["pins"], key, scope, version is not None,
                                                         version=version, all_ids=all_ids)))


@app.post("/api/v2/plugins/{key}/ignore")
def plugin_ignore(key: str, request: Request, body: dict = Body(default={})):
    """{ignored: bool, servers: [ids]|"*"}."""
    _check_key(key)
    body = body if isinstance(body, dict) else {}
    scope = _scope_arg(body)
    all_ids = [s.id for s in inventory.discover()]
    before = settings.load_raw()
    audit_after = _AuditAfter(user_of(request), "ignore", before, key)
    return audit_after(settings.mutate(lambda raw: settings.set_hold(raw["ignores"], key, scope, bool(body.get("ignored", True)),
                                                         all_ids=all_ids)))


@app.post("/api/v2/plugins/{key}/remove")
def plugin_remove(key: str, request: Request, body: dict = Body(default={})):
    _check_key(key)
    servers = body.get("servers")
    if servers is not None and (not isinstance(servers, list) or not all(isinstance(s, str) for s in servers)):
        raise HTTPException(400, "servers must be a list of ids")
    job = actions.start_remove(user_of(request), key, servers, bool(body.get("remove_folder", False)),
                               bool(body.get("dry_run", False)), force=body.get("force") is True)
    return job_ref(job)


# ---------------------------------------------------------------- updates

@app.post("/api/v2/updates/check")
def updates_check(request: Request):
    return job_ref(actions.start_check(user_of(request)))


@app.post("/api/v2/updates/plan")
def updates_plan(request: Request, body: dict = Body(...)):
    """{items: "all" | [{key, servers?}]} → a stored changeset of exact jar swaps (expires in 30 min)."""
    return updates.create_plan(body.get("items", "all"), user_of(request))


@app.post("/api/v2/updates/apply")
def updates_apply(request: Request, body: dict = Body(...)):
    """{plan_id, exclude?: [[server, key]], dry_run?} → job applying exactly that plan. 409 if stale."""
    return job_ref(actions.start_apply(user_of(request), body.get("plan_id"), body.get("exclude"),
                                       dry_run=bool(body.get("dry_run", False))))


# ---------------------------------------------------------------- deploy

@app.post("/api/v2/deploy/plan")
def deploy_plan(request: Request, body: dict = Body(...)):
    out = engine.plan(body, user_of(request))
    shown = [w for w in out["warnings"] if w.get("type") == "server_specific"]
    if shown:  # plan warnings carry (redacted) config values: they are a read like /diff
        audit.record(user_of(request), "plan-values", servers=sorted({w["server"] for w in shown}),
                     path=", ".join(sorted({w["path"] for w in shown}))[:500],
                     detail=f"{sum(len(w['keys']) for w in shown)} server-specific value(s) shown")
    return out


@app.post("/api/v2/deploy")
def deploy(request: Request, body: dict = Body(...)):
    """{plan_id} → job applying exactly the previewed plan. 409 if targets changed since the preview."""
    return job_ref(actions.start_deploy(user_of(request), body.get("plan_id"),
                                        skip_failing=body.get("skip_failing") is True))


# ---------------------------------------------------------------- search / diff

@app.get("/api/v2/servers/{server_id}/search")
def server_search(server_id: str, q: str = Query(..., min_length=2, max_length=100),
                  limit: int = Query(200, ge=1, le=200)):
    srv = inventory.get_server(server_id)
    return inventory.search(srv, q, limit)


@app.get("/api/v2/diff")
def diff(request: Request, source: str, target: str, path: str, preserve_keys: str | None = None):
    """Secret-looking values are redacted on both sides; every read is written to the access log.

    preserve_keys=server_specific (or a comma-separated key list) diffs the merged file that a
    push with that option would write, instead of the raw source file."""
    src, tgt = inventory.get_server(source), inventory.get_server(target)
    transform = None
    if preserve_keys:
        if len(preserve_keys) > 2000:
            raise HTTPException(400, "preserve_keys too long")

        def transform(src_lines, tgt_lines, out):
            rel = out["path"]
            if not configmerge.is_config(rel):
                out["merge_error"] = "not a config file"
                return src_lines
            if preserve_keys == "server_specific":
                peers = [s for s in inventory.discover()
                         if s.family == src.family and s.id not in (src.id, tgt.id)]
                others = [configmerge.parse(rel, ln) for s in peers
                          if (ln := configmerge.read_lines(s.plugins_dir / rel)) is not None]
                keep = [k["key"] for k in configmerge.server_specific(
                    rel, configmerge.parse(rel, src_lines), configmerge.parse(rel, tgt_lines), others)]
            else:
                keep = [k.strip() for k in preserve_keys.split(",") if k.strip()]
            try:
                merged, kept = configmerge.merge(rel, src_lines, tgt_lines, keep)
            except configmerge.MergeUnsafe as e:
                out["merge_error"] = str(e)  # the push would refuse this file (merge_unsafe)
                return src_lines
            out["kept_keys"] = kept
            return merged

    out = inventory.diff_file(src, tgt, path, transform)
    if preserve_keys:
        out["preserve_keys"] = preserve_keys
        out.setdefault("kept_keys", [])
        out.setdefault("merge_error", None)
    audit.record(user_of(request), "diff", servers=[src.id, tgt.id], path=out["path"],
                 detail=f"{len(out['redacted'])} value(s) redacted")
    return out


@app.get("/api/v2/access-log")
def access_log(limit: int = Query(200, ge=1, le=2000), user: str | None = None, server: str | None = None,
               path: str | None = None, action: str | None = None, before: str | None = Query(None, max_length=200),
               changes_only: bool = False):
    """Who read or changed what (newest first). Filters are case-insensitive substring matches. Paging: pass
    the previous page's `next_before` as `before`; `changes_only` hides reads (diff views, plan values)."""
    return audit.page(limit=limit, before=before, changes_only=changes_only,
                      user=user, server=server, path=path, action=action)


UPLOAD_NAME_RE = re.compile(r"[^A-Za-z0-9._+\-() ]")


@app.post("/api/v2/upload")
async def upload(request: Request, file: UploadFile = File(...)):
    name = UPLOAD_NAME_RE.sub("_", Path(file.filename or "").name).strip().lstrip(".")
    if not name.lower().endswith(".jar") or len(name) > 150:
        raise HTTPException(400, "only .jar files can be uploaded")
    upload_id = secrets.token_hex(8)
    d = config.state("uploads", upload_id)
    d.mkdir(parents=True)
    dest = d / name
    size = 0
    try:
        with open(dest, "wb") as f:
            while chunk := await file.read(1 << 20):
                size += len(chunk)
                if size > config.MAX_UPLOAD_BYTES:
                    raise HTTPException(413, "file too large")
                f.write(chunk)
        try:
            descs = await run_in_threadpool(inventory.read_descriptors, dest)
        except Exception:  # noqa: BLE001 - any unreadable/hostile archive is a client error
            raise HTTPException(400, "not a valid jar (zip) file")
        if not descs:
            raise HTTPException(400, "jar has no plugin.yml, paper-plugin.yml, velocity-plugin.json or bungee.yml")
    except BaseException:
        shutil.rmtree(d, ignore_errors=True)
        raise

    main = descs.get("bukkit") or descs.get("velocity") or descs.get("bungee")
    families = [f for f in ("bukkit", "velocity") if f in descs]
    sha1 = await run_in_threadpool(inventory.file_hash, dest)
    audit.record(user_of(request), "upload", servers=[], path=name,
                 detail=f"{main['name']} {main['version']} · sha1 {sha1} · {size} bytes")
    return {
        "upload_id": upload_id, "name": name, "plugin_name": main["name"], "version": main["version"],
        "families": families,
        "keys": {f: inventory.plugin_key(f, descs[f]["id"] if f == "velocity" else descs[f]["name"])
                 for f in families},
        "sha1": sha1, "size": size,
    }


# ---------------------------------------------------------------- jobs

def _job_summary(d: dict) -> dict:
    counts: dict[str, int] = {}
    for r in d.get("results") or []:
        counts[r["outcome"]] = counts.get(r["outcome"], 0) + 1
    servers = sorted({r["server"] for r in d.get("results") or []})
    out = {k: v for k, v in d.items() if k not in ("results", "log", "params")}
    out.update({"counts": counts, "servers": servers, "params": d.get("params")})
    return out


def _get_job(job_id: str) -> jobs.Job:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "unknown job")
    return job


@app.get("/api/v2/jobs")
def jobs_list(limit: int = Query(100, ge=1, le=1000)):
    return [_job_summary(d) for d in jobs.list_jobs(limit)]


@app.get("/api/v2/jobs/{job_id}")
def job_detail(job_id: str):
    return _get_job(job_id).to_dict()


@app.get("/api/v2/jobs/{job_id}/stream")
async def job_stream(job_id: str, request: Request):
    job = _get_job(job_id)

    async def gen():
        idx = 0
        yield "retry: 3000\n\n"
        idle = 0.0
        while True:
            if await request.is_disconnected():
                return
            lines = job.log[idx:]
            for ln in lines:
                yield f"data: {ln}\n\n"
            idx += len(lines)
            if job.status not in jobs.ACTIVE and idx >= len(job.log):
                yield f"event: end\ndata: {json.dumps({'status': job.status, 'summary': job.summary})}\n\n"
                return
            idle = 0.0 if lines else idle + 0.4
            if idle >= 10:
                idle = 0.0
                yield ": ping\n\n"
            await asyncio.sleep(0.4)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/v2/jobs/{job_id}/undo")
def job_undo(job_id: str, request: Request):
    _get_job(job_id)
    return job_ref(actions.start_undo(user_of(request), job_id))


# ---------------------------------------------------------------- settings

@app.get("/api/v2/settings")
def settings_get():
    return settings.load()


@app.put("/api/v2/settings")
def settings_put(request: Request, body: dict = Body(...)):
    """Partial update. Switching auto_update.mode to "apply" requires confirm_apply: true."""
    before = settings.load_raw()
    au = body.get("auto_update") if isinstance(body, dict) else None
    if isinstance(au, dict) and au.get("mode") == "apply" and before["auto_update"]["mode"] != "apply" \
            and body.get("confirm_apply") is not True:
        raise HTTPException(400, "switching auto-update to 'apply' installs updates unattended; "
                                 "send confirm_apply: true to confirm")
    out = settings.update({k: v for k, v in body.items() if k != "confirm_apply"})
    _audit_settings(user_of(request), "settings", before, settings.load_raw())
    return out


def _audit_settings(user: str, action: str, before: dict, after: dict, path: str = "") -> None:
    changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    if not changed:
        return
    audit.record(user, action, servers=[], path=path or ", ".join(changed),
                 detail="changed " + ", ".join(changed),
                 before={k: before.get(k) for k in changed}, after={k: after.get(k) for k in changed})


@app.get("/api/v2/health")
def api_health() -> dict[str, Any]:
    return {"ok": True, "version": config.VERSION, "base_exists": config.BASE.is_dir()}
