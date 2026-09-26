""""Since you last looked": per-user last-seen time and what happened after it (dashboard)."""
from __future__ import annotations

import threading
from datetime import datetime, timezone

from . import config, health, inventory, jobs, updates
from .storage import read_json, write_json

_lock = threading.Lock()
MAX_ITEMS = 20


def _path():
    return config.state("last_seen.json")


def _ts(iso: str | None) -> float | None:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() if iso else None
    except ValueError:
        return None


def last_seen(user: str) -> str | None:
    return (read_json(_path(), {}) or {}).get(user.lower())


def mark_seen(user: str, at: str | None = None) -> str:
    """Move the user's last-seen time forward (to `at`, capped at now; never backwards)."""
    now = datetime.now(timezone.utc)
    t = min(_ts(at) or now.timestamp(), now.timestamp())
    with _lock:
        data = read_json(_path(), {}) or {}
        cur = _ts(data.get(user.lower()))
        if cur is None or t > cur:
            data[user.lower()] = datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="seconds")
            write_json(_path(), data)
        return data[user.lower()]


def since_last_visit(user: str, pending: list[dict]) -> dict:
    """What changed after the user's last-seen time: newly found updates, plugins that stopped running on a
    start since then, and jobs other people (or the scheduler) finished. `at` is null on a first visit."""
    at = last_seen(user)
    t = _ts(at)
    if t is None:
        return {"at": None, "new_updates": [], "new_failures": [], "jobs_by_others": []}
    first = updates.load_cache().get("first_seen") or {}
    new_updates = []
    for u in pending:
        seen = _ts(first.get(f"{u['key']}|{u['to_version']}"))
        if seen is not None and seen > t:
            new_updates.append({"key": u["key"], "name": u["name"], "to_version": u["to_version"],
                                "from_versions": u["from_versions"], "servers": u["servers"],
                                "first_seen": first[f"{u['key']}|{u['to_version']}"]})
    new_updates.sort(key=lambda u: u["first_seen"], reverse=True)
    failures = []
    for srv in inventory.discover():
        s = health.startup_summary(srv)
        if not s or not s.get("run_started") or s["run_started"] <= t:
            continue
        for f in s["failed"]:
            failures.append({"server": srv.id, "key": f["key"], "name": f["name"], "reason": f["reason"],
                             "preexisting": f["preexisting"], "cause": (f.get("cause") or {}).get("kind"),
                             "run_started": s["run_started"]})
    others = []
    for d in jobs.list_jobs(200):
        ft = _ts(d.get("finished"))
        if ft is None or ft <= t:
            continue
        if d["user"].lower() == user.lower() or d.get("dry_run") or d["kind"] == "update-check":
            continue
        others.append({"id": d["id"], "kind": d["kind"], "user": d["user"], "status": d["status"],
                       "finished": d["finished"], "summary": d["summary"], "servers": d["changed_servers"]})
    return {"at": at, "new_updates": new_updates[:MAX_ITEMS], "new_updates_total": len(new_updates),
            "new_failures": failures[:MAX_ITEMS], "jobs_by_others": others[:MAX_ITEMS]}
