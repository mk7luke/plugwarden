"""Plugin health from server logs: did a plugin start cleanly after a restart?

Logs are read from <server>/Minecraft/logs (latest.log + rotated YYYY-MM-DD-N.log.gz), confined to the
server directory and bounded to MAX_LOG_BYTES in total, newest first. Lines are stitched into *runs*
(one per server start: Paper/Purpur "[bootstrap] Running Java"/"Starting minecraft server", Velocity
"Booting up Velocity"); daily log rollover does not start a new run.

The verdict is differential:
  * hard failures always fail: "Error occurred while enabling X", "Could not load '…/<jar>'", unknown
    dependency / invalid plugin errors naming it, or the plugin disabled before "Done (";
  * other ERROR/SEVERE blocks that reference the plugin ([Name] tag, name in the message, "Could not pass
    event … to Name", or its main-class package in the stack trace) count only if they occur within
    GRACE_SECONDS of the start AND their normalised signature (no timestamps, numbers, IPs, UUIDs, hex,
    player names) did not occur in the baseline runs before. Known signatures are reported as
    preexisting; without any baseline run a new error is only a warning;
  * healthy additionally needs the plugin's own enable line ("Enabling X v<version>" /
    Velocity "Loaded plugin <id> <version>"); without it the result is unknown.
"""
from __future__ import annotations

import gzip
import re
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from . import inventory
from .inventory import Server
from .redact import scrub_value

MAX_LOG_BYTES = 20 * 1024 * 1024
MAX_LOG_FILES = 40
GRACE_SECONDS = 30 * 60
BASELINE_RUNS = 2
EXCERPT_BEFORE, EXCERPT_AFTER = 2, 12

_START = re.compile(r"\[bootstrap\] Running Java|Starting minecraft server version|Booting up Velocity")
_TIME = re.compile(r"^\[(\d{1,2}):(\d{2}):(\d{2})")
_LEVEL = re.compile(r"^\[[^\]]*?\b(ERROR|SEVERE)\]|^\[[^\]]*\]\s*\[[^\]]*/(ERROR|SEVERE)\]")
_WARN_LEVEL = re.compile(r"^\[[^\]]*?\b(WARN|WARNING)\]|^\[[^\]]*\]\s*\[[^\]]*/(WARN|WARNING)\]")
_MSG = re.compile(r"^(?:\[[^\]]*\]\s*)+?(?:\[[^\]]*/[A-Z]+\](?:\s*\[[^\]]*\])?:?|[A-Z]+\]:)\s*")
# Known-issue noise: banner decoration, "join my discord" lines, and update nags (not problems).
_TAGS = re.compile(r"^(?:\[[^\]]*\]\s*)+")
_ALNUM = re.compile(r"[A-Za-z0-9]")
_BANNER = re.compile(r"discord\.gg/|discord(?:app)?\.com/invite|github\.com/|patreon\.com|ko-fi\.com|paypal\.me|"
                     r"join (?:my|our) discord|support (?:server|discord)", re.I)
_NAG = re.compile(r"\bnew(?:er)? (?:plugin )?(?:version|release|update)|\bupdate (?:for .+ )?(?:is )?available|"
                  r"\bupdates? available|\b(?:is|seems to be|are|you're) (?:running an )?out(?:dated| of date)|"
                  r"\bplease update\b|\bnewest version\b", re.I)
_GZ_NAME = re.compile(r"^(\d{4})-(\d{2})-(\d{2})-(\d+)\.log\.gz$")
_PLAYER_LINES = [re.compile(r"UUID of player (\w{3,16})"), re.compile(r"\b(\w{3,16})\[/[\d.:]+\] logged in"),
                 re.compile(r"\b(\w{3,16}) (?:joined|left) the game"),
                 re.compile(r"\[(?:connected|initial) player\] (\w{3,16})"),
                 re.compile(r"for (\w{3,16}) after \d+ms")]


# ---------------------------------------------------------------- reading

