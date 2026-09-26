"""Background job runner: threads, JSON persistence, one mutating job at a time, live log for SSE."""
from __future__ import annotations

import re
import threading
import time
import traceback
from datetime import datetime, timezone
from typing import Any, Callable

from . import config
from .storage import read_json, write_json

JOB_ID_RE = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{6}$")
ACTIVE = ("queued", "running")
MAX_LOG_LINES = 20000

class FifoLock:
    """Mutex that grants waiters in arrival order, so queued jobs run in the order they were submitted."""

    def __init__(self):
        self._cond = threading.Condition()
        self._next_ticket = 0
        self._serving = 0

    def acquire(self) -> None:
        with self._cond:
            ticket = self._next_ticket
            self._next_ticket += 1
            while ticket != self._serving:
                self._cond.wait()

    def busy(self) -> bool:
        with self._cond:
            return self._next_ticket != self._serving

    def release(self) -> None:
        with self._cond:
            self._serving += 1
            self._cond.notify_all()


_mutate_lock = FifoLock()   # only one job that writes to servers at a time
_jobs_lock = threading.Lock()
_live: dict[str, "Job"] = {}       # jobs started in this process (running or recently finished)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class JobError(Exception):
    pass


_id_lock = threading.Lock()
_last_id = ""


def _new_id() -> str:
    """Sortable, strictly increasing ids: YYYYmmdd-HHMMSS-<microseconds as 6 hex digits>."""
    global _last_id
    with _id_lock:
        while True:
            now = datetime.now(timezone.utc)
            jid = now.strftime("%Y%m%d-%H%M%S") + f"-{now.microsecond:06x}"
            if jid > _last_id:
                _last_id = jid
                return jid


