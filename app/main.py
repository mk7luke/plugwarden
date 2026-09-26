"""LGT AMP Sync 2.0 — FastAPI app and /api/v2 routes."""
from __future__ import annotations

import asyncio
import json
import re
import secrets
import shutil
import threading
import time
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import Body, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import actions, config, engine, inventory, jobs, scheduler, settings, updates
from .inventory import PathError, UnknownServer
from .settings import SettingsError

HERE = Path(__file__).parent
@asynccontextmanager
async def lifespan(_: FastAPI):
    config.ensure_dirs()
    jobs.recover_interrupted()
    scheduler.start()
    # Warm the jar hash/metadata cache so the first page load is fast.
    threading.Thread(target=snapshot, name="warm-cache", daemon=True).start()
    yield
    scheduler.stop()
    inventory.flush_cache()


app = FastAPI(lifespan=lifespan, title=config.APP_NAME, version=config.VERSION, docs_url="/api/v2/docs", redoc_url=None,
              openapi_url="/api/v2/openapi.json")
templates = Jinja2Templates(directory=str(HERE / "templates"))
app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")


# ---------------------------------------------------------------- errors / security

@app.exception_handler(UnknownServer)
async def _unknown_server(_: Request, exc: UnknownServer):
    return JSONResponse({"detail": str(exc)}, status_code=404)


@app.exception_handler(PathError)
async def _path_error(_: Request, exc: PathError):
    return JSONResponse({"detail": str(exc)}, status_code=400)


@app.exception_handler(engine.DeployError)
async def _deploy_error(_: Request, exc: engine.DeployError):
    return JSONResponse({"detail": str(exc)}, status_code=400)


@app.exception_handler(SettingsError)
async def _settings_error(_: Request, exc: SettingsError):
    return JSONResponse({"detail": str(exc)}, status_code=400)


@app.middleware("http")
async def _same_origin_writes(request: Request, call_next):
    """Refuse cross-site state-changing requests (CSRF), since auth is a Cloudflare Access cookie."""
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.url.path.startswith("/api/"):
        if request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"detail": "cross-site request refused"}, status_code=403)
        origin = request.headers.get("origin")
        if origin and origin != "null" and urlparse(origin).netloc != request.headers.get("host"):
            return JSONResponse({"detail": "cross-origin request refused"}, status_code=403)
    return await call_next(request)


def user_of(request: Request) -> str:
    u = (request.headers.get(config.USER_HEADER) or "").strip()
    return u[:200] if u else "local"


def job_ref(job: jobs.Job) -> dict:
    return {"job_id": job.id, "status": job.status, "kind": job.kind}


# ---------------------------------------------------------------- snapshot

def snapshot() -> dict:
    """Everything the read views need: servers, their plugins with status, drift per key."""
    st = settings.load_raw()
    cache = updates.load_cache()
    servers = inventory.discover()
    plugins: dict[str, list[dict]] = {}
    versions: dict[str, set] = {}
    for srv in servers:
        rows = []
        for p in inventory.list_plugins(srv):
            status, source, latest = updates.status_for(p, srv, st, cache)
            rows.append({**p, "status": status, "source": source, "latest": updates.public_latest(latest),
                         "pinned_version": st["pins"].get(p["key"])})
            versions.setdefault(p["key"], set()).add(p["version"])
        plugins[srv.id] = rows
    inventory.flush_cache()
    drift = {k for k, v in versions.items() if len(v) > 1}
    for rows in plugins.values():
        for r in rows:
            r["drift"] = r["key"] in drift
    source = next((s for s in servers if s.id == st["default_source"]), None)
    return {"settings": st, "cache": cache, "servers": servers, "plugins": plugins, "drift": drift,
            "source": source}


def server_view(srv: inventory.Server, snap: dict) -> dict:
    rows = snap["plugins"][srv.id]
    src = snap["source"]
    src_family = src.family if src else "bukkit"
    return {
        "id": srv.id, "platform": srv.platform, "family": srv.family, "mc_version": srv.mc_version,
        "plugin_count": len(rows),
        "updates": sum(1 for r in rows if r["status"] == "outdated"),
        "drift": sum(1 for r in rows if r["drift"]),
        "unknown": sum(1 for r in rows if r["status"] == "unknown"),
        "pending_restart": actions.pending_restart(srv),
        "is_source": bool(src and src.id == srv.id),
        "eligible_target": srv.family == src_family and srv.family != "fabric" and not (src and src.id == srv.id),
        "note": "Fabric server (no plugins)" if srv.family == "fabric" else None,
    }


# ---------------------------------------------------------------- pages

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {"title": config.APP_NAME, "version": config.VERSION})


# ---------------------------------------------------------------- read API

@app.get("/api/v2/overview")
def overview(request: Request):
    snap = snapshot()
    servers = [server_view(s, snap) for s in snap["servers"]]
    keys = {r["key"] for rows in snap["plugins"].values() for r in rows}
    return {
        "servers": servers,
        "totals": {
            "servers": len(servers),
            "plugins": len(keys),
            "updates": sum(s["updates"] for s in servers),
            "update_plugins": len({r["key"] for rows in snap["plugins"].values() for r in rows
                                   if r["status"] == "outdated"}),
            "drift": len(snap["drift"]),
            "pending_restart": sum(1 for s in servers if s["pending_restart"]),
        },
        "last_check": snap["cache"].get("checked_at"),
        "auto_update": scheduler.status(),
        "active_jobs": [j.to_dict(with_log=False) for j in jobs.active_jobs()],
        "recent_jobs": [_job_summary(j) for j in jobs.list_jobs(8)],
        "user": user_of(request),
        "default_source": snap["settings"]["default_source"],
        "version": config.VERSION,
    }


