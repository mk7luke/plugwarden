"""Network statistics for the Stats page: inventory, freshness, history, update lag, health, footprint.

Read-only and network-free: everything comes from the jar index, the update cache, job records and the
cached startup summaries. The result is cached until something it depends on changes (or MAX_AGE).

History comes from jar file mtimes, the only install history already on disk: a jar's mtime is roughly
when that build was installed. Deploys copy with rsync -a, which keeps the source jar's mtime, so a
deployed jar dates from when the build landed on the source server. PlugWarden job records (update,
deploy, remove, undo) are exact and take precedence for the jars they wrote.
"""
from __future__ import annotations

import os
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from . import config, health, inventory, jobs, settings, updates
from .storage import read_json, write_json

MAX_AGE = 60.0             # seconds; relative values ("days behind") stay fresh enough
HISTORY_WEEKS = 26
HISTORY_DAYS = 365
TREND_CAP = 400
MIN_MTIME = datetime(2009, 1, 1, tzinfo=timezone.utc).timestamp()  # older than Bukkit: a bogus clock
FUTURE_SLACK = 86400       # mtimes more than a day ahead are bogus too
SOURCE_KINDS = ("modrinth", "hangar", "spiget", "github")
JOB_KINDS = ("update-apply", "deploy", "remove", "undo")
STATUSES = ("current", "outdated", "unknown", "pinned", "ignored")
STATUS_RANK = {"outdated": 0, "current": 1, "pinned": 2, "ignored": 3, "unknown": 4}  # duplicate jars: worst wins
DAY = 86400.0
LAG_BUCKETS = [("<1d", 1), ("1–3d", 3), ("3–7d", 7), ("1–2w", 14), ("2–4w", 28), ("1–3mo", 91), (">3mo", None)]
HISTORY_NOTE = ("Dates come from jar file times: a jar's modification time is roughly when that build was "
                "installed. Deploys copy jars with rsync -a, which keeps the source server's time, so a "
                "deployed jar dates from when it landed on the source. PlugWarden job records are exact and "
                "replace file times for the jars they wrote; removals are known only from job records.")

_lock = threading.Lock()
_cached: dict[str, Any] = {"sig": None, "at": 0.0, "data": None}


def _history_file():
    return config.state("cache", "stats_history.json")


# ---------------------------------------------------------------- trend snapshots (written by update checks)

def load_trend() -> list[dict]:
    data = read_json(_history_file(), None)
    return [s for s in data if isinstance(s, dict)] if isinstance(data, list) else []


def record_snapshot(counts: dict, tracked: int, total: int) -> None:
    """Append one compact point after an update check (kept to the last TREND_CAP). current_installs and
    score use the freshness definitions, so trend points and the freshness section agree."""
    st, cache = settings.load_raw(), updates.load_cache()
    rows = {srv.id: [{"key": p["key"], "status": updates.status_for(p, srv, st, cache)[0]}
                     for p in inventory.list_plugins(srv)] for srv in inventory.discover()}
    c = dict.fromkeys(STATUSES, 0)
    for status in _installs(rows).values():
        c[status] += 1
    snap = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "outdated_plugins": counts["plugins"], "outdated_installs": counts["installs"],
            "servers": counts["servers"], "tracked": tracked, "total": total,
            "current_installs": c["current"], "score": _score(c)}
    write_json(_history_file(), (load_trend() + [snap])[-TREND_CAP:])


# ---------------------------------------------------------------- cache

def _mtime(path) -> int | None:
    try:
        return os.stat(path).st_mtime_ns
    except OSError:
        return None


def _signature(servers: list[inventory.Server]) -> tuple:
    """Everything the stats read. State files are written by atomic replace, so their dirs' mtimes move."""
    dirs = [config.STATE_DIR, config.state("cache"), config.state("jobs"), config.state("backups")]
    summaries = tuple((s.id, (health.startup_summary(s) or {}).get("checked_at")) for s in servers)
    plugin_dirs = tuple((s.id, _mtime(s.plugins_dir)) for s in servers)
    return (str(config.STATE_DIR), tuple(_mtime(d) for d in dirs), plugin_dirs, summaries,
            inventory.indexing_state() is not None, date.today())