class Job:
    def __init__(self, kind: str, user: str, params: dict, mutating: bool, dry_run: bool = False):
        self.id = _new_id()
        self.kind = kind
        self.user = user
        self.params = params
        self.mutating = mutating
        self.dry_run = dry_run
        self.status = "queued"
        self.created = now_iso()
        self.started: str | None = None
        self.finished: str | None = None
        self.summary = ""
        self.results: list[dict] = []
        self.log: list[str] = []
        self.undo_of: str | None = params.get("undo_of")
        self.undone_by: str | None = None
        self.has_backup = False
        self.changed_servers: list[str] = []
        self._last_save = 0.0
        self._cond = threading.Condition()

    # ---- progress API used by job bodies
    def write(self, line: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        for part in str(line).splitlines() or [""]:
            with self._cond:
                if len(self.log) < MAX_LOG_LINES:
                    self.log.append(f"[{stamp}] {part}")
                elif len(self.log) == MAX_LOG_LINES:
                    self.log.append("[log truncated]")
                self._cond.notify_all()
        self._maybe_save()

    def add_result(self, server: str, item: str, action: str, outcome: str, detail: str = "", **extra) -> dict:
        r = {"server": server, "item": item, "action": action, "outcome": outcome, "detail": detail, **extra}
        self.results.append(r)
        if outcome == "changed" and not self.dry_run and server not in self.changed_servers:
            self.changed_servers.append(server)
        self._maybe_save()
        return r

    def to_dict(self, with_log: bool = True) -> dict:
        d = {
            "id": self.id, "kind": self.kind, "status": self.status, "user": self.user,
            "created": self.created, "started": self.started, "finished": self.finished,
            "summary": self.summary, "dry_run": self.dry_run, "params": self.params,
            "results": self.results, "undo_of": self.undo_of, "undone_by": self.undone_by,
            "undoable": self.undoable, "changed_servers": self.changed_servers,
            "has_backup": self.has_backup,
        }
        if with_log:
            d["log"] = "\n".join(self.log)
        return d

    @property
    def undoable(self) -> bool:
        return (self.has_backup and not self.dry_run and self.undone_by is None
                and self.status in ("done", "failed", "interrupted")
                and config.state("backups", self.id, "manifest.json").is_file())

    def save(self) -> None:
        self._last_save = time.monotonic()
        d = self.to_dict(with_log=False)
        d["log_lines"] = self.log
        write_json(config.state("jobs", f"{self.id}.json"), d)

    def _maybe_save(self) -> None:
        if time.monotonic() - self._last_save > 1.0:
            self.save()

    def _finish(self, status: str) -> None:
        with self._cond:
            self.status = status
            self.finished = now_iso()
            self._cond.notify_all()
        self.save()


def _from_disk(d: dict) -> Job:
    j = Job.__new__(Job)
    j.__dict__.update({
        "id": d["id"], "kind": d.get("kind"), "user": d.get("user"), "params": d.get("params") or {},
        "mutating": d.get("mutating", True), "dry_run": d.get("dry_run", False), "status": d.get("status"),
        "created": d.get("created"), "started": d.get("started"), "finished": d.get("finished"),
        "summary": d.get("summary", ""), "results": d.get("results") or [], "log": d.get("log_lines") or [],
        "undo_of": d.get("undo_of"), "undone_by": d.get("undone_by"), "has_backup": d.get("has_backup", False),
        "changed_servers": d.get("changed_servers") or [], "_last_save": 0.0, "_cond": threading.Condition(),
    })
    return j


def get(job_id: str) -> Job | None:
    if not JOB_ID_RE.match(job_id or ""):
        return None
    with _jobs_lock:
        if job_id in _live:
            return _live[job_id]
    d = read_json(config.state("jobs", f"{job_id}.json"))
    return _from_disk(d) if d else None


def list_jobs(limit: int = 100) -> list[dict]:
    files = sorted(config.state("jobs").glob("*.json"), reverse=True)[: max(1, min(limit, 1000))]
    out = []
    for f in files:
        jid = f.stem
        with _jobs_lock:
            live = _live.get(jid)
        if live:
            out.append(live.to_dict(with_log=False))
            continue
        d = read_json(f)
        if d:
            out.append(_from_disk(d).to_dict(with_log=False))
    return out


def active_jobs() -> list[Job]:
    with _jobs_lock:
        return [j for j in _live.values() if j.status in ACTIVE]


def find_active(kind: str) -> Job | None:
    for j in active_jobs():
        if j.kind == kind:
            return j
    return None


def mark_undone(job_id: str, by: str) -> None:
    j = get(job_id)
    if j:
        j.undone_by = by
        j.save()


def submit(kind: str, user: str, params: dict, body: Callable[[Job], str | None],
           mutating: bool = True, dry_run: bool = False,
           on_done: Callable[[Job], None] | None = None) -> Job:
    """Start a background job. body(job) does the work and returns a summary string."""
    job = Job(kind, user, params, mutating=mutating and not dry_run, dry_run=dry_run)
    with _jobs_lock:
        _live[job.id] = job
        # Keep memory bounded: forget finished jobs beyond the most recent 50.
        finished = [k for k, v in _live.items() if v.status not in ACTIVE]
        for k in finished[:-50]:
            _live.pop(k, None)
    job.save()

    def run() -> None:
        lock = _mutate_lock if job.mutating else None
        if lock:
            if lock.busy():
                job.write("Waiting for another job to finish…")
            lock.acquire()
        final = "failed"
        try:
            job.status = "running"
            job.started = now_iso()
            job.write(f"{kind} started by {user}" + (" (dry run)" if dry_run else ""))
            job.save()
            summary = body(job)
            job.summary = summary or _default_summary(job)
            failed = any(r["outcome"] == "error" for r in job.results)
            job.write("Finished" + (" with errors" if failed else ""))
            final = "failed" if failed else "done"
        except Exception as e:  # noqa: BLE001 - job boundary: record and continue
            job.write(f"ERROR: {e}")
            job.write(traceback.format_exc())
            job.summary = job.summary or f"Failed: {e}"
        finally:
            if lock:
                lock.release()
        # on_done sees the final status; waiters are released only after it ran (finished is set last).
        job.status = final
        if on_done:
            try:
                on_done(job)
            except Exception:  # noqa: BLE001
                traceback.print_exc()
        job._finish(final)

    threading.Thread(target=run, name=f"job-{job.id}", daemon=True).start()
    return job


def _default_summary(job: Job) -> str:
    counts: dict[str, int] = {}
    for r in job.results:
        counts[r["outcome"]] = counts.get(r["outcome"], 0) + 1
    if not counts:
        return "Nothing to do"
    return ", ".join(f"{v} {k}" for k, v in sorted(counts.items()))


def recover_interrupted() -> None:
    """Jobs left queued/running by a previous process are marked interrupted."""
    for f in config.state("jobs").glob("*.json"):
        d = read_json(f)
        if d and d.get("status") in ACTIVE:
            d["status"] = "interrupted"
            d["has_backup"] = config.state("backups", d["id"], "manifest.json").is_file()
            d["finished"] = d.get("finished") or now_iso()
            d.setdefault("log_lines", []).append("[service restarted: job interrupted]")
            write_json(f, d)


def wait(job: Job, timeout: float = 60.0) -> Job:
    """Block until a job finishes (used by the scheduler and tests)."""
    deadline = time.monotonic() + timeout
    with job._cond:
        while job.finished is None and time.monotonic() < deadline:
            job._cond.wait(0.5)
    return job
