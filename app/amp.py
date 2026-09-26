"""CubeCoders AMP integration (optional): live status, power actions, console read + commands.

All calls go to the ADS controller (LGT_AMP_URL). Instance calls are proxied through it:
  POST {url}/API/ADSModule/Servers/{InstanceID}/API/{Module}/{Method}
Each target (ADS itself, and every instance through the proxy) needs its own Core/Login session. A
session that expired comes back as HTTP 401/403 or as an error object (Title "Unauthorized Access" /
StackTrace); we log in again once and retry.

Power actions use the instance-level Core/Start|Stop|Restart: they act on the Minecraft application
inside the AMP instance, which stays up (so status, console and the API keep working while the server
restarts). ADSModule/StartInstance etc. would stop the whole AMP instance process; we never use them.

Only Minecraft-module instances whose InstanceName matches a datastore server id are exposed.
Credentials are read from the environment only, never logged or returned; error texts are scrubbed.
"""
from __future__ import annotations

import fnmatch
import queue
import re
import threading
import time
from collections import deque
from typing import Any

import httpx

from . import config
from .redact import redact_lines, scrub_value

STATUS_TTL = 5.0
INSTANCES_TTL = 60.0
CONSOLE_BACKLOG = 200
CONSOLE_POLL = 1.0
CONSOLE_IDLE_STOP = 30.0
TIMEOUT = httpx.Timeout(10.0, connect=3.0)

STATES = {-1: "undefined", 0: "stopped", 5: "prestart", 7: "configuring", 10: "starting", 20: "running",
          30: "restarting", 40: "stopping", 45: "preparing_sleep", 50: "sleeping", 60: "waiting",
          70: "installing", 75: "updating", 80: "awaiting_input", 100: "failed", 200: "suspended",
          250: "maintenance", 999: "indeterminate"}
READY = 20

TRANSPORT: httpx.BaseTransport | None = None  # tests: mock_amp.mock_transport(...)
SLEEP = time.sleep  # tests make waiting instant


class AmpError(Exception):
    def __init__(self, message: str, status: int = 502):
        super().__init__(_scrub(message))
        self.status = status


class _Unauthorized(Exception):
    pass


def configured() -> bool:
    return bool(config.AMP_URL and config.AMP_USER and config.AMP_PASSWORD)


def _scrub(text: str) -> str:
    text = str(text)
    for secret in (config.AMP_PASSWORD, config.AMP_TOKEN):
        if secret:
            text = text.replace(secret, "«redacted»")
    return scrub_value(text)[:500]


class Client:
    """One set of sessions (ADS + per instance). Separate clients keep separate console cursors."""

    def __init__(self):
        self._lock = threading.RLock()
        self._sessions: dict[str | None, str] = {}
        self._http = httpx.Client(timeout=TIMEOUT, transport=TRANSPORT, follow_redirects=False,
                                  headers={"Accept": "application/json", "User-Agent": config.USER_AGENT})

    def close(self) -> None:
        self._http.close()

    def _post(self, path: str, body: dict) -> Any:
        url = f"{config.AMP_URL.rstrip('/')}/API/{path}"
        try:
            r = self._http.post(url, json=body)
        except httpx.HTTPError as e:
            raise AmpError(f"AMP unreachable: {type(e).__name__}", 503)
        if r.status_code in (401, 403):
            raise _Unauthorized()
        try:
            data = r.json() if r.content else None
        except ValueError:
            raise AmpError(f"AMP returned HTTP {r.status_code} (not JSON)")
        if isinstance(data, dict) and ("StackTrace" in data or data.get("Title") == "Unauthorized Access"):
            title, msg = str(data.get("Title", "")), str(data.get("Message", ""))
            if "Unauthori" in title or "session" in msg.lower() or "log in" in msg.lower():
                raise _Unauthorized()
            raise AmpError(f"AMP error: {title}: {msg}")
        if r.status_code >= 400:
            raise AmpError(f"AMP returned HTTP {r.status_code}")
        return data

    def _prefix(self, instance_id: str | None) -> str:
        if instance_id is None:
            return ""
        if not re.fullmatch(r"[0-9a-fA-F-]{36}", instance_id):
            raise AmpError("invalid instance id", 400)
        return f"ADSModule/Servers/{instance_id}/API/"

    def _login(self, instance_id: str | None) -> str:
        body = {"username": config.AMP_USER, "password": config.AMP_PASSWORD, "token": config.AMP_TOKEN or "",
                "rememberMe": False}
        try:
            data = self._post(self._prefix(instance_id) + "Core/Login", body)
        except _Unauthorized:
            raise AmpError("AMP login refused", 502)
        if not isinstance(data, dict) or not data.get("success") or not data.get("sessionID"):
            reason = (data or {}).get("resultReason", "") if isinstance(data, dict) else ""
            raise AmpError(f"AMP login failed{': ' + reason if reason else ''}", 502)
        return data["sessionID"]

    def call(self, method: str, params: dict | None = None, instance_id: str | None = None) -> Any:
        prefix = self._prefix(instance_id)
        for attempt in (0, 1):
            with self._lock:
                sid = self._sessions.get(instance_id)
                if sid is None:
                    sid = self._sessions[instance_id] = self._login(instance_id)
            try:
                return self._post(prefix + method, {**(params or {}), "SESSIONID": sid})
            except _Unauthorized:
                with self._lock:
                    if self._sessions.get(instance_id) == sid:
                        del self._sessions[instance_id]
        raise AmpError("AMP session could not be renewed", 502)


