"""Lightweight read-access log (JSON lines in STATE_DIR/access.log), e.g. who viewed which config diff."""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone

from . import config

MAX_BYTES = 5 * 1024 * 1024  # rotate to access.log.1 beyond this
_lock = threading.Lock()


def _path():
    return config.state("access.log")


def record(user: str, action: str, servers: list[str], path: str, detail: str = "") -> None:
    entry = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "user": user, "action": action,
             "servers": servers, "path": path, "detail": detail}
    p = _path()
    with _lock:
        try:
            if p.stat().st_size > MAX_BYTES:
                os.replace(p, p.with_name("access.log.1"))
        except FileNotFoundError:
            pass
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")


def read(limit: int = 200, user: str | None = None, server: str | None = None, path: str | None = None,
         action: str | None = None) -> list[dict]:
    lines: list[str] = []
    for p in (_path().with_name("access.log.1"), _path()):
        try:
            with open(p, encoding="utf-8") as f:
                lines += f.readlines()
        except FileNotFoundError:
            pass
    out = []
    for ln in reversed(lines):
        try:
            e = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if user and user.lower() not in e["user"].lower():
            continue
        if server and not any(server.lower() in s.lower() for s in e["servers"]):
            continue
        if path and path.lower() not in e["path"].lower():
            continue
        if action and action != e["action"]:
            continue
        out.append(e)
        if len(out) >= limit:
            break
    return out