def get(snapshot: Callable[[], dict]) -> dict:
    """Cached stats; `snapshot` (main.snapshot) is only called when something changed."""
    servers = inventory.discover()
    sig = _signature(servers)
    with _lock:
        if _cached["sig"] == sig and time.monotonic() - _cached["at"] < MAX_AGE:
            return _cached["data"]
    data = compute(snapshot(), time.time())
    # Re-read: computing can write the state it depends on (jar index flush); MAX_AGE bounds any race.
    sig = _signature(servers)
    with _lock:
        _cached.update(sig=sig, at=time.monotonic(), data=data)
    return data


def reset() -> None:
    with _lock:
        _cached.update(sig=None, at=0.0, data=None)


# ---------------------------------------------------------------- sections

def compute(snap: dict, now: float) -> dict:
    job_list = jobs.list_jobs(1000)
    events, excluded, when = _install_times(snap, job_list, now)
    return {
        "generated_at": datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="seconds"),
        "indexing": inventory.indexing_state(),
        "inventory": _inventory(snap),
        "freshness": _freshness(snap, _installs(snap["plugins"])),
        "sources": _sources(snap),
        "history": _history(events, excluded, now),
        "lag": _lag(snap, when, now),
        "health": _health(snap),
        "trend": load_trend(),
        "footprint": _footprint(snap, job_list),
    }


def _installs(plugins: dict[str, list[dict]]) -> dict[tuple[str, str], str]:
    """(server, plugin key) -> status; duplicate jars of one plugin on a server count once (worst status)."""
    out: dict[tuple[str, str], str] = {}
    for sid, rows in plugins.items():
        for r in rows:
            k = (sid, r["key"])
            if k not in out or STATUS_RANK[r["status"]] < STATUS_RANK[out[k]]:
                out[k] = r["status"]
    return out


def _inventory(snap: dict) -> dict:
    on: dict[str, dict] = {}  # key -> {name, servers}
    for sid, rows in snap["plugins"].items():
        for r in rows:
            on.setdefault(r["key"], {"name": r["name"], "servers": set()})["servers"].add(sid)
    servers = []
    for srv in snap["servers"]:
        rows = snap["plugins"][srv.id]
        keys = {r["key"] for r in rows}
        servers.append({"id": srv.id, "platform": srv.platform, "family": srv.family, "mc_version": srv.mc_version,
                        "plugins": len(keys), "jars": len(rows), "bytes": sum(r["size"] for r in rows),
                        "unique": sum(1 for k in keys if len(on[k]["servers"]) == 1)})
    sharing: dict[int, int] = {}
    for v in on.values():
        sharing[len(v["servers"])] = sharing.get(len(v["servers"]), 0) + 1
    ranked = sorted(on.items(), key=lambda kv: (-len(kv[1]["servers"]), kv[1]["name"].lower()))
    return {
        "totals": {"servers": len(servers), "plugin_servers": sum(1 for s in snap["servers"] if s.family != "fabric"),
                   "plugins": len(on), "jars": sum(s["jars"] for s in servers),
                   "bytes": sum(s["bytes"] for s in servers)},
        "servers": servers,
        "sharing": [{"servers": n, "plugins": sharing[n]} for n in sorted(sharing)],
        "most_shared": [{"key": k, "name": v["name"], "servers": len(v["servers"])} for k, v in ranked[:10]
                        if len(v["servers"]) > 1],
        "unique": sorted(({"key": k, "name": v["name"], "server": next(iter(v["servers"]))}
                          for k, v in on.items() if len(v["servers"]) == 1),
                         key=lambda u: (u["server"].lower(), u["name"].lower())),
    }


def _score(c: dict) -> float | None:
    tracked = c["current"] + c["outdated"]
    return round(100 * c["current"] / tracked, 1) if tracked else None


def _freshness(snap: dict, installs: dict[tuple[str, str], str]) -> dict:
    """Installs are (server, plugin) pairs. Score = current / (current + outdated): pinned, ignored and
    unknown installs are not tracked against a latest version."""
    total = dict.fromkeys(STATUSES, 0)
    per: dict[str, dict] = {s.id: dict.fromkeys(STATUSES, 0) for s in snap["servers"] if s.family != "fabric"}
    for (sid, _), status in installs.items():
        total[status] += 1
        per[sid][status] += 1
    return {"score": _score(total), "counts": {**total, "total": sum(total.values())},
            "servers": [{"id": sid, **c, "score": _score(c)} for sid, c in per.items()],
            "last_check": snap["cache"].get("checked_at")}


