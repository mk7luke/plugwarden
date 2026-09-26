"""Mock AMP ADS controller for development and tests. NEVER a stand-in for production.

Implements the subset of the AMP API PlugWarden uses, with realistic payloads:
  POST /API/Core/Login, /API/ADSModule/GetInstances, and per instance through the ADS proxy
  /API/ADSModule/Servers/{InstanceID}/API/Core/{Login,GetStatus,GetUserList,GetUpdates,
  SendConsoleMessage,Start,Stop,Restart}.
State changes advance per status poll (deterministic, fast), and the fake console emits Paper startup
lines. With --log-root (a sandbox base dir) a (re)start also rotates and writes
<root>/<instance>/Minecraft/logs/latest.log, so PlugWarden's log health check sees real-looking runs.

Run: .venv/bin/python dev/mock_amp.py --port 18100 --log-root <sandbox>/base
"""
from __future__ import annotations

import gzip
import itertools
import os
import re
import secrets
import threading
import time
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request  # module level: route annotations must resolve (future annotations)
from fastapi.responses import JSONResponse

USER, PASSWORD = "admin", "mock-password"

# Mirrors `ampinstmgr -t` on the real host (names, modules, which instances are up).
DEFAULT_INSTANCES = [
    ("ADS01", "ADS01", "ADS", True), ("elChapo01", "2-survival", "Minecraft", True),
    ("Enshrouded01", "Enshrouded", "GenericModule", False), ("M0-proxy01", "0-proxy", "Minecraft", True),
    ("M1-hub01", "1-hub", "Minecraft", True), ("M3-hunger01", "3-hunger", "Minecraft", True),
    ("M4-skyblock01", "4-skyblock", "Minecraft", True), ("M5-kitpvp01", "5-kitpvp", "Minecraft", True),
    ("M6-creative01", "6-creative", "Minecraft", True), ("M7-bending01", "7-bending", "Minecraft", True),
    ("M8-lifesteal01", "8-lifesteal", "Minecraft", True), ("M9-aerons-server01", "9-aerons-server", "Minecraft", False),
    ("M9-homestead01", "9-homestead", "Minecraft", True), ("sonsforest101", "sonsforest1", "GenericModule", False),
    ("subnautica101", "subnautica1", "GenericModule", False),
]
READY, STOPPED, STARTING, RESTARTING, STOPPING = 20, 0, 10, 30, 40


def _ms() -> str:
    return f"/Date({int(time.time() * 1000)})/"


