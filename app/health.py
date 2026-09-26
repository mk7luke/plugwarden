"""Plugin health from server logs: did a plugin enable cleanly after a restart?

Reads the server's Minecraft/logs/latest.log (tail) and rotated *.log.gz written since a point in time,
bounded to 20 MB in total, confined to the server's directory. Per plugin the newest run that mentions it
decides: healthy (it logged "Enabling <Name> v<version>" and nothing below matched), failed (it failed to
load/enable, was disabled during startup, or an ERROR/SEVERE block references it), or unknown (no log of
that version yet). Excerpts are secret-scrubbed.
"""
from __future__ import annotations

import gzip
import re
import time
from pathlib import Path

from . import inventory
from .inventory import Server
from .redact import scrub_value

MAX_LOG_BYTES = 20 * 1024 * 1024
EXCERPT_BEFORE, EXCERPT_AFTER = 2, 12
_LEVEL = re.compile(r"^\[[^\]]*?\b(ERROR|SEVERE)\]|^\[[^\]]*\]\s*\[[^\]]*/(ERROR|SEVERE)\]")
_NEW_ENTRY = re.compile(r"^\[\d{1,2}:\d{2}:\d{2}")


def _logs_dir(srv: Server) -> Path | None:
    d = srv.root / "Minecraft" / "logs"
    try:
        real = d.resolve()
        root = srv.root.resolve()
    except (OSError, RuntimeError):
        return None
    if d.is_symlink() or not real.is_dir() or not (real == root or root in real.parents):
        return None
    return real


def read_runs(srv: Server, since: float | None) -> list[dict]:
    """Log runs (oldest → newest) as [{"file", "mtime", "lines"}], at most MAX_LOG_BYTES read in total.
    Without `since`, only the current run (latest.log)."""
    d = _logs_dir(srv)
    if d is None:
        return []
    files = []
    latest = d / "latest.log"
    if latest.is_file() and not latest.is_symlink():
        files.append(latest)
    if since is not None:
        gz = [p for p in d.glob("*.log.gz") if p.is_file() and not p.is_symlink() and p.stat().st_mtime >= since]
        files += sorted(gz, key=lambda p: p.stat().st_mtime, reverse=True)
    budget = MAX_LOG_BYTES
    runs = []
    for p in files:  # newest first, so the budget favours the most recent runs
        if budget <= 0:
            break
        try:
            if p.suffix == ".gz":
                with gzip.open(p, "rb") as f:
                    data = f.read(budget)
            else:
                size = p.stat().st_size
                with open(p, "rb") as f:
                    f.seek(max(0, size - budget))
                    data = f.read(budget)
        except (OSError, EOFError, gzip.BadGzipFile):
            continue
        budget -= len(data)
        runs.append({"file": p.name, "mtime": p.stat().st_mtime,
                     "lines": data.decode("utf-8", errors="replace").splitlines()})
    return list(reversed(runs))


def _excerpt(lines: list[str], i: int) -> list[str]:
    return [scrub_value(ln)[:500] for ln in lines[max(0, i - EXCERPT_BEFORE):i + EXCERPT_AFTER]]


def _cut_shutdown(lines: list[str]) -> list[str]:
    for i, ln in enumerate(lines):
        if "Stopping server" in ln or "Stopping the server" in ln:
            return lines[:i]
    return lines