def _sources(snap: dict) -> dict:
    kind_of: dict[str, str | None] = {}
    for rows in snap["plugins"].values():
        for r in rows:
            kind = (r["source"] or {}).get("kind")
            if kind_of.get(r["key"]) is None:
                kind_of[r["key"]] = kind if kind in SOURCE_KINDS else None
    out = {k: 0 for k in SOURCE_KINDS}
    out["untracked"] = 0
    for kind in kind_of.values():
        out[kind or "untracked"] += 1
    return out


def _job_time(d: dict) -> float | None:
    for f in ("finished", "started", "created"):
        if d.get(f):
            try:
                return datetime.fromisoformat(d[f]).timestamp()
            except ValueError:
                continue
    return None


def _job_changes(job_list: list[dict]) -> tuple[list[float], dict[tuple[str, str], float]]:
    """Jar changes from job records: (times of each changed jar result, (server, jar written) -> time)."""
    times: list[float] = []
    wrote: dict[tuple[str, str], float] = {}
    for d in job_list:
        if d["kind"] not in JOB_KINDS or d.get("dry_run"):
            continue
        t = _job_time(d)
        if t is None:
            continue
        for r in d.get("results") or []:
            if r.get("outcome") != "changed":
                continue
            if not (r.get("new_jar") or r.get("old_jars") or str(r.get("item") or "").lower().endswith(".jar")):
                continue  # config/folder syncs are not installs
            times.append(t)
            if r.get("new_jar"):
                k = (r["server"], r["new_jar"])
                wrote[k] = max(wrote.get(k, 0.0), t)
    return times, wrote


def _bogus(ts: float, now: float) -> bool:
    return ts < MIN_MTIME or ts > now + FUTURE_SLACK


def _install_times(snap: dict, job_list: list[dict], now: float) -> tuple[list[tuple[float, bool]], int, dict]:
    """[(time, from_job)] per jar change, the number of bogus mtimes skipped, and each current jar's
    install time ((server, jar) -> time: its job's time when a job wrote it, else its mtime)."""
    job_times, wrote = _job_changes(job_list)
    events = [(t, True) for t in job_times]
    excluded = 0
    when: dict[tuple[str, str], float] = {}
    for sid, rows in snap["plugins"].items():
        for r in rows:
            k = (sid, r["jar"])
            if k in wrote:
                when[k] = wrote[k]
                continue
            if _bogus(r["mtime"], now):
                excluded += 1
                continue
            when[k] = float(r["mtime"])
            events.append((float(r["mtime"]), False))
    return events, excluded, when


def _history(events: list[tuple[float, bool]], excluded: int, now: float) -> dict:
    today = datetime.fromtimestamp(now).date()
    first_day = today - timedelta(days=HISTORY_DAYS - 1)
    this_week = today - timedelta(days=today.weekday())
    first_week = this_week - timedelta(weeks=HISTORY_WEEKS - 1)
    daily = dict.fromkeys((first_day + timedelta(days=i) for i in range(HISTORY_DAYS)), 0)
    weekly = {first_week + timedelta(weeks=i): [0, 0] for i in range(HISTORY_WEEKS)}
    for t, from_job in events:
        d = datetime.fromtimestamp(t).date()
        if d in daily:
            daily[d] += 1
        w = weekly.get(d - timedelta(days=d.weekday()))
        if w is not None:
            w[0] += 1
            w[1] += from_job
    return {
        "weekly": [{"week_start": w.isoformat(), "count": c, "from_jobs": j} for w, (c, j) in weekly.items()],
        "daily": [{"date": d.isoformat(), "count": c} for d, c in daily.items()],
        "recent_30d": {"changes": sum(1 for t, _ in events if now - t <= 30 * DAY),
                       "from_jobs": sum(1 for t, j in events if j and now - t <= 30 * DAY)},
        "max_daily": max(daily.values()), "total": sum(daily.values()), "excluded": excluded,
        "note": HISTORY_NOTE,
    }


def _percentile(sorted_vals: list[float], q: float) -> float | None:
    """Linear interpolation between closest ranks."""
    if not sorted_vals:
        return None
    pos = (len(sorted_vals) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return round(sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo), 1)