_client: Client | None = None
_client_lock = threading.Lock()


def client() -> Client:
    global _client
    with _client_lock:
        if _client is None:
            _client = Client()
        return _client


def reset() -> None:
    """Forget sessions and caches (tests, config changes)."""
    global _client
    with _client_lock:
        if _client:
            _client.close()
        _client = None
    _instances.update(at=0.0, map={})
    with _status_lock:
        _status.clear()
    CONSOLE.stop_all()


# ---------------------------------------------------------------- instances

_instances: dict[str, Any] = {"at": 0.0, "map": {}}
_inst_lock = threading.Lock()


def instances(force: bool = False) -> dict[str, dict]:
    """server id -> {instance_id, friendly, running} for Minecraft instances named like our servers."""
    from . import inventory
    with _inst_lock:
        if not force and time.time() - _instances["at"] < INSTANCES_TTL and _instances["map"]:
            return _instances["map"]
        targets = client().call("ADSModule/GetInstances", {"ForceIncludeSelf": False}) or []
        known = {s.id for s in inventory.discover()}
        out = {}
        for t in targets if isinstance(targets, list) else []:
            for i in t.get("AvailableInstances") or []:
                name = i.get("InstanceName")
                if i.get("Module") == "Minecraft" and name in known:
                    out[name] = {"instance_id": i["InstanceID"], "friendly": i.get("FriendlyName"),
                                 "running": bool(i.get("Running"))}
        _instances.update(at=time.time(), map=out)
        return out


def instance_id(server_id: str) -> str:
    inst = instances().get(server_id)
    if not inst:
        raise AmpError(f"{server_id} is not an AMP Minecraft instance", 404)
    return inst["instance_id"]


# ---------------------------------------------------------------- status (cached)

_status: dict[str, dict] = {}
_status_lock = threading.Lock()
_last_refresh = {"at": 0.0, "error": None}


def _metric(metrics: dict, name: str) -> dict:
    m = metrics.get(name) or {}
    return m if isinstance(m, dict) else {}


def _uptime_seconds(s: str | None) -> int | None:
    m = re.fullmatch(r"(?:(\d+)\.)?(\d+):(\d+):(\d+)(?:\.\d+)?", str(s or ""))
    if not m:
        return None
    d, h, mi, se = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + se