def evaluate(lines: list[str], name: str, version: str | None, jar: str, main_pkg: str | None,
             family: str = "bukkit", plugin_id: str | None = None) -> dict | None:
    """Health of one plugin in one run, or None if the run never mentions it."""
    lines = _cut_shutdown(lines)
    esc = re.escape(name)
    if family == "velocity":
        pid = re.escape(plugin_id or name.lower())
        enabled_re = re.compile(rf"Loaded plugin {pid} (\S+)", re.I)
        fail_res = [re.compile(rf"(Can't create|Couldn't (load|enable)|Unable to load) plugin {pid}\b", re.I)]
    else:
        enabled_re = re.compile(rf"Enabling {esc} v(\S+)")
        fail_res = [re.compile(rf"Error occurred while enabling {esc}\b"),
                    re.compile(rf"Could not load '?plugins/{re.escape(jar)}'?"),
                    re.compile(rf"Could not load plugin '?{re.escape(jar)}'?"),
                    re.compile(rf"(Error loading plugin|Failed to load plugin)[^\n]*{re.escape(jar)}"),
                    re.compile(rf"(UnknownDependencyException|InvalidPluginException|InvalidDescriptionException)"
                               rf"[^\n]*({esc}|{re.escape(jar)})")]
    tag = f"[{name}]"
    enabled_version = None
    enabled_idx = None
    done_idx = next((i for i, ln in enumerate(lines) if "Done (" in ln), None)
    mentioned = False
    for i, ln in enumerate(lines):
        m = enabled_re.search(ln)
        if m:
            mentioned, enabled_version, enabled_idx = True, m.group(1), i
        for fr in fail_res:
            if fr.search(ln):
                return {"status": "failed", "reason": "failed to load or enable", "line": i + 1,
                        "excerpt": _excerpt(lines, i)}
        if family != "velocity" and re.search(rf"Disabling {esc} v", ln) and (done_idx is None or i < done_idx):
            return {"status": "failed", "reason": "disabled during startup", "line": i + 1,
                    "excerpt": _excerpt(lines, i)}
    # ERROR/SEVERE blocks (message + stack trace) that reference the plugin
    i = 0
    while i < len(lines):
        if _LEVEL.search(lines[i]):
            j = i + 1
            while j < len(lines) and not _NEW_ENTRY.match(lines[j]):
                j += 1
            head, block = lines[i], "\n".join(lines[i:j])
            if tag in head or re.search(rf"\b{esc}\b", head) or (main_pkg and main_pkg in block):
                return {"status": "failed", "reason": "error logged by the plugin", "line": i + 1,
                        "excerpt": _excerpt(lines, i)}
            i = j
        else:
            i += 1
    if not mentioned:
        return None
    if version and enabled_version and _norm(enabled_version) != _norm(version):
        return {"status": "unknown", "reason": f"the server ran v{enabled_version}, not v{version}",
                "line": enabled_idx + 1, "excerpt": []}
    return {"status": "healthy", "reason": "enabled", "line": enabled_idx + 1,
            "excerpt": [scrub_value(lines[enabled_idx])[:500]]}


def _norm(v: str) -> str:
    return re.sub(r"[\s+]", "", v).lower()


def plugin_health(srv: Server, runs: list[dict], plugin: dict, meta_desc: dict | None) -> dict:
    """Newest run that mentions the plugin decides."""
    main = (meta_desc or {}).get("main")
    pkg = main.rsplit(".", 1)[0] if main and main.count(".") >= 2 else None
    for run in reversed(runs):
        res = evaluate(run["lines"], plugin["name"], plugin["version"], plugin["jar"], pkg, srv.family,
                       (meta_desc or {}).get("id"))
        if res:
            return {**res, "log": run["file"]}
    return {"status": "unknown", "reason": "no log of this plugin since then", "excerpt": [], "log": None}


def server_report(srv: Server, since: float | None) -> dict:
    runs = read_runs(srv, since)
    out = []
    for p in inventory.list_plugins(srv):
        desc = inventory.jar_meta(srv.plugins_dir / p["jar"])["descriptors"].get(srv.family)
        out.append({"key": p["key"], "name": p["name"], "jar": p["jar"], "version": p["version"],
                    **plugin_health(srv, runs, p, desc)})
    order = {"failed": 0, "unknown": 1, "healthy": 2}
    out.sort(key=lambda r: (order[r["status"]], r["name"].lower()))
    started = any("Done (" in ln for r in runs[-1:] for ln in r["lines"])
    from .actions import _last_start  # newest rotated log = last server start
    last_start = _last_start(srv) or None
    return {"server": srv.id, "since": since, "checked_at": time.time(), "logs": [r["file"] for r in runs],
            "startup_complete": started, "restarted_at": last_start,
            "restarted": bool(last_start and (since is None or last_start >= since)),
            "counts": {s: sum(1 for r in out if r["status"] == s) for s in ("healthy", "failed", "unknown")},
            "plugins": out}