def _histogram(days: list[float]) -> list[dict]:
    """Counts per LAG_BUCKETS bucket; lo inclusive, hi exclusive (None = open-ended), in days."""
    out, lo = [], 0
    for label, hi in LAG_BUCKETS:
        out.append({"bucket": label, "lo": lo, "hi": hi,
                    "count": sum(1 for v in days if v >= lo and (hi is None or v < hi))})
        lo = hi or lo
    return out


def _lag(snap: dict, when: dict[tuple[str, str], float], now: float) -> dict:
    """Install lag = install time − publish date of the installed version (Modrinth-identified jars, whose
    installed version's date the update check caches), clamped at 0. Behind = days since the latest
    compatible version was published, for outdated installs."""
    entries = snap["cache"].get("entries") or {}
    by_id = {s.id: s for s in snap["servers"]}
    lags: list[float] = []
    for sid, rows in snap["plugins"].items():
        srv = by_id[sid]
        for r in rows:
            t = when.get((sid, r["jar"]))
            e = entries.get(updates.cache_key(r["sha1"], srv.family, srv.mc_version)) if r["sha1"] else None
            pub = updates._parse_time(((e or {}).get("current") or {}).get("published"))
            if t is None or pub is None or pub.timestamp() < MIN_MTIME:
                continue
            lags.append(max(0.0, (t - pub.timestamp()) / DAY))
    lags.sort()
    behind = []
    for u in snap["pending"]:
        pub = updates._parse_time(u.get("published"))
        if pub is None:
            continue
        behind.append({"key": u["key"], "name": u["name"], "to_version": u["to_version"],
                       "servers": len(u["servers"]), "days": round(max(0.0, (now - pub.timestamp()) / DAY), 1)})
    behind.sort(key=lambda b: (-b["days"], b["name"].lower()))
    per_install = sorted(b["days"] for b in behind for _ in range(b["servers"]))
    return {
        "samples": len(lags), "median_days": _percentile(lags, 0.5), "p75_days": _percentile(lags, 0.75),
        "histogram": _histogram(lags),
        "behind": {"installs": len(per_install), "median_days": _percentile(per_install, 0.5),
                   "max_days": per_install[-1] if per_install else None, "histogram": _histogram(per_install),
                   "plugins": behind},
    }


def _health(snap: dict) -> dict:
    servers = dict.fromkeys(("ok", "warnings", "not_running", "unknown"), 0)
    plugins = dict.fromkeys(("ok", "warnings", "not_running"), 0)
    per = []
    for srv in snap["servers"]:
        if srv.family == "fabric":
            continue
        s = health.startup_summary(srv)
        if s is None:
            status, failed, warns = "unknown", None, None
        else:
            failed, warns = s["failed_count"], s["warning_count"]
            status = "not_running" if failed else "warnings" if warns else "ok"
            n = len({r["key"] for r in snap["plugins"][srv.id]})
            plugins["not_running"] += failed
            plugins["warnings"] += warns
            plugins["ok"] += max(0, n - failed - warns)
        servers[status] += 1
        per.append({"id": srv.id, "status": status, "failed": failed, "warnings": warns})
    return {"servers": servers, "plugins": plugins, "per_server": per}


def _dir_bytes(root) -> tuple[int, int]:
    """(total bytes, top-level entries) under root, without following symlinks."""
    total, top = 0, 0
    try:
        top = sum(1 for _ in os.scandir(root))
    except OSError:
        return 0, 0
    for dirpath, _, files in os.walk(root):
        for f in files:
            try:
                total += os.lstat(os.path.join(dirpath, f)).st_size
            except OSError:
                pass
    return total, top


def _footprint(snap: dict, job_list: list[dict]) -> dict:
    per = [{"id": s.id, "bytes": sum(r["size"] for r in snap["plugins"][s.id])} for s in snap["servers"]]
    backup_bytes, backups = _dir_bytes(config.state("backups"))
    return {"jar_bytes": sum(p["bytes"] for p in per), "servers": per, "backups_bytes": backup_bytes,
            "backups": backups, "jobs": len(job_list), "undos": sum(1 for d in job_list if d["kind"] == "undo"),
            "jobs_failed": sum(1 for d in job_list if d["run_status"] == "failed"),
            "jobs_by_kind": {k: sum(1 for d in job_list if d["kind"] == k)
                             for k in sorted({d["kind"] for d in job_list})}}
