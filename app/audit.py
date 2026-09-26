"""Append-only audit log (JSON lines in STATE_DIR/access.log): who read config values (diffs, plan
warnings) and who changed settings, pins, ignores, source mappings or uploaded jars (with before/after)."""
from __future__ import annotations

import json
import os
import secrets
import threading
import time
from datetime import datetime, timezone

from . import config

MAX_BYTES = 5 * 1024 * 1024  # rotate to access.log.1 beyond this
# Repeated identical reads (every plan recompute re-shows the same values) collapse into one row with a count.
COLLAPSIBLE = {"diff", "plan-values"}
COLLAPSE_WINDOW = 600  # seconds
_lock = threading.Lock()
_recent: dict[tuple, tuple[str, float]] = {}  # (user, action, servers, path) -> (entry id, first time)


def _path():
    return config.state("access.log")


def record(user: str, action: str, servers: list[str], path: str, detail: str = "",
           before=None, after=None) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    entry = {"id": secrets.token_hex(6), "at": now, "user": user, "action": action,
             "servers": servers, "path": path, "detail": detail}
    if before is not None or after is not None:
        entry["before"], entry["after"] = before, after
    p = _path()
    with _lock:
        if action in COLLAPSIBLE:
            k = (user, action, tuple(servers), path)
            prev = _recent.get(k)
            if prev and time.time() - prev[1] < COLLAPSE_WINDOW:
                # Append-only: a small "seen again" line that read() folds into the original row.
                entry = {"again": prev[0], "at": now, "detail": detail}
            else:
                _recent[k] = (entry["id"], time.time())
                for old in [x for x, (_i, t) in _recent.items() if time.time() - t >= COLLAPSE_WINDOW]:
                    _recent.pop(old, None)
        try:
            if p.stat().st_size > MAX_BYTES:
                os.replace(p, p.with_name("access.log.1"))
        except FileNotFoundError:
            pass
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")


READ_ACTIONS = {"diff", "plan-values"}  # someone looked at values; everything else changed something


def page(limit: int = 50, before: str | None = None, changes_only: bool = False, **filters) -> dict:
    """Newest first, `limit` rows older than the `before` cursor (from a previous page's next_before)."""
    rows = read(limit=None, **filters)
    if changes_only:
        rows = [e for e in rows if e["action"] not in READ_ACTIONS]
    if before:
        at, _, eid = before.partition("|")
        rows = [e for e in rows if (e["last_seen"], e.get("id") or "") < (at, eid)]
    out = rows[:limit]
    more = len(rows) > limit
    return {"entries": out, "has_more": more,
            "next_before": f"{out[-1]['last_seen']}|{out[-1].get('id') or ''}" if more and out else None}


def read(limit: int | None = 200, user: str | None = None, server: str | None = None, path: str | None = None,
         action: str | None = None) -> list[dict]:
    lines: list[str] = []
    for p in (_path().with_name("access.log.1"), _path()):
        try:
            with open(p, encoding="utf-8") as f:
                lines += f.readlines()
        except FileNotFoundError:
            pass
    rows, again = [], {}
    for ln in lines:
        try:
            e = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if "again" in e:
            a = again.setdefault(e["again"], {"count": 0})
            a["count"] += 1
            a["last_seen"], a["detail"] = e["at"], e.get("detail") or a.get("detail")
            continue
        rows.append(e)
    out = []
    for e in reversed(rows):
        extra = again.get(e.get("id"), {})
        e["count"] = 1 + extra.get("count", 0)
        e["last_seen"] = extra.get("last_seen", e["at"])
        if extra.get("detail"):
            e["detail"] = extra["detail"]
        if user and user.lower() not in e["user"].lower():
            continue
        if server and not any(server.lower() in s.lower() for s in e["servers"]):
            continue
        if path and path.lower() not in (e.get("path") or "").lower():
            continue
        if action and action != e["action"]:
            continue
        out.append(e)
    # a collapsed row moves up when seen again; the id breaks ties so paging cursors are stable
    out.sort(key=lambda e: (e["last_seen"], e.get("id") or ""), reverse=True)
    return out if limit is None else out[:limit]