def _logs_dir(srv: Server) -> Path | None:
    d = srv.root / "Minecraft" / "logs"
    try:
        real, root = d.resolve(), srv.root.resolve()
    except (OSError, RuntimeError):
        return None
    if d.is_symlink() or not real.is_dir() or not (real == root or root in real.parents):
        return None
    return real


def _files(d: Path) -> list[tuple[Path, date, int]]:
    """Log files with the calendar date of their content, oldest first (latest.log last)."""
    out = []
    for p in d.glob("*.log.gz"):
        m = _GZ_NAME.match(p.name)
        if m and p.is_file() and not p.is_symlink():
            out.append((p, date(int(m[1]), int(m[2]), int(m[3])), int(m[4])))
    out.sort(key=lambda t: (t[1], t[2]))
    latest = d / "latest.log"
    if latest.is_file() and not latest.is_symlink():
        out.append((latest, datetime.fromtimestamp(latest.stat().st_mtime).date(), 1 << 30))
    return out


def _read(p: Path, budget: int) -> bytes:
    try:
        if p.suffix == ".gz":
            with gzip.open(p, "rb") as f:
                return f.read(budget)
        size = p.stat().st_size
        with open(p, "rb") as f:
            f.seek(max(0, size - budget))
            return f.read(budget)
    except (OSError, EOFError, gzip.BadGzipFile):
        return b""


def read_runs(srv: Server, since: float | None = None, want_before: int = BASELINE_RUNS) -> list[dict]:
    """Runs (oldest → newest): {"start": epoch|None, "file", "lines": [(epoch, text)], "complete": bool}.

    Reads newest files first until it has the runs that started after `since` (or the latest run) plus
    `want_before` earlier complete runs for a baseline, within MAX_LOG_BYTES / MAX_LOG_FILES."""
    d = _logs_dir(srv)
    if d is None:
        return []
    budget = MAX_LOG_BYTES
    chunks = []  # newest first: (path, day, lines)
    starts_seen_before = 0
    for p, day, _n in reversed(_files(d)[-MAX_LOG_FILES:]):
        if budget <= 0:
            break
        data = _read(p, budget)
        budget -= len(data)
        lines = data.decode("utf-8", errors="replace").splitlines()
        chunks.append((p, day, lines))
        starts = [ln for ln in lines if _START.search(ln)]
        if starts:
            t = _abs(day, starts[0])
            if since is None or (t is not None and t < since):
                starts_seen_before += max(1, sum(1 for ln in lines if "Done (" in ln))
        if starts_seen_before > want_before:  # enough history: the newest run + baseline runs
            break
    runs: list[dict] = []
    cur = None
    for p, day, lines in reversed(chunks):
        for n, ln in enumerate(lines, 1):
            t = _abs(day, ln)
            # One start logs several markers ("[bootstrap] Running Java", then "Starting minecraft server");
            # a marker only opens a new run once the current one finished starting or stopped.
            if _START.search(ln) and (cur is None or cur["start"] is None or cur.get("ended")):
                cur = {"start": t, "file": p.name, "lines": [], "pos": [], "complete": True}
                runs.append(cur)
            elif cur is None:
                cur = {"start": None, "file": p.name, "lines": [], "pos": [], "complete": False}  # started before
                runs.append(cur)
            cur["lines"].append((t, ln))
            cur["pos"].append((p.name, n))  # file + 1-based line number, parallel to "lines"
            if "Done (" in ln or "Stopping server" in ln or "Shutting down the proxy" in ln:
                cur["ended"] = True
    return runs


def _abs(day: date, line: str) -> float | None:
    m = _TIME.match(line)
    if not m:
        return None
    try:
        return datetime(day.year, day.month, day.day, int(m[1]), int(m[2]), int(m[3])).timestamp()
    except ValueError:
        return None


_start_cache: dict[str, tuple[tuple, float | None]] = {}