def parse_status(data: dict, users: dict | None) -> dict:
    metrics = (data or {}).get("Metrics") or {}
    cpu, mem, act = _metric(metrics, "CPU Usage"), _metric(metrics, "Memory Usage"), _metric(metrics, "Active Users")
    state = (data or {}).get("State")
    names = sorted(str(v) for v in (users or {}).values())[:200] if isinstance(users, dict) else []
    return {"state": STATES.get(state, "unknown"), "state_code": state,
            "players_online": act.get("RawValue"), "players_max": act.get("MaxValue"), "players": names,
            "cpu_percent": cpu.get("RawValue"), "memory_mb": mem.get("RawValue"), "memory_max_mb": mem.get("MaxValue"),
            "uptime": (data or {}).get("Uptime"), "uptime_seconds": _uptime_seconds((data or {}).get("Uptime"))}


def fetch_status(server_id: str, c: Client | None = None) -> dict:
    c = c or client()
    inst = instances().get(server_id)
    if not inst:
        raise AmpError(f"{server_id} is not an AMP Minecraft instance", 404)
    if not inst["running"]:
        return {"state": "instance_offline", "state_code": None, "players_online": None, "players_max": None,
                "players": [], "cpu_percent": None, "memory_mb": None, "memory_max_mb": None, "uptime": None,
                "uptime_seconds": None}
    data = c.call("Core/GetStatus", {}, inst["instance_id"])
    users = c.call("Core/GetUserList", {}, inst["instance_id"]) if (data or {}).get("State") == READY else {}
    return parse_status(data, users)


def refresh_all() -> None:
    """Poll every mapped instance once and update the cache (errors are cached per server)."""
    try:
        mapping = instances(force=time.time() - _instances["at"] > INSTANCES_TTL)
        _last_refresh["error"] = None
    except AmpError as e:
        _last_refresh.update(at=time.time(), error=str(e))
        return
    for sid in mapping:
        try:
            st = fetch_status(sid)
            st["error"] = None
        except AmpError as e:
            st = {"state": "unknown", "error": str(e)}
        st["checked_at"] = time.time()
        with _status_lock:
            _status[sid] = st
    _last_refresh["at"] = time.time()


def cached_status() -> dict:
    """What /overview may show: the cache only, never a network call."""
    with _status_lock:
        servers = {k: dict(v) for k, v in _status.items()}
    ok = configured() and bool(_last_refresh["at"]) and not _last_refresh["error"]
    return {"configured": configured(), "reachable": ok, "readonly": config.AMP_READONLY, "servers": servers,
            "updated_at": _last_refresh["at"] or None, "error": _last_refresh["error"]}


def status(max_age: float = STATUS_TTL) -> dict:
    if configured() and time.time() - _last_refresh["at"] > max_age:
        refresh_all()
    return cached_status()


def wait_state(c: Client, inst_id: str, wanted: set[int], timeout: float, poll: float = 2.0) -> dict:
    deadline = time.monotonic() + timeout
    last = {}
    while True:
        last = c.call("Core/GetStatus", {}, inst_id) or {}
        if last.get("State") in wanted:
            return last
        if time.monotonic() > deadline:
            raise AmpError(f"timed out waiting for {', '.join(STATES.get(w, str(w)) for w in wanted)} "
                           f"(now {STATES.get(last.get('State'), last.get('State'))})", 504)
        SLEEP(poll)


# ---------------------------------------------------------------- console

def redact_console(line: str) -> str:
    out, _found = redact_lines([line], "")
    return scrub_value(out[0] if out else "")[:2000]


def console_lines(updates: dict) -> list[str]:
    return [redact_console(str(e.get("Contents", ""))) for e in (updates or {}).get("ConsoleEntries") or []
            if isinstance(e, dict)]


