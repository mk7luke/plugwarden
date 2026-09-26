"""Job factories shared by the API and the scheduler, plus pending-restart tracking."""
from __future__ import annotations

import shutil
import threading
import time
from pathlib import Path

from . import config, engine, inventory, jobs, plans, settings, updates
from .storage import read_json, write_json


# ---------------------------------------------------------------- pending restart

def _pending_file() -> Path:
    return config.state("pending_restart.json")


_pending_lock = threading.Lock()
_check_lock = threading.Lock()


def mark_changed(job: jobs.Job) -> None:
    if not job.changed_servers or job.dry_run:
        return
    with _pending_lock:
        _mark_changed(job)


def _load_pending() -> dict:
    data = read_json(_pending_file(), {}) or {}
    # Legacy format: {server: timestamp}
    return {s: (v if isinstance(v, dict) else {"since": v, "changed": v, "jobs": []}) for s, v in data.items()}


def _mark_changed(job: jobs.Job) -> None:
    data = _load_pending()
    now = time.time()
    known = inventory.servers_by_id()
    for s in job.changed_servers:
        old = data.get(s)
        if old and s in known and _last_start(known[s]) >= old["changed"]:
            data.pop(s)  # restarted since: start a fresh entry
        entry = data.setdefault(s, {"since": now, "changed": now, "jobs": []})
        entry["changed"] = now
        items = [r["item"] for r in job.results if r["server"] == s and r["outcome"] == "changed"]
        entry["jobs"] = [j for j in entry["jobs"] if j["job_id"] != job.id] + [
            {"job_id": job.id, "kind": job.kind, "user": job.user, "at": now, "items": items[:50]}]
    write_json(_pending_file(), data)


def clear_pending(server_id: str) -> None:
    with _pending_lock:
        data = _load_pending()
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
    entry = _load_pending().get(srv.id)
    return bool(entry) and _last_start(srv) < entry["changed"]


def restart_checklist(servers: list[inventory.Server]) -> list[dict]:
    """Servers whose files changed since their last start, with the jobs/items that changed them."""
    data = _load_pending()
    out = []
    for srv in servers:
        e = data.get(srv.id)
        if e and _last_start(srv) < e["changed"]:
            out.append({"server": srv.id, "since": _iso(e["since"]), "jobs": [
                {**j, "at": _iso(j["at"])} for j in e["jobs"]]})
    return out


def _iso(ts: float) -> str:
    return plans.iso(ts)


# ---------------------------------------------------------------- job factories

def _finish(job: jobs.Job) -> None:
    inventory.flush_cache()
    mark_changed(job)


def _prune_dir(root: Path, max_age_days: float) -> None:
    cutoff = time.time() - max_age_days * 86400
    for d in root.iterdir():
        if d.is_dir() and d.stat().st_mtime < cutoff:
            shutil.rmtree(d, ignore_errors=True)


def _mutating_body(job: jobs.Job, fn) -> None:
    """Runs while holding the mutate lock (for non-dry-run jobs)."""
    def recorded(entry: dict) -> None:
        # Every mutation is preceded by a backup record, so this also catches changes whose
        # item later ended in an error (partial rsync, failed rollback): the server still needs a restart.
        if entry["server"] not in job.changed_servers:
            job.changed_servers.append(entry["server"])
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


def start_apply(user: str, plan_id: str, exclude=None, dry_run: bool = False, auto: bool = False) -> jobs.Job:
    """Apply exactly the rows of a stored update plan (minus exclusions). Raises PlanError (409 on drift)."""
    with plans.lock:
        plan = plans.load(plan_id, "updates")
        rows = updates.check_plan(plan, exclude)
        params = {"plan_id": plan_id, "exclude": exclude or [], "auto": auto, "rows": len(rows)}
        job = jobs.submit("update-apply", user, params,
                          lambda job: _mutating_body(job, lambda ctx: updates.apply_rows(ctx, rows, require_verified=auto)),
                          dry_run=dry_run, on_done=_finish)
        if not dry_run:
            plans.consume(plan, job.id)
    return job


def start_deploy(user: str, plan_id: str) -> jobs.Job:
    """Apply a stored deploy plan, only if a fresh dry run still matches what was previewed."""
    with plans.lock:
        plan = plans.load(plan_id, "deploy")
        plans.ensure_usable(plan)
        if plan.get("needs_decision"):
            raise plans.PlanError(409, {
                "code": "needs_decision",
                "message": "this push would overwrite server-specific values; re-plan with preserve_keys "
                           '("server_specific", "none" or key list) or overwrite_server_specific: true',
                "warnings": [w for w in plan.get("warnings", []) if w.get("type") == "server_specific"],
                "conflicts": []})
        drift = engine.plan_drift(plan)
        if drift:
            raise plans.PlanError(409, {"message": "targets changed since the preview; re-plan", "conflicts": drift})
        req = engine.validate_deploy(plan["body"])
        params = {k: plan["body"].get(k) for k in ("source", "targets", "action", "items", "options", "force")}
        params["plan_id"] = plan_id

        def body(job: jobs.Job) -> None:
            # Re-check under the mutate lock: another job may have run while this one was queued.
            late = engine.plan_drift(plan)
            if late:
                for d in late:
                    job.add_result(d["server"] or "-", d["item"] or "-", d["action"] or "-", "error",
                                   f"changed since preview ({d['planned']} → {d['now']}); nothing applied")
                return
            _mutating_body(job, lambda ctx: engine.run_deploy(ctx, req))

        job = jobs.submit("deploy", user, params, body, on_done=_finish)
        plans.consume(plan, job.id)
    return job


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
            n = len(later)
            raise plans.PlanError(400, {
                "code": "later_jobs",
                "message": f"{n} later job{'s' * (n != 1)} changed the same files; undo "
                           f"{'them' if n != 1 else 'it'} first, newest first",
                "jobs": sorted(later, reverse=True)})
        _undo_pending.add(job_id)

    def done(job: jobs.Job) -> None:
        with _undo_lock:
            _undo_pending.discard(job_id)
            if job.status == "done":
                jobs.mark_undone(job_id, job.id, job.user)
                if original.undo_of:  # undoing an undo re-applies the first job: it is no longer undone
                    jobs.mark_undone(original.undo_of, None)
        _finish(job)

    return jobs.submit("undo", user, {"undo_of": job_id},
                       lambda job: _mutating_body(job, lambda ctx: engine.run_undo(ctx, job_id)),
                       on_done=done)