@app.get("/api/v2/servers")
def servers_list():
    snap = snapshot()
    return [server_view(s, snap) for s in snap["servers"]]


@app.get("/api/v2/servers/{server_id}")
def server_detail(server_id: str):
    srv = inventory.get_server(server_id)
    snap = snapshot()
    return {**server_view(srv, snap), "plugins": snap["plugins"][srv.id]}


@app.get("/api/v2/servers/{server_id}/plugins")
def server_plugins(server_id: str):
    srv = inventory.get_server(server_id)
    return snapshot()["plugins"][srv.id]


@app.get("/api/v2/servers/{server_id}/tree")
def server_tree(server_id: str, path: str = ""):
    srv = inventory.get_server(server_id)
    return inventory.list_tree(srv, path)


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
                                             "drift": r["drift"], "latest_version": None, "cells": {},
                                             "source": None})
            if r["latest"] and not row["latest_version"]:
                row["latest_version"] = r["latest"]["version"]
            if r["source"] and not row["source"]:
                row["source"] = r["source"]
            if srv.id in row["cells"]:  # duplicate jars of one plugin on a server
                row["cells"][srv.id]["duplicates"].append(r["jar"])
                continue
            row["cells"][srv.id] = {"version": r["version"], "jar": r["jar"], "status": r["status"],
                                    "sha1": r["sha1"], "duplicates": []}
    src = snap["source"]
    if src:
        for row in rows.values():
            cell = row["cells"].get(src.id)
            row["source_version"] = cell["version"] if cell else None
    return {"servers": [s.id for s in snap["servers"]],
            "server_info": [server_view(s, snap) for s in snap["servers"]],
            "plugins": sorted(rows.values(), key=lambda r: (r["family"] != "bukkit", r["name"].lower()))}


@app.get("/api/v2/updates")
def updates_list():
    snap = snapshot()
    return updates.pending_updates(snap["servers"], snap["plugins"])


# ---------------------------------------------------------------- plugin actions

KEY_RE = settings.KEY_RE


def _check_key(key: str) -> str:
    if not KEY_RE.match(key):
        raise HTTPException(400, f"invalid plugin key: {key!r}")
    return key


@app.post("/api/v2/plugins/{key}/pin")
def plugin_pin(key: str, body: dict = Body(default={})):
    _check_key(key)
    version = body.get("version") if isinstance(body, dict) else None
    if version is not None and (not isinstance(version, str) or len(version) > 100):
        raise HTTPException(400, "version must be a string or null")

    def fn(raw):
        if version is None:
            raw["pins"].pop(key, None)
        else:
            raw["pins"][key] = version
    return settings.mutate(fn)


@app.post("/api/v2/plugins/{key}/ignore")
def plugin_ignore(key: str, body: dict = Body(default={})):
    _check_key(key)
    ignored = bool(body.get("ignored", True)) if isinstance(body, dict) else True

    def fn(raw):
        s = set(raw["ignores"])
        (s.add if ignored else s.discard)(key)
        raw["ignores"] = sorted(s)
    return settings.mutate(fn)


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


@app.post("/api/v2/updates/apply")
def updates_apply(request: Request, body: dict = Body(...)):
    items = body.get("items", "all")
    return job_ref(actions.start_apply(user_of(request), items, bool(body.get("dry_run", False))))


# ---------------------------------------------------------------- deploy

@app.post("/api/v2/deploy/plan")
def deploy_plan(body: dict = Body(...)):
    return engine.plan(body)


@app.post("/api/v2/deploy")
def deploy(request: Request, body: dict = Body(...)):
    return job_ref(actions.start_deploy(user_of(request), body, dry_run=bool(body.get("dry_run", False))))


UPLOAD_NAME_RE = re.compile(r"[^A-Za-z0-9._+\-() ]")


@app.post("/api/v2/upload")
async def upload(file: UploadFile = File(...)):
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
        except (zipfile.BadZipFile, OSError):
            raise HTTPException(400, "not a valid jar (zip) file")
        if not descs:
            raise HTTPException(400, "jar has no plugin.yml, paper-plugin.yml, velocity-plugin.json or bungee.yml")
    except BaseException:
        shutil.rmtree(d, ignore_errors=True)
        raise
    await run_in_threadpool(_prune_uploads)
    main = descs.get("bukkit") or descs.get("velocity") or descs.get("bungee")
    families = [f for f in ("bukkit", "velocity") if f in descs]
    return {
        "upload_id": upload_id, "name": name, "plugin_name": main["name"], "version": main["version"],
        "families": families,
        "keys": {f: inventory.plugin_key(f, descs[f]["id"] if f == "velocity" else descs[f]["name"])
                 for f in families},
        "sha1": await run_in_threadpool(inventory.file_hash, dest), "size": size,
    }


def _prune_uploads(max_age_days: float = 7) -> None:
    cutoff = time.time() - max_age_days * 86400
    for d in config.state("uploads").iterdir():
        if d.is_dir() and d.stat().st_mtime < cutoff:
            shutil.rmtree(d, ignore_errors=True)


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
def settings_put(body: dict = Body(...)):
    return settings.update(body)


@app.get("/api/v2/health")
def health() -> dict[str, Any]:
    return {"ok": True, "version": config.VERSION, "base_exists": config.BASE.is_dir()}