class ConsoleHub:
    """One poller per instance while anyone is watching; fans lines out to subscribers."""

    def __init__(self):
        self._lock = threading.Lock()
        self._subs: dict[str, set[queue.Queue]] = {}
        self._backlog: dict[str, deque] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._stop = threading.Event()

    def subscribe(self, server_id: str) -> tuple[queue.Queue, list[str]]:
        inst_id = instance_id(server_id)
        q: queue.Queue = queue.Queue(maxsize=5000)
        with self._lock:
            self._subs.setdefault(server_id, set()).add(q)
            backlog = list(self._backlog.get(server_id, []))
            t = self._threads.get(server_id)
            if t is None or not t.is_alive():
                self._stop.clear()
                t = threading.Thread(target=self._poll, args=(server_id, inst_id), daemon=True,
                                     name=f"amp-console-{server_id}")
                self._threads[server_id] = t
                t.start()
        return q, backlog

    def unsubscribe(self, server_id: str, q: queue.Queue) -> None:
        with self._lock:
            self._subs.get(server_id, set()).discard(q)

    def watchers(self, server_id: str) -> int:
        with self._lock:
            return len(self._subs.get(server_id, ()))

    def stop_all(self) -> None:
        self._stop.set()

    def _poll(self, server_id: str, inst_id: str) -> None:
        c = Client()  # own sessions: GetUpdates cursors are per session
        idle_since = None
        try:
            while not self._stop.is_set():
                with self._lock:
                    subs = list(self._subs.get(server_id, ()))
                if not subs:
                    idle_since = idle_since or time.monotonic()
                    if time.monotonic() - idle_since > CONSOLE_IDLE_STOP:
                        return
                else:
                    idle_since = None
                try:
                    lines = console_lines(c.call("Core/GetUpdates", {}, inst_id))
                except AmpError as e:
                    lines = [f"[PlugWarden] console unavailable: {e}"]
                    SLEEP(5)
                if lines:
                    with self._lock:
                        buf = self._backlog.setdefault(server_id, deque(maxlen=CONSOLE_BACKLOG))
                        buf.extend(lines)
                        subs = list(self._subs.get(server_id, ()))
                    for q in subs:
                        for ln in lines:
                            try:
                                q.put_nowait(ln)
                            except queue.Full:
                                break
                SLEEP(CONSOLE_POLL)
        finally:
            c.close()
            with self._lock:
                self._threads.pop(server_id, None)


CONSOLE = ConsoleHub()


# ---------------------------------------------------------------- background status poller

POLL_EVERY = 10.0
_poller_stop = threading.Event()


def start_poller() -> None:
    """Keeps the status cache fresh so /overview never waits on AMP."""
    if not configured():
        return
    _poller_stop.clear()

    def loop():
        while not _poller_stop.is_set():
            try:
                refresh_all()
            except Exception:  # noqa: BLE001 - never let the poller die
                pass
            _poller_stop.wait(POLL_EVERY)
    threading.Thread(target=loop, name="amp-status", daemon=True).start()


def stop_poller() -> None:
    _poller_stop.set()


# ---------------------------------------------------------------- commands

class CommandRefused(AmpError):
    def __init__(self, message: str, code: str, matched: str | None = None):
        super().__init__(message, 409 if code == "confirm_required" else 400)
        self.code, self.matched = code, matched


def check_command(command: Any, denylist: list[str], confirm: bool) -> str:
    if not isinstance(command, str):
        raise CommandRefused("command must be a string", "invalid")
    cmd = command.strip()
    if not cmd:
        raise CommandRefused("command is empty", "invalid")
    if len(cmd) > 256 or any(ch in cmd for ch in "\r\n\x00"):
        raise CommandRefused("command is too long or contains line breaks", "invalid")
    norm = re.sub(r"\s+", " ", cmd.lstrip("/")).lower()
    for pat in denylist:
        p = re.sub(r"\s+", " ", pat.strip().lstrip("/")).lower()
        if p and (fnmatch.fnmatchcase(norm, p) or norm == p):
            if not confirm:
                raise CommandRefused(f"'{cmd}' matches the protected command '{pat}'; send confirm: true to run it",
                                     "confirm_required", pat)
            break
    return cmd


def send_command(server_id: str, command: str, c: Client | None = None) -> None:
    require_writable()
    (c or client()).call("Core/SendConsoleMessage", {"message": command}, instance_id(server_id))


def require_writable() -> None:
    if not configured():
        raise AmpError("AMP integration is not configured", 503)
    if config.AMP_READONLY:
        raise AmpError("AMP is read-only in this environment (LGT_AMP_READONLY=1)", 403)
