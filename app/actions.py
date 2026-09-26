"""Job factories shared by the API and the scheduler, plus pending-restart tracking."""
from __future__ import annotations

import shutil
import threading
import time
from pathlib import Path

from . import config, engine, inventory, jobs, settings, updates
from .storage import read_json, write_json


# ---------------------------------------------------------------- pending restart

def _pending_file() -> Path:
    return config.state("pending_restart.json")


_pending_lock = threading.Lock()
_check_lock = threading.Lock()


def mark_changed(server_ids: list[str]) -> None:
    if not server_ids:
        return
    with _pending_lock:
        _mark_changed(server_ids)


def _mark_changed(server_ids: list[str]) -> None:
    data = read_json(_pending_file(), {}) or {}
    now = time.time()
    for s in server_ids:
        data[s] = now
    write_json(_pending_file(), data)


def clear_pending(server_id: str) -> None:
    with _pending_lock:
        data = read_json(_pending_file(), {}) or {}
        if data.pop(server_id, None) is not None:
            write_json(_pending_file(), data)


def _last_start(srv: inventory.Server) -> float:
    """Best-effort server start time: newest rotated log (Minecraft gzips latest.log on startup)."""
    logs = srv.root / "Minecraft" / "logs"
    try:
        return max((p.stat().st_mtime for p in logs.glob("*.log.gz")), default=0.0)
    except OSError:
        return 0.0


def pending_restart(srv: inventory.Server) -> bool:
    changed = (read_json(_pending_file(), {}) or {}).get(srv.id)
    return bool(changed) and _last_start(srv) < changed


# ---------------------------------------------------------------- job factories

def _finish(job: jobs.Job) -> None:
    inventory.flush_cache()
    if job.changed_servers:
        mark_changed(job.changed_servers)


def _prune_dir(root: Path, max_age_days: float) -> None:
    cutoff = time.time() - max_age_days * 86400
    for d in root.iterdir():
        if d.is_dir() and d.stat().st_mtime < cutoff:
            shutil.rmtree(d, ignore_errors=True)


def _mutating_body(job: jobs.Job, fn) -> None:
    """Runs while holding the mutate lock (for non-dry-run jobs)."""
    def recorded() -> None:
        if not job.has_backup:
            job.has_backup = True
            job.save()

    backup = None if job.dry_run else engine.Backup(job.id, on_record=recorded)
    ctx = engine.Ctx(dry_run=job.dry_run, job=job, backup=backup)
    try:
        fn(ctx)
    finally:
        job.has_backup = bool(backup and backup.used)
        if not job.dry_run:
            try:
                engine.prune_backups(settings.load_raw()["backup_keep_jobs"], keep_ids={job.id})
                _prune_dir(config.state("staging"), max_age_days=7)
            except OSError:
                pass


def start_check(user: str) -> jobs.Job:
    with _check_lock:
        existing = jobs.find_active("update-check")
        if existing:
            return existing
        return jobs.submit("update-check", user, {}, lambda job: updates.check(job)["summary"],
                           mutating=False, on_done=_finish)


def start_apply(user: str, items, dry_run: bool, auto: bool = False) -> jobs.Job:
    pairs = updates.select_targets(items)  # validates before the job is created
    params = {"items": items, "auto": auto}

    def body(job: jobs.Job) -> None:
        if not pairs:
            job.write("No updates to apply")
            return
        _mutating_body(job, lambda ctx: updates.apply(job, ctx, pairs, require_verified=auto))

    return jobs.submit("update-apply", user, params, body, dry_run=dry_run, on_done=_finish)


def start_deploy(user: str, body: dict, dry_run: bool = False) -> jobs.Job:
    req = engine.validate_deploy(body)
    params = {k: body.get(k) for k in ("source", "targets", "action", "items", "options", "force")}
    return jobs.submit("deploy", user, params,
                       lambda job: _mutating_body(job, lambda ctx: engine.run_deploy(ctx, req)),
                       dry_run=dry_run, on_done=_finish)


def start_remove(user: str, key: str, server_ids: list[str] | None, remove_folder: bool,
                 dry_run: bool, force: bool = False) -> jobs.Job:
    known = inventory.servers_by_id()
    if server_ids is None:
        targets = [s for s in known.values() if any(p["key"] == key for p in inventory.list_plugins(s))]
    else:
        unknown = [s for s in server_ids if s not in known]
        if unknown:
            raise engine.DeployError(f"unknown servers: {unknown}")
        targets = [known[s] for s in server_ids]
    if not targets:
        raise engine.DeployError(f"{key} is not installed on any selected server")
    params = {"key": key, "servers": [t.id for t in targets], "remove_folder": remove_folder, "force": force}

    def run(ctx: engine.Ctx) -> None:
        for t in targets:
            ctx.log(f"==> {t.id}")
            engine.remove_plugin(ctx, t, key, remove_folder, force)

    return jobs.submit("remove", user, params, lambda job: _mutating_body(job, run),
                       dry_run=dry_run, on_done=_finish)


_undo_lock = threading.Lock()
_undo_pending: set[str] = set()


def start_undo(user: str, job_id: str) -> jobs.Job:
    with _undo_lock:
        original = jobs.get(job_id)
        if original is None:
            raise engine.DeployError("unknown job")
        if not original.undoable or job_id in _undo_pending:
            raise engine.DeployError("this job cannot be undone (no backups, dry run, still running, or already undone)")
        later = engine.overlapping_later_jobs(job_id)
        if later:
            raise engine.DeployError("later jobs changed the same files; undo these first (newest first): "
                                     + ", ".join(sorted(later, reverse=True)))
        _undo_pending.add(job_id)

    def done(job: jobs.Job) -> None:
        with _undo_lock:
            _undo_pending.discard(job_id)
            if job.status == "done":
                jobs.mark_undone(job_id, job.id)
        _finish(job)

    return jobs.submit("undo", user, {"undo_of": job_id},
                       lambda job: _mutating_body(job, lambda ctx: engine.run_undo(ctx, job_id)),
                       on_done=done)