class Instance:
    def __init__(self, name: str, friendly: str, module: str, up: bool, port: int):
        self.name, self.friendly, self.module, self.up, self.port = name, friendly, module, up, port
        self.id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"mock-amp/{name}"))
        self.state = READY if up and module == "Minecraft" else STOPPED
        self.started = time.time() - 3600
        self.players = {"8667ba71-b85a-4004-af54-457a9734eed7": "PlayerOne"} if name == "elChapo01" else {}
        self.console: list[dict] = []
        self.cursors: dict[str, int] = {}
        self.pending: list[int] = []  # states still to pass through on the next status polls
        self.fail_plugin: str | None = None
        self.disable_after_done: str | None = None  # plugin that shuts itself down right after "Done ("
        self.commands: list[str] = []

    def say(self, text: str, source: str = "INFO") -> None:
        # Real AMP 2.x: message without the log prefix in Contents, level in Source, ISO UTC Timestamp.
        m = re.match(r"^\[[^\]]*?(INFO|WARN|ERROR|SEVERE)\]:\s*(.*)$", text) or \
            re.match(r"^\[[^\]]*\]\s*\[[^\]]*/(INFO|WARN|ERROR|SEVERE)\]:\s*(.*)$", text)
        if m:
            source, text = m.group(1), m.group(2)
        self.console.append({"Timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                             "Source": source, "SourceId": "", "Type": "Console", "Contents": text})
        del self.console[:-2000]


class MockADS:
    def __init__(self, log_root: str | None = None, instances=DEFAULT_INSTANCES):
        self.log_root = Path(log_root) if log_root else None
        self.sessions: dict[str, str | None] = {}  # session id -> instance id (None = ADS)
        self.instances = {}
        for port, (name, friendly, module, up) in zip(itertools.count(8080), instances):
            inst = Instance(name, friendly, module, up, port)
            self.instances[inst.id] = inst
        self.lock = threading.Lock()
        self.calls: list[str] = []  # every method called, for tests

    # -------------------------------------------------------------- dispatch
    def handle(self, path: str, body: dict) -> tuple[int, object]:
        with self.lock:
            path = path.strip("/")
            if path.startswith("API/"):
                path = path[4:]
            m = re.match(r"^ADSModule/Servers/([0-9a-f-]{36})/API/(.+)$", path)
            inst = None
            if m:
                inst = self.instances.get(m.group(1))
                if inst is None or not inst.up:
                    return 502, {"Title": "Instance unavailable", "Message": "The instance is offline",
                                 "StackTrace": ""}
                path = m.group(2)
            self.calls.append(("" if inst is None else inst.name + ":") + path)
            if path == "Core/Login":
                return 200, self._login(body, inst)
            sid = body.get("SESSIONID")
            if sid not in self.sessions or self.sessions[sid] != (inst.id if inst else None):
                # AMP answers HTTP 200 with an error object for a stale session
                return 200, {"Title": "Unauthorized Access", "Message": "Your session has expired.",
                             "StackTrace": "   at AMP..."}
            if inst is None:
                if path == "ADSModule/GetInstances":
                    return 200, self._get_instances()
                if path == "Core/GetModuleInfo":
                    return 200, {"Name": "Application Deployment Server", "AppName": "Application Deployment"}
                return 404, {"Title": "Not found", "Message": path, "StackTrace": ""}
            return 200, self._instance_call(inst, path, body)

    def _login(self, body: dict, inst: Instance | None) -> dict:
        if body.get("username") != USER or body.get("password") != PASSWORD:
            return {"success": False, "resultReason": "Invalid username or password", "sessionID": ""}
        sid = secrets.token_hex(16)
        self.sessions[sid] = inst.id if inst else None
        return {"success": True, "sessionID": sid, "rememberMeToken": "", "resultReason": "",
                "permissions": ["*"], "userInfo": {"Username": USER}}

    def _get_instances(self) -> list:
        avail = []
        for i in self.instances.values():
            avail.append({"InstanceID": i.id, "InstanceName": i.name, "FriendlyName": i.friendly,
                          "Module": i.module, "ModuleDisplayName": i.module, "Running": i.up,
                          "AppState": i.state, "Port": i.port, "IP": "0.0.0.0", "Suspended": False,
                          "Metrics": {}})
        return [{"Id": 1, "InstanceId": str(uuid.uuid5(uuid.NAMESPACE_DNS, "mock-ads")), "FriendlyName": "Local",
                 "Description": "", "Disabled": False, "AvailableInstances": avail}]

    # -------------------------------------------------------------- instance API
    def _instance_call(self, inst: Instance, path: str, body: dict):
        if path == "Core/GetStatus":
            return self._status(inst)
        if path == "Core/GetUserList":
            return dict(inst.players) if inst.state == READY else {}
        if path == "Core/GetUpdates":
            sid = body["SESSIONID"]
            start = inst.cursors.get(sid, max(0, len(inst.console) - 50))
            inst.cursors[sid] = len(inst.console)
            return {"Status": self._status(inst), "ConsoleEntries": inst.console[start:], "Messages": [],
                    "Tasks": [], "Ports": []}
        if path == "Core/SendConsoleMessage":
            msg = str(body.get("message", ""))
            inst.commands.append(msg)
            inst.say(f"[{datetime.now():%H:%M:%S} INFO]: {msg}" if not msg.startswith("say ")
                     else f"[{datetime.now():%H:%M:%S} INFO]: [Server] {msg[4:]}")
            return None
        if path == "Core/Restart":
            inst.pending = [STOPPING, STOPPED, STARTING, STARTING, READY]
            inst.state = RESTARTING
            inst.say(f"[{datetime.now():%H:%M:%S} INFO]: Stopping server")
            return {"Status": True, "Reason": ""}
        if path == "Core/Stop":
            inst.pending = [STOPPED]
            inst.state = STOPPING
            inst.say(f"[{datetime.now():%H:%M:%S} INFO]: Stopping server")
            return None
        if path == "Core/Start":
            if inst.state == READY:
                return {"Status": False, "Reason": "The application is already running"}
            inst.pending = [STARTING, STARTING, READY]
            inst.state = STARTING
            return {"Status": True, "Reason": ""}
        return {"Title": "Not implemented in mock", "Message": path, "StackTrace": ""}

    def _status(self, inst: Instance) -> dict:
        if inst.pending:
            new = inst.pending.pop(0)
            if new == STARTING and inst.state != STARTING:
                self._boot(inst)
            inst.state = new
            if new == READY:
                inst.started = time.time()
        up = int(time.time() - inst.started) if inst.state == READY else 0
        players = len(inst.players) if inst.state == READY else 0
        return {"State": inst.state, "Uptime": f"{up // 86400}.{up % 86400 // 3600:02d}:{up % 3600 // 60:02d}:{up % 60:02d}",
                "Metrics": {"CPU Usage": {"RawValue": 7 if inst.state == READY else 0, "MaxValue": 100, "Percent": 7,
                                          "Units": "%"},
                            "Memory Usage": {"RawValue": 3120, "MaxValue": 8192, "Percent": 38, "Units": "MB"},
                            "Active Users": {"RawValue": players, "MaxValue": 50, "Percent": players * 2,
                                             "Units": ""}}}

    # -------------------------------------------------------------- fake server start
    def _plugins(self, inst: Instance) -> list[tuple[str, str]]:
        if not self.log_root:
            return [("CoreProtect", "23.4"), ("LuckPerms", "5.5.21")]
        out = []
        pdir = self.log_root / inst.name / "Minecraft" / "plugins"
        for jar in sorted(pdir.glob("*.jar")) if pdir.is_dir() else []:
            try:
                with zipfile.ZipFile(jar) as z:
                    text = z.read("plugin.yml").decode("utf-8", "replace")
            except Exception:  # noqa: BLE001 - mock: skip anything odd
                continue
            name = re.search(r"^name:\s*['\"]?([^'\"\n]+)", text, re.M)
            ver = re.search(r"^version:\s*['\"]?([^'\"\n]+)", text, re.M)
            if name:
                out.append((name.group(1).strip(), ver.group(1).strip() if ver else "1.0"))
        return out

    def _boot(self, inst: Instance) -> None:
        t = datetime.now().strftime("%H:%M:%S")
        lines = [f"[{t}] [ServerMain/INFO]: [bootstrap] Running Java 21 (OpenJDK 64-Bit Server VM) on Linux",
                 f"[{t}] [Server thread/INFO]: Starting minecraft server version 1.21.6"]
        for name, ver in self._plugins(inst):
            lines.append(f"[{t}] [Server thread/INFO]: [{name}] Enabling {name} v{ver}")
            if inst.fail_plugin and name == inst.fail_plugin:
                lines.append(f"[{t}] [Server thread/ERROR]: Error occurred while enabling {name} v{ver} "
                             "(Is it up to date?)")
                lines.append("java.lang.NoSuchMethodError: 'void org.bukkit.Foo.bar()'")
        lines.append(f'[{t}] [Server thread/INFO]: Done (12.345s)! For help, type "help"')
        if inst.disable_after_done:
            name = inst.disable_after_done
            ver = next((v for n, v in self._plugins(inst) if n == name), "1.0")
            lines.append(f"[{t}] [Server thread/ERROR]: [{name}] Disabling {name} after a fatal error")
            lines.append(f"[{t}] [Server thread/INFO]: [{name}] Disabling {name} v{ver}")
        for ln in lines:
            inst.say(ln)
        if self.log_root and (self.log_root / inst.name / "Minecraft").is_dir():
            self._write_log(inst, lines)

    def _write_log(self, inst: Instance, lines: list[str]) -> None:
        logs = self.log_root / inst.name / "Minecraft" / "logs"
        logs.mkdir(exist_ok=True)
        latest = logs / "latest.log"
        if latest.exists():  # Log4j-style rotation on start
            day = datetime.fromtimestamp(latest.stat().st_mtime).strftime("%Y-%m-%d")
            n = 1
            while (logs / f"{day}-{n}.log.gz").exists():
                n += 1
            with open(latest, "rb") as src, gzip.open(logs / f"{day}-{n}.log.gz", "wb") as dst:
                dst.write(src.read())
        latest.write_text("\n".join(lines) + "\n")

    def by_name(self, name: str) -> Instance:
        return next(i for i in self.instances.values() if i.name == name)


def create_app(mock: MockADS):
    app = FastAPI(title="Mock AMP ADS")

    @app.post("/API/{path:path}")
    async def api(path: str, request: Request):
        try:
            body = await request.json()
        except ValueError:
            body = {}
        status, data = mock.handle("API/" + path, body if isinstance(body, dict) else {})
        return JSONResponse(data, status_code=status)

    return app


def mock_transport(mock: MockADS):
    """httpx transport that answers from the mock (tests)."""
    import json

    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        status, data = mock.handle(request.url.path, body)
        return httpx.Response(status, json=data)

    return httpx.MockTransport(handler)


if __name__ == "__main__":
    import argparse

    import uvicorn

    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=18100)
    ap.add_argument("--log-root", default=os.environ.get("LGT_BASE"))
    a = ap.parse_args()
    uvicorn.run(create_app(MockADS(a.log_root)), host="127.0.0.1", port=a.port, log_level="warning")
