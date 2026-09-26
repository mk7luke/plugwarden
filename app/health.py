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
_MSG = re.compile(r"^(?:\[[^\]]*\]\s*)+?(?:\[[^\]]*/[A-Z]+\](?:\s*\[[^\]]*\])?:?|[A-Z]+\]:)\s*")
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
        for ln in lines:
            t = _abs(day, ln)
            # One start logs several markers ("[bootstrap] Running Java", then "Starting minecraft server");
            # a marker only opens a new run once the current one finished starting or stopped.
            if _START.search(ln) and (cur is None or cur["start"] is None or cur.get("ended")):
                cur = {"start": t, "file": p.name, "lines": [], "complete": True}
                runs.append(cur)
            elif cur is None:
                cur = {"start": None, "file": p.name, "lines": [], "complete": False}  # started before our window
                runs.append(cur)
            cur["lines"].append((t, ln))
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


def _blocks(lines: list[tuple]) -> list[tuple[int, int]]:
    """(start, end) index ranges of ERROR/SEVERE entries including their stack-trace lines."""
    out, i = [], 0
    while i < len(lines):
        if _LEVEL.search(lines[i][1]):
            j = i + 1
            while j < len(lines) and not _TIME.match(lines[j][1]):
                j += 1
            out.append((i, j))
            i = j
        else:
            i += 1
    return out


def _cut_shutdown(lines: list[tuple]) -> list[tuple]:
    for i, (_t, ln) in enumerate(lines):
        if "Stopping server" in ln or "Stopping the server" in ln or "Shutting down the proxy" in ln:
            return lines[:i]
    return lines


def _excerpt(lines: list[tuple], i: int, after: int = EXCERPT_AFTER) -> list[str]:
    return [scrub_value(ln)[:500] for _t, ln in lines[max(0, i - EXCERPT_BEFORE):i + after]]


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


def baseline_signatures(baseline: list[dict], players: set[str]) -> set[str]:
    return {signature(h[1], players) for r in baseline for h in _error_heads(r)}


def analyse_run(run: dict, baseline: list[dict], m: _Matcher, version: str | None, players: set[str],
                base_sigs: set[str] | None = None) -> dict:
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
                return {"status": "failed", "reason": "failed to load or enable", "line": i + 1,
                        "excerpt": _excerpt(lines, i), "preexisting": _in_baseline(baseline, h)}
        if m.disabled and m.disabled.search(ln) and done_i is not None and i < done_i:
            return {"status": "failed", "reason": "disabled during startup", "line": i + 1,
                    "excerpt": _excerpt(lines, i), "preexisting": False}
    if base_sigs is None:
        base_sigs = baseline_signatures(baseline, players)
    new, known, late = [], [], []
    for a, b in _blocks(lines):
        t, head = lines[a]
        block = "\n".join(ln for _t, ln in lines[a:b])
        if not m.references(head, block):
            continue
        item = {"line": a + 1, "signature": signature(head, players), "excerpt": _excerpt(lines, a, min(b - a, 8) + 1)}
        if start and t and t - start > GRACE_SECONDS:
            late.append(item)  # runtime error long after start: not the update's startup, reported only
        elif item["signature"] in base_sigs:
            known.append(item)
        else:
            new.append(item)
    res = {"preexisting_errors": known, "later_errors": late, "warnings": []}
    if new and baseline:
        return {**res, "status": "failed", "reason": "new error after the update", "line": new[0]["line"],
                "excerpt": new[0]["excerpt"], "new_errors": new}
    if new:
        res["warnings"] = [{**n, "reason": "error with no earlier run to compare against"} for n in new]
    if enabled_i is None:
        return {**res, "status": "unknown", "reason": "no enable line for this plugin in the run", "excerpt": []}
    if version and _norm(enabled_version) != _norm(version) and not _norm(enabled_version).startswith(_norm(version)):
        return {**res, "status": "unknown", "reason": f"the server ran v{enabled_version}, not v{version}",
                "line": enabled_i + 1, "excerpt": []}
    return {**res, "status": "healthy", "reason": "enabled" + (" (with known issues)" if known or new else ""),
            "line": enabled_i + 1, "excerpt": [scrub_value(lines[enabled_i][1])[:500]]}


def _error_heads(run: dict) -> list[tuple]:
    lines = _cut_shutdown(run["lines"])
    return [lines[a] for a, _b in _blocks(lines)]


def _in_baseline(baseline: list[dict], pat: re.Pattern) -> bool:
    return any(pat.search(ln) for r in baseline for _t, ln in r["lines"])


def _norm(v: str | None) -> str:
    return re.sub(r"[\s+]", "", v or "").lower()


def _pick(runs: list[dict], since: float | None) -> tuple[dict | None, list[dict]]:
    """The run to judge (first run started after `since`, else the latest) and its baseline runs."""
    if not runs:
        return None, []
    if since is not None:
        after = [r for r in runs if r["start"] and r["start"] >= since]
        if not after:
            return None, [r for r in runs if r["complete"]][-BASELINE_RUNS:]
        target = after[-1]
    else:
        target = runs[-1]
    idx = runs.index(target)
    baseline = [r for r in runs[:idx] if r["complete"] or r["lines"]][-BASELINE_RUNS:]
    return target, baseline


def plugin_health(srv: Server, runs: list[dict], plugin: dict, meta_desc: dict | None,
                  since: float | None = None, _ctx: dict | None = None) -> dict:
    ctx = _ctx or context(runs, since)
    target, baseline = ctx["target"], ctx["baseline"]
    empty = {"excerpt": [], "log": None, "preexisting_errors": [], "later_errors": [], "warnings": []}
    if target is None:
        return {**empty, "status": "unknown", "reason": "no server start in the logs since then"}
    if target["start"] is None:
        return {**empty, "status": "unknown", "reason": "the last server start is older than the available logs"}
    main = (meta_desc or {}).get("main")
    pkg = main.rsplit(".", 1)[0] if main and main.count(".") >= 2 else None
    m = _Matcher(plugin["name"], plugin["jar"], pkg, srv.family, (meta_desc or {}).get("id"))
    res = analyse_run(target, baseline, m, plugin.get("version"), ctx["players"], ctx["base_sigs"])
    return {**res, "log": target["file"], "run_started": target["start"], "baseline_runs": len(baseline)}


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
    for p in inventory.list_plugins(srv):
        desc = inventory.jar_meta(srv.plugins_dir / p["jar"])["descriptors"].get(srv.family)
        r = plugin_health(srv, runs, p, desc, since, _ctx=ctx)
        plugins.append({"key": p["key"], "name": p["name"], "jar": p["jar"], "version": p["version"], **r})
    order = {"failed": 0, "unknown": 1, "healthy": 2}
    plugins.sort(key=lambda r: (order[r["status"]], r["name"].lower()))
    known = []
    for p in plugins:
        for e in p.get("preexisting_errors") or []:
            known.append({"plugin": p["name"], **e})
    last_start = last_startup(srv) or None
    lines = target["lines"] if target else []
    return {"server": srv.id, "since": since, "checked_at": time.time(),
            "run_started": target["start"] if target else None,
            "logs": sorted({r["file"] for r in runs}),
            "baseline_runs": len(baseline),
            "startup_complete": any("Done (" in ln for _t, ln in lines),
            "restarted_at": last_start,
            "restarted": bool(last_start and (since is None or last_start >= since)),
            "counts": {s: sum(1 for r in plugins if r["status"] == s) for s in ("healthy", "failed", "unknown")},
            "preexisting_errors": known,
            "plugins": plugins}