def last_startup(srv: Server) -> float:
    """When the server last started (from log content; daily rollover doesn't count), or 0."""
    d = _logs_dir(srv)
    if d is None:
        return 0.0
    files = _files(d)
    sig = tuple((p.name, p.stat().st_mtime_ns, p.stat().st_size) for p, _d, _n in files[-3:])
    hit = _start_cache.get(srv.id)
    if hit and hit[0] == sig:
        return hit[1] or 0.0
    found = None
    budget = MAX_LOG_BYTES
    for p, day, _n in reversed(files[-MAX_LOG_FILES:]):
        data = _read(p, budget)
        budget -= len(data)
        starts = [ln for ln in data.decode("utf-8", errors="replace").splitlines() if _START.search(ln)]
        if starts:
            found = _abs(day, starts[-1])
            break
        if budget <= 0:
            break
    _start_cache[srv.id] = (sig, found)
    return found or 0.0


# ---------------------------------------------------------------- analysis

def _players(runs: list[dict]) -> set[str]:
    names = set()
    for r in runs:
        for _t, ln in r["lines"]:
            for pat in _PLAYER_LINES:
                m = pat.search(ln)
                if m:
                    names.add(m.group(1))
    return names


def signature(message: str, players: set[str]) -> str:
    """Error text with the variable parts removed, so the same problem matches across runs."""
    s = _MSG.sub("", message, count=1)
    for name in sorted(players, key=len, reverse=True):
        s = re.sub(rf"\b{re.escape(name)}\b", "<player>", s)
    s = re.sub(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", "<uuid>", s, flags=re.I)
    s = re.sub(r"/?\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b", "<ip>", s)
    s = re.sub(r"\b0x[0-9a-f]+\b|\b[0-9a-f]{12,}\b", "<hex>", s, flags=re.I)
    s = re.sub(r"\d+", "#", s)
    return re.sub(r"\s+", " ", s).strip()[:300]


def _blocks(lines: list[tuple], warnings: bool = False) -> list[tuple[int, int]]:
    """(start, end) index ranges of ERROR/SEVERE (or, with warnings=True, WARN) entries including their
    stack-trace lines."""
    level = _WARN_LEVEL if warnings else _LEVEL
    out, i = [], 0
    while i < len(lines):
        if level.search(lines[i][1]):
            j = i + 1
            while j < len(lines) and not _TIME.match(lines[j][1]):
                j += 1
            out.append((i, j))
            i = j
        else:
            i += 1
    return out


def _message(line: str) -> str:
    return _MSG.sub("", line, count=1)


def _noise(line: str) -> bool:
    """Banner decoration (no letters or digits once the [tags] are gone) or a support/discord/GitHub link."""
    msg = _message(line)
    rest = _TAGS.sub("", msg)
    return not _ALNUM.search(rest) or bool(_BANNER.search(msg))


def _groups(lines: list[tuple], warnings: bool = False) -> list[dict]:
    """ERROR (or WARN) entries, with consecutive entries from the same thread, second and plugin tag merged
    into one issue (multi-line banners). Each group is titled by its first line that isn't decoration or a
    support link; groups made only of those are dropped."""
    out: list[dict] = []
    prev_key = None
    for a, b in _blocks(lines, warnings):
        head = lines[a][1]
        msg = _message(head)
        tag = _TAGS.match(msg)
        key = (head[:len(head) - len(msg)], tag.group(0).strip() if tag else None)
        if out and key == prev_key and out[-1]["end"] == a:
            g = out[-1]
            g["end"], g["size"] = b, g["size"] + 1
        else:
            g = {"start": a, "end": b, "size": 1, "title": None}
            out.append(g)
        if g["title"] is None and not _noise(head):
            g["title"] = a
        prev_key = key
    return [g for g in out if g["title"] is not None]


def _dedup(items: list[dict]) -> list[dict]:
    """The same message several times in one start = one entry with a repeat count."""
    out: dict[str, dict] = {}
    for it in sorted(items, key=lambda k: k.get("line") or 0):
        if it["signature"] in out:
            out[it["signature"]]["repeats"] += 1
        else:
            out[it["signature"]] = {**it, "repeats": 1}
    return list(out.values())


def _cut_shutdown(lines: list[tuple]) -> list[tuple]:
    for i, (_t, ln) in enumerate(lines):
        if "Stopping server" in ln or "Stopping the server" in ln or "Shutting down the proxy" in ln:
            return lines[:i]
    return lines


def _hit(run: dict, lines: list[tuple], i: int, after: int = EXCERPT_AFTER) -> dict:
    """Where a match is: its log file + line number, and an excerpt that always contains the matching
    line (at excerpt[match_index]) with up to EXCERPT_BEFORE lines before and `after` lines after it."""
    after = max(after, 3)
    start = max(0, i - EXCERPT_BEFORE)
    pos = run.get("pos") or []
    log, line = pos[i] if i < len(pos) else (run.get("file"), i + 1)
    return {"log": log, "line": line, "match_index": i - start,
            "excerpt": [scrub_value(ln)[:500] for _t, ln in lines[start:i + 1 + after]]}


class _Matcher:
    def __init__(self, name: str, jar: str, main_pkg: str | None, family: str, plugin_id: str | None):
        esc = re.escape(name)
        self.name, self.family = name, family
        self.pid = (plugin_id or name).lower()
        if family == "velocity":
            pid = re.escape(self.pid)
            self.enabled = re.compile(rf"Loaded plugin {pid} (.+?)(?: by .*)?$", re.I)
            self.hard = [re.compile(rf"(Can't create|Couldn't (load|enable|create)|Unable to load|"
                                    rf"An error occurred while (loading|enabling)) plugin {pid}\b", re.I)]
            self.tags = [f"[{self.pid}]"]
            self.disabled = None
        else:
            self.enabled = re.compile(rf"\bEnabling {esc} v(.+)$")
            self.hard = [re.compile(rf"Error occurred while enabling {esc}\b"),
                         re.compile(rf"Could not load '[^']*{re.escape(jar)}'"),
                         re.compile(rf"Could not load plugin '?[^']*{re.escape(jar)}"),
                         re.compile(rf"(Error loading plugin|Failed to load plugin)[^\n]*{re.escape(jar)}"),
                         re.compile(rf"(UnknownDependencyException|InvalidPluginException|"
                                    rf"InvalidDescriptionException)[^\n]*({esc}|{re.escape(jar)})")]
            self.tags = [f"[{name}]"]
            self.disabled = re.compile(rf"\bDisabling {esc} v")
        self.name_re = re.compile(rf"\b{esc}\b")
        self.event = re.compile(rf"Could not pass event \w+ to {esc}\b")
        self.pkg = main_pkg

    def references(self, head: str, block: str) -> bool:
        return (any(t in head for t in self.tags) or bool(self.name_re.search(_MSG.sub("", head, count=1)))
                or bool(self.event.search(head)) or bool(self.pkg and self.pkg in block))


def baseline_signatures(baseline: list[dict], players: set[str]) -> dict[str, list[float | None]]:
    """signature -> start times of the baseline runs in which it appeared (errors and warnings)."""
    out: dict[str, list[float | None]] = {}
    for r in baseline:
        heads = _error_heads(r) + _error_heads(r, warnings=True)
        for sig in {signature(h[1], players) for h in heads}:
            out.setdefault(sig, []).append(r["start"])
    return out


def analyse_run(run: dict, baseline: list[dict], m: _Matcher, version: str | None, players: set[str],
                base_sigs: dict[str, list] | None = None) -> dict:
    lines = _cut_shutdown(run["lines"])
    start = run["start"] or (lines[0][0] if lines and lines[0][0] else None)
    done_i = next((i for i, (_t, ln) in enumerate(lines) if "Done (" in ln), None)
    enabled_version, enabled_i = None, None
    for i, (_t, ln) in enumerate(lines):
        e = m.enabled.search(ln)
        if e:
            enabled_version, enabled_i = e.group(1).strip(), i
        for h in m.hard:
            if h.search(ln):
                return {"status": "failed", "reason": "failed to load or enable", **_hit(run, lines, i),
                        "preexisting": _in_baseline(baseline, h), "running": False}
        if m.disabled and m.disabled.search(ln) and done_i is not None and i < done_i:
            return {"status": "failed", "reason": "disabled during startup", **_hit(run, lines, i),
                    "preexisting": False, "running": False}
    if base_sigs is None:
        base_sigs = baseline_signatures(baseline, players)
    new, known, late, new_warn, notices = [], [], [], [], []
    for level in ("error", "warning"):
        for g in _groups(lines, warnings=level == "warning"):
            i = g["title"]
            t, head = lines[i]
            block = "\n".join(ln for _t, ln in lines[g["start"]:g["end"]])
            if not m.references(head, block):
                continue
            item = {"signature": signature(head, players), "level": level,
                    "title": scrub_value(_message(head))[:300], "group_size": g["size"],
                    **_hit(run, lines, i, min(g["end"] - i, 8))}
            if _NAG.search(_message(head)):
                notices.append(item)  # "a new version is available": not a problem, listed separately
            elif start and t and t - start > GRACE_SECONDS:
                if level == "error":
                    late.append(item)  # runtime error long after start: not the update's startup, reported only
            elif item["signature"] in base_sigs:
                seen = [s for s in base_sigs[item["signature"]] if s]
                known.append({**item, "seen_in_runs": len(base_sigs[item["signature"]]) + 1,
                              "first_seen": min(seen) if seen else None})
            elif level == "error":
                new.append(item)
            else:
                new_warn.append({**item, "reason": "new warning (not blocking)"})
    known, new, late, new_warn = map(_dedup, (known, new, late, new_warn))
    # one notice per plugin: nags often come as several differently worded lines
    notices = [{**min(notices, key=lambda n: n.get("line") or 0), "repeats": len(notices)}] if notices else []
    res = {"preexisting_errors": known, "later_errors": late, "warnings": new_warn, "update_notices": notices,
           "running": None}
    off = _disabled_after_done(run, m)
    if off is not None:
        again = any(_disabled_after_done(r, m) is not None for r in baseline)
        return {**res, "status": "failed", "running": False, "preexisting": again,
                "reason": "disabled itself after startup" + (" (on every start)" if again else ""),
                **_hit(run, lines, off)}
    if new and baseline:
        first = {k: new[0][k] for k in ("line", "log", "excerpt", "match_index")}
        return {**res, "status": "failed", "reason": "new error after the update", **first, "new_errors": new,
                "running": True if enabled_i is not None else None}
    if new:
        res["warnings"] = [{**n, "reason": "error with no earlier run to compare against"} for n in new] + new_warn
    if enabled_i is None:
        return {**res, "status": "unknown", "reason": "no enable line for this plugin in the run", "excerpt": []}
    if version and _norm(enabled_version) != _norm(version) and not _norm(enabled_version).startswith(_norm(version)):
        return {**res, "status": "unknown", "reason": f"the server ran v{enabled_version}, not v{version}",
                **_hit(run, lines, enabled_i, 0), "excerpt": [], "match_index": None}
    return {**res, "status": "healthy", "running": True,
            "reason": "enabled" + (" (with known issues)" if known or new else ""),
            **_hit(run, lines, enabled_i, 0), "excerpt": [scrub_value(lines[enabled_i][1])[:500]], "match_index": 0}


def _error_heads(run: dict, warnings: bool = False) -> list[tuple]:
    lines = _cut_shutdown(run["lines"])
    return [lines[a] for a, _b in _blocks(lines, warnings)]


def _disabled_after_done(run: dict, m: "_Matcher") -> int | None:
    """Index of a "Disabling <Name>" line after "Done (" (and before shutdown, within the grace window):
    the plugin shut itself down after the server finished starting."""
    if not m.disabled:
        return None
    lines = _cut_shutdown(run["lines"])
    done_i = next((i for i, (_t, ln) in enumerate(lines) if "Done (" in ln), None)
    if done_i is None:
        return None
    start = run["start"] or (lines[0][0] if lines else None)
    for i in range(done_i + 1, len(lines)):
        t, ln = lines[i]
        if start and t and t - start > GRACE_SECONDS:
            return None
        if m.disabled.search(ln):
            return i
    return None


def _in_baseline(baseline: list[dict], pat: re.Pattern) -> bool:
    return any(pat.search(ln) for r in baseline for _t, ln in r["lines"])


def _norm(v: str | None) -> str:
    return re.sub(r"[\s+]", "", v or "").lower()


def _pick(runs: list[dict], since: float | None) -> tuple[dict | None, list[dict]]:
    """The run to judge (the latest run, or the latest one started after `since`) and its baseline runs."""
    if not runs:
        return None, []
    if since is not None:
        # After a change (canary, update check) the baseline is only starts from BEFORE it, i.e. with the old
        # version: a second post-change start that fails the same way must not make the failure "pre-existing".
        before = [r for r in runs if r["start"] and r["start"] < since and (r["complete"] or r["lines"])]
        after = [r for r in runs if r["start"] and r["start"] >= since]
        return (after[-1] if after else None), before[-BASELINE_RUNS:]
    baseline = [r for r in runs[:-1] if r["complete"] or r["lines"]][-BASELINE_RUNS:]
    return runs[-1], baseline


def plugin_health(srv: Server, runs: list[dict], plugin: dict, meta_desc: dict | None,
                  since: float | None = None, _ctx: dict | None = None) -> dict:
    ctx = _ctx or context(runs, since)
    target, baseline = ctx["target"], ctx["baseline"]
    empty = {"excerpt": [], "log": None, "preexisting_errors": [], "later_errors": [], "warnings": [],
             "update_notices": []}
    if target is None:
        return {**empty, "status": "unknown", "reason": "no server start in the logs since then"}
    if target["start"] is None:
        return {**empty, "status": "unknown", "reason": "the last server start is older than the available logs"}
    main = (meta_desc or {}).get("main")
    pkg = main.rsplit(".", 1)[0] if main and main.count(".") >= 2 else None
    m = _Matcher(plugin["name"], plugin["jar"], pkg, srv.family, (meta_desc or {}).get("id"))
    res = analyse_run(target, baseline, m, plugin.get("version"), ctx["players"], ctx["base_sigs"])
    return {**res, "log": res.get("log") or target["file"], "run_started": target["start"],
            "baseline_runs": len(baseline)}


def context(runs: list[dict], since: float | None) -> dict:
    """Things shared by every plugin checked against the same logs (computed once per report)."""
    target, baseline = _pick(runs, since)
    players = _players(runs)
    return {"target": target, "baseline": baseline, "players": players,
            "base_sigs": baseline_signatures(baseline, players)}


def server_report(srv: Server, since: float | None) -> dict:
    runs = read_runs(srv, since)
    ctx = context(runs, since)
    target, baseline = ctx["target"], ctx["baseline"]
    plugins = []
    cached_only = inventory.indexing_state() is not None
    for p in inventory.list_plugins(srv, cached_only=cached_only):
        meta = inventory.jar_meta(srv.plugins_dir / p["jar"], cached_only=cached_only)
        desc = (meta or {}).get("descriptors", {}).get(srv.family)
        r = plugin_health(srv, runs, p, desc, since, _ctx=ctx)
        plugins.append({"key": p["key"], "name": p["name"], "jar": p["jar"], "version": p["version"], **r})
    order = {"failed": 0, "unknown": 1, "healthy": 2}
    plugins.sort(key=lambda r: (order[r["status"]], r["name"].lower()))
    known, notices = [], []
    for p in plugins:
        for e in p.get("preexisting_errors") or []:
            known.append({"key": p["key"], "name": p["name"], "plugin": p["name"], "log": p.get("log"),
                          "reason": "also in earlier starts", **e})
        for e in p.get("update_notices") or []:
            notices.append({"key": p["key"], "name": p["name"], "plugin": p["name"], "log": p.get("log"), **e})
    known.sort(key=lambda k: (k["level"] != "error", k["name"].lower(), k.get("line") or 0))
    last_start = last_startup(srv) or None
    lines = target["lines"] if target else []
    report = {"server": srv.id, "since": since, "checked_at": time.time(),
            "run_started": target["start"] if target else None,
            "logs": sorted({r["file"] for r in runs}),
            "baseline_runs": len(baseline),
            "startup_complete": any("Done (" in ln for _t, ln in lines),
            "restarted_at": last_start,
            "restarted": bool(last_start and (since is None or last_start >= since)),
            "counts": {s: sum(1 for r in plugins if r["status"] == s) for s in ("healthy", "failed", "unknown")},
            "preexisting_errors": known,
            "update_notices": notices,
            "indexing": inventory.indexing_state(),
            "plugins": plugins}
    if not cached_only:
        remember(srv, report)
    return report


# ---------------------------------------------------------------- cached startup summary (dashboard)

_summary: dict[str, dict] = {}
_summary_lock = threading.Lock()
REFRESH_EVERY = 60.0


def _signature(srv: Server) -> tuple:
    d = _logs_dir(srv)
    logs = tuple((p.name, p.stat().st_mtime_ns, p.stat().st_size) for p, _d, _n in (_files(d)[-4:] if d else []))
    jars = tuple(sorted((p.name, p.stat().st_mtime_ns) for p in srv.plugins_dir.glob("*.jar"))) \
        if srv.plugins_dir.is_dir() else ()
    return logs, jars


def _summarise(report: dict) -> dict:
    failed = [{"key": p["key"], "name": p["name"], "reason": p["reason"], "preexisting": bool(p.get("preexisting")),
               "running": p.get("running")} for p in report["plugins"] if p["status"] == "failed"]
    known = report["preexisting_errors"]
    return {"failed": failed, "failed_count": len(failed), "run_started": report["run_started"],
            "known_errors": sum(1 for k in known if k.get("level") == "error"),
            "known_warnings": sum(1 for k in known if k.get("level") == "warning"),
            "update_notices": len(report.get("update_notices") or []),
            "checked_at": report["checked_at"]}


def remember(srv: Server, report: dict, sig: tuple | None = None) -> None:
    if report.get("since") is not None:
        return  # only "latest start" reports describe the server's current state
    with _summary_lock:
        _summary[srv.id] = {"sig": sig or _signature(srv), "summary": _summarise(report)}


def startup_summary(srv: Server) -> dict | None:
    """Cached summary of the latest start (never computed on the request path)."""
    with _summary_lock:
        hit = _summary.get(srv.id)
    return dict(hit["summary"]) if hit else None


def refresh_summaries() -> None:
    """Recompute reports whose logs or jars changed (background)."""
    for srv in inventory.discover():
        if srv.family == "fabric":
            continue
        try:
            sig = _signature(srv)
            with _summary_lock:
                hit = _summary.get(srv.id)
            if hit and hit["sig"] == sig:
                continue
            remember(srv, server_report(srv, None), sig)
        except Exception:  # noqa: BLE001 - background; one bad server must not stop the others
            continue


def start_refresher(stop: threading.Event) -> None:
    def loop():
        while not stop.is_set():
            if inventory.indexing_state() is None:
                refresh_summaries()
            stop.wait(REFRESH_EVERY if _summary else 5.0)
    threading.Thread(target=loop, name="health-summary", daemon=True).start()
